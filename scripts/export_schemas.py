from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from driftproof.cli import _schema_catalog as driftproof_schema_catalog
from mergeproof.cli import _schema_catalog as mergeproof_schema_catalog
from mergeproof.utils import atomic_write_text, pretty_json, sha256_text

ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_BUILDERS: dict[str, Callable[[], dict[str, dict[str, Any]]]] = {
    "driftproof": driftproof_schema_catalog,
    "mergeproof": mergeproof_schema_catalog,
}


class SchemaExportError(RuntimeError):
    """Raised when committed schemas differ from executable runtime models."""


def _filename(name: str) -> str:
    return f"{name.replace('_', '-')}.schema.json"


def expected_exports() -> dict[Path, str]:
    exports: dict[Path, str] = {}
    for tool, build_catalog in sorted(_SCHEMA_BUILDERS.items()):
        catalog = build_catalog()
        records: list[dict[str, object]] = []
        for name, schema in sorted(catalog.items()):
            relative = Path(tool) / _filename(name)
            payload = pretty_json(schema) + "\n"
            exports[relative] = payload
            records.append(
                {
                    "name": name,
                    "file": relative.name,
                    "sha256": sha256_text(payload),
                }
            )
        index = {
            "schema_version": 1,
            "tool": tool,
            "source": f"{tool}.cli._schema_catalog",
            "schemas": records,
        }
        exports[Path(tool) / "index.json"] = pretty_json(index) + "\n"
    return exports


def export_schemas(destination: Path) -> dict[str, object]:
    destination = destination.resolve()
    expected = expected_exports()
    destination.mkdir(parents=True, exist_ok=True)
    for relative, payload in expected.items():
        atomic_write_text(destination / relative, payload)
    manifest = {
        "schema_version": 1,
        "root": "schemas",
        "files": [
            {
                "path": relative.as_posix(),
                "bytes": len(payload.encode("utf-8")),
                "sha256": sha256_text(payload),
            }
            for relative, payload in sorted(expected.items())
        ],
    }
    atomic_write_text(destination / "manifest.json", pretty_json(manifest) + "\n")
    return manifest


def check_schemas(destination: Path) -> dict[str, object]:
    destination = destination.resolve()
    expected = expected_exports()
    expected_with_manifest = set(expected) | {Path("manifest.json")}
    if destination.is_symlink() or not destination.is_dir():
        raise SchemaExportError("schema destination is missing or unsafe")
    unsafe = sorted(
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_symlink()
    )
    if unsafe:
        raise SchemaExportError(f"schema tree contains symlinks: {unsafe}")
    observed = {path.relative_to(destination) for path in destination.rglob("*") if path.is_file()}

    missing = sorted(path.as_posix() for path in set(expected) - observed)
    unexpected = sorted(path.as_posix() for path in observed - expected_with_manifest)
    changed = sorted(
        relative.as_posix()
        for relative, payload in expected.items()
        if relative in observed and (destination / relative).read_text(encoding="utf-8") != payload
    )
    if missing or unexpected or changed:
        raise SchemaExportError(
            json.dumps(
                {
                    "missing": missing,
                    "unexpected": unexpected,
                    "changed": changed,
                },
                sort_keys=True,
            )
        )

    manifest_path = destination / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SchemaExportError("schema manifest is missing or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_manifest = {
        "schema_version": 1,
        "root": "schemas",
        "files": [
            {
                "path": relative.as_posix(),
                "bytes": len(payload.encode("utf-8")),
                "sha256": sha256_text(payload),
            }
            for relative, payload in sorted(expected.items())
        ],
    }
    if manifest != expected_manifest:
        raise SchemaExportError("schema manifest differs from executable schemas")
    return {
        "schema_version": 1,
        "verified": True,
        "destination": str(destination),
        "files": len(expected),
        "manifest_sha256": sha256_text(pretty_json(manifest) + "\n"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export or verify deterministic JSON Schemas from runtime models."
    )
    parser.add_argument("--destination", type=Path, default=ROOT / "schemas")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = check_schemas(args.destination) if args.check else export_schemas(args.destination)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
