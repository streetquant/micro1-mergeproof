from __future__ import annotations

from pathlib import Path

import pytest

from scripts.export_schemas import SchemaExportError, check_schemas, export_schemas


def file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_schema_exports_are_clone_independent_and_self_verifying(tmp_path: Path) -> None:
    first = tmp_path / "first" / "schemas"
    second = tmp_path / "second" / "schemas"

    export_schemas(first)
    export_schemas(second)

    assert check_schemas(first)["verified"] is True
    assert check_schemas(second)["verified"] is True
    exported = file_map(first)
    assert exported == file_map(second)
    assert "manifest.json" in exported
    assert "driftproof/onboarding-response.schema.json" in exported
    assert "driftproof/index.json" in exported
    assert "mergeproof/index.json" in exported


def test_schema_check_detects_content_drift(tmp_path: Path) -> None:
    destination = tmp_path / "schemas"
    export_schemas(destination)
    request = destination / "driftproof" / "request.schema.json"
    request.write_text("{}\n", encoding="utf-8")

    with pytest.raises(SchemaExportError, match="changed"):
        check_schemas(destination)


def test_schema_check_rejects_unexpected_files_and_symlinks(tmp_path: Path) -> None:
    destination = tmp_path / "schemas"
    export_schemas(destination)
    (destination / "unexpected.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SchemaExportError, match="unexpected"):
        check_schemas(destination)

    (destination / "unexpected.json").unlink()
    target = destination / "driftproof" / "request.schema.json"
    link = destination / "driftproof" / "linked.schema.json"
    link.symlink_to(target)

    with pytest.raises(SchemaExportError, match="symlinks"):
        check_schemas(destination)
