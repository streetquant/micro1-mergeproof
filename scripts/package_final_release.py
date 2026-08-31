from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, overload

_FIXED_ZIP_TIME = (2026, 8, 30, 0, 0, 0)
_MAX_MEMBER_BYTES = 95_000_000
_MAX_ARCHIVE_BYTES = 1_500_000_000
_SOURCE_PREFIXES = (
    ".github/",
    "benchmark/",
    "benchmark_dbt/",
    "docs/",
    "examples/",
    "inputs/",
    "oracle/",
    "schemas/",
    "scripts/",
    "src/",
    "tests/",
    "upstream/",
)
_SOURCE_ROOTS = {
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "pyproject.toml",
    "uv.lock",
}
_EVIDENCE_PREFIXES = (
    "fixtures/agent/",
    "fixtures/replay/",
    "results/",
    "reviews/continuation-round-",
)
_EVIDENCE_ROOTS = {
    "benchmark/manifest.json",
    "benchmark_dbt/manifest.json",
    "docs/baseline-results.md",
    "docs/driftdoctor-upstream.md",
    "docs/evaluation-plan.md",
    "docs/evaluation-protocol-v2.md",
    "docs/requirements.md",
    "oracle/problem-brief.md",
    "upstream/DriftDoctor-LICENSE",
    "upstream/driftdoctor.lock.json",
}
_REQUIRED_EVIDENCE_ARTIFACTS = (
    "benchmark/manifest.json",
    "benchmark_dbt/manifest.json",
    "results/benchmark-validation.json",
    "results/baseline-replay-gpt-oss-20b/replay-verification.json",
    "results/driftproof-comparison/comparison.json",
    "results/agent-fallback-deterministic-verification.json",
    "results/agent-fallback-live-verification.json",
    "results/agent-fallback-replay-verification.json",
    "reviews/continuation-round-4/operator-adjudication.json",
)
_PRIVATE_HOST_MARKERS = tuple(
    marker.encode("utf-8")
    for marker in (
        "/" + "storage" + "/",
        "/" + "8tb" + "/",
        "/" + "home" + "/arch/",
        "/" + "mnt" + "/data/",
    )
)
_CREDENTIAL_PATTERNS = {
    "github_token": re.compile(rb"(?:ghp|github_pat)_[A-Za-z0-9_]{20,}"),
    "google_api_key": re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    "openai_key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    "groq_key": re.compile(rb"\bgsk_[A-Za-z0-9]{20,}"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
}


class PackagingError(RuntimeError):
    """Raised when a release package cannot be proven safe and reproducible."""


@dataclass(frozen=True)
class Member:
    archive_path: str
    source_path: Path
    mode: int = 0o644


@overload
def _run(root: Path, *argv: str, text: Literal[True] = True) -> str: ...


@overload
def _run(root: Path, *argv: str, text: Literal[False]) -> bytes: ...


def _run(root: Path, *argv: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        list(argv),
        cwd=root,
        check=False,
        capture_output=True,
        text=text,
        timeout=300,
    )
    if completed.returncode != 0:
        stderr = completed.stderr if text else completed.stderr.decode("utf-8", errors="replace")
        raise PackagingError(f"command failed ({' '.join(argv)}): {stderr[-4000:]}")
    if text:
        assert isinstance(completed.stdout, str)
        return completed.stdout
    assert isinstance(completed.stdout, bytes)
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise PackagingError(f"unsafe tracked path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PackagingError(f"unsafe tracked path: {value!r}")
    return path.as_posix()


def _tracked_files(root: Path) -> list[str]:
    raw = _run(root, "git", "ls-tree", "-r", "-z", "HEAD", text=False)
    assert isinstance(raw, bytes)
    result: list[str] = []
    for token in raw.split(b"\x00"):
        if not token:
            continue
        try:
            metadata, raw_path = token.split(b"\t", 1)
            mode, object_type, _object_id = metadata.decode("ascii").split(" ", 2)
            value = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise PackagingError("malformed or non-UTF-8 Git tree entry") from exc
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise PackagingError(
                f"unsupported tracked object (symlink/submodule/special mode): {mode} {object_type} {value}"
            )
        result.append(_safe_relative(value))
    if len(result) != len(set(result)):
        raise PackagingError("duplicate tracked paths")
    return sorted(result)


def _require_clean_worktree(root: Path) -> None:
    status = str(_run(root, "git", "status", "--porcelain=v1", "--untracked-files=all")).strip()
    if status:
        preview = "\n".join(status.splitlines()[:50])
        raise PackagingError(
            f"release packaging requires a clean worktree; commit or remove these paths:\n{preview}"
        )


def _is_source(path: str) -> bool:
    return path in _SOURCE_ROOTS or path.startswith(_SOURCE_PREFIXES)


def _is_evidence(path: str) -> bool:
    return path in _EVIDENCE_ROOTS or path.startswith(_EVIDENCE_PREFIXES)


def _required_evidence_hashes(root: Path, tracked: list[str]) -> dict[str, str]:
    tracked_set = set(tracked)
    missing = sorted(set(_REQUIRED_EVIDENCE_ARTIFACTS) - tracked_set)
    if missing:
        raise PackagingError(
            "required submission evidence is missing from HEAD: " + ", ".join(missing)
        )
    hashes: dict[str, str] = {}
    for path in _REQUIRED_EVIDENCE_ARTIFACTS:
        payload = _run(root, "git", "show", f"HEAD:{path}", text=False)
        hashes[path] = hashlib.sha256(payload).hexdigest()
    return hashes


def _audit_release_content(root: Path, tracked: list[str]) -> dict[str, object]:
    findings: list[dict[str, str]] = []
    for path in tracked:
        payload = _run(root, "git", "show", f"HEAD:{path}", text=False)
        assert isinstance(payload, bytes)
        for marker in _PRIVATE_HOST_MARKERS:
            if marker in payload:
                findings.append({"path": path, "kind": "private_host_path"})
        for name, pattern in _CREDENTIAL_PATTERNS.items():
            if pattern.search(payload):
                findings.append({"path": path, "kind": name})
    if findings:
        raise PackagingError(
            "release content contains private host paths or credential-shaped material: "
            + json.dumps(findings, sort_keys=True)
        )
    return {
        "schema_version": 1,
        "files_scanned": len(tracked),
        "private_host_path_hits": 0,
        "credential_shape_hits": 0,
        "passed": True,
    }


def _git_blob(root: Path, path: str, destination: Path) -> None:
    payload = _run(root, "git", "show", f"HEAD:{path}", text=False)
    assert isinstance(payload, bytes)
    if len(payload) > _MAX_MEMBER_BYTES:
        raise PackagingError(f"tracked file exceeds {_MAX_MEMBER_BYTES} bytes: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def _materialize_commit(root: Path, paths: list[str], destination: Path) -> list[Member]:
    members: list[Member] = []
    for relative in paths:
        target = destination / relative
        _git_blob(root, relative, target)
        executable = relative.startswith("scripts/") and target.read_bytes().startswith(b"#!")
        members.append(Member(relative, target, 0o755 if executable else 0o644))
    return members


def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.flag_bits |= 0x800
    return info


def _write_zip(path: Path, *, prefix: str, members: list[Member]) -> None:
    names: set[str] = set()
    total = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for member in sorted(members, key=lambda item: item.archive_path):
            relative = _safe_relative(member.archive_path)
            name = f"{prefix}/{relative}"
            if name in names:
                raise PackagingError(f"duplicate archive member: {name}")
            names.add(name)
            payload = member.source_path.read_bytes()
            if len(payload) > _MAX_MEMBER_BYTES:
                raise PackagingError(f"archive member exceeds limit: {relative}")
            total += len(payload)
            if total > _MAX_ARCHIVE_BYTES:
                raise PackagingError("uncompressed archive exceeds safety limit")
            archive.writestr(_zip_info(name, member.mode), payload)


def verify_zip(path: Path) -> dict[str, object]:
    seen: set[str] = set()
    top_levels: set[str] = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name in seen:
                raise PackagingError(f"duplicate member in {path.name}: {name}")
            seen.add(name)
            if "\\" in name or "\x00" in name:
                raise PackagingError(f"ambiguous member in {path.name}: {name!r}")
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
                raise PackagingError(f"unsafe member in {path.name}: {name}")
            if not pure.parts:
                raise PackagingError(f"empty member in {path.name}")
            top_levels.add(pure.parts[0])
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in {0, stat.S_IFREG}:
                raise PackagingError(f"special archive member in {path.name}: {name}")
            if info.file_size > _MAX_MEMBER_BYTES:
                raise PackagingError(f"oversized member in {path.name}: {name}")
            total += info.file_size
            if total > _MAX_ARCHIVE_BYTES:
                raise PackagingError(f"archive exceeds uncompressed limit: {path.name}")
    if len(top_levels) != 1:
        raise PackagingError(f"archive must have one top-level directory: {path.name}")
    return {
        "file": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "members": len(seen),
        "uncompressed_bytes": total,
        "top_level": next(iter(top_levels)),
    }


def safe_extract(path: Path, destination: Path) -> Path:
    """Extract one verified archive into a fresh, dedicated directory."""

    verification = verify_zip(path)
    if destination.is_symlink():
        raise PackagingError(f"extraction destination may not be a symlink: {destination}")
    if destination.exists():
        if not destination.is_dir():
            raise PackagingError(f"extraction destination must be a directory: {destination}")
        if any(destination.iterdir()):
            raise PackagingError(f"extraction destination must be empty: {destination}")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    extraction_root = destination.resolve()

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            pure = PurePosixPath(info.filename)
            target = destination.joinpath(*pure.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = target.parent.resolve()
            if not resolved_parent.is_relative_to(extraction_root):
                raise PackagingError(f"archive member escapes extraction root: {info.filename}")
            if target.exists() or target.is_symlink():
                raise PackagingError(f"archive member target already exists: {info.filename}")
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            mode = (info.external_attr >> 16) & 0o777
            target.chmod(mode or 0o644)
    return destination / str(verification["top_level"])


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _prepare_output_directory(output: Path) -> None:
    if output.is_symlink():
        raise PackagingError(f"release output may not be a symlink: {output}")
    if output.exists() and not output.is_dir():
        raise PackagingError(f"release output must be a directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    owned = [
        *output.glob("mergeproof-final-*.zip"),
        output / "release-manifest.json",
        output / "final-release-attestation.json",
        output / "SHA256SUMS",
    ]
    for path in owned:
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise PackagingError(f"unsafe owned release output: {path}")
            path.unlink()


def package(root: Path, output: Path) -> dict[str, object]:
    root = root.resolve()
    output_input = output.expanduser()
    if output_input.is_symlink():
        raise PackagingError(f"release output may not be a symlink: {output_input}")
    output = output_input.resolve()
    _require_clean_worktree(root)
    head = str(_run(root, "git", "rev-parse", "HEAD")).strip()
    branch = str(_run(root, "git", "branch", "--show-current")).strip()
    if branch != "main":
        raise PackagingError(f"release must be packaged from main, observed {branch!r}")
    tree = str(_run(root, "git", "rev-parse", "HEAD^{tree}")).strip()
    remote = str(_run(root, "git", "ls-remote", "origin", "refs/heads/main")).split()[0]
    if head != remote:
        raise PackagingError("local HEAD does not equal origin/main")
    tracked = _tracked_files(root)
    content_audit = _audit_release_content(root, tracked)
    required_evidence_sha256 = _required_evidence_hashes(root, tracked)
    source_paths = [path for path in tracked if _is_source(path)]
    evidence_paths = [path for path in tracked if _is_evidence(path)]
    if not source_paths or not evidence_paths:
        raise PackagingError("source/evidence selection is unexpectedly empty")

    _prepare_output_directory(output)

    with tempfile.TemporaryDirectory(prefix="mergeproof-release-") as raw_temp:
        temp = Path(raw_temp)
        full_stage = temp / "full"
        source_stage = temp / "source"
        evidence_stage = temp / "evidence"
        full_members = _materialize_commit(root, tracked, full_stage)
        source_members = _materialize_commit(root, source_paths, source_stage)
        evidence_members = _materialize_commit(root, evidence_paths, evidence_stage)

        bundle = temp / "repository.bundle"
        _run(root, "git", "bundle", "create", str(bundle), "main")
        _run(root, "git", "bundle", "verify", str(bundle))

        attestation = {
            "schema_version": 1,
            "repository": "streetquant/micro1-mergeproof",
            "private": True,
            "branch": branch,
            "commit": head,
            "tree": tree,
            "origin_main": remote,
            "primary_comparison_path": "results/driftproof-comparison/comparison.json",
            "baseline_replay_verification_path": "results/baseline-replay-gpt-oss-20b/replay-verification.json",
            "benchmark_manifest_paths": [
                "benchmark/manifest.json",
                "benchmark_dbt/manifest.json",
            ],
            "agent_fallback_verification_paths": [
                "results/agent-fallback-deterministic-verification.json",
                "results/agent-fallback-live-verification.json",
                "results/agent-fallback-replay-verification.json",
            ],
            "review_adjudication_path": "reviews/continuation-round-4/operator-adjudication.json",
            "required_evidence_sha256": required_evidence_sha256,
            "content_audit": content_audit,
            "tracked_file_count": len(tracked),
            "source_file_count": len(source_paths),
            "evidence_file_count": len(evidence_paths),
        }
        attestation_path = temp / "RELEASE-ATTESTATION.json"
        _write_json(attestation_path, attestation)
        full_members.extend(
            [
                Member("RELEASE-ATTESTATION.json", attestation_path),
                Member("repository.bundle", bundle),
            ]
        )
        source_members.append(Member("RELEASE-ATTESTATION.json", attestation_path))
        evidence_members.append(Member("RELEASE-ATTESTATION.json", attestation_path))

        short = head[:12]
        prefix = f"mergeproof-{short}"
        full_zip = output / f"mergeproof-final-full-{short}.zip"
        source_zip = output / f"mergeproof-final-source-{short}.zip"
        evidence_zip = output / f"mergeproof-final-evidence-{short}.zip"
        _write_zip(full_zip, prefix=prefix, members=full_members)
        _write_zip(source_zip, prefix=prefix, members=source_members)
        _write_zip(evidence_zip, prefix=prefix, members=evidence_members)

        assets = [verify_zip(path) for path in (full_zip, source_zip, evidence_zip)]
        extraction_root = safe_extract(full_zip, temp / "extracted")
        embedded_bundle = extraction_root / "repository.bundle"
        if not embedded_bundle.is_file() or _sha256(embedded_bundle) != _sha256(bundle):
            raise PackagingError("embedded Git bundle mismatch")
        _run(root, "git", "bundle", "verify", str(embedded_bundle))

        manifest = {
            "schema_version": 1,
            "commit": head,
            "tree": tree,
            "origin_main": remote,
            "private": True,
            "prefix": prefix,
            "assets": assets,
            "attestation": attestation,
        }
        manifest_path = output / "release-manifest.json"
        attestation_output = output / "final-release-attestation.json"
        _write_json(manifest_path, manifest)
        _write_json(attestation_output, attestation)

        checksums: list[tuple[str, str]] = []
        for path in sorted(output.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                checksums.append((_sha256(path), path.name))
        (output / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for digest, name in checksums),
            encoding="utf-8",
        )
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build deterministic, safely verifiable MergeProof release archives."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("release/final"))
    args = parser.parse_args()
    manifest = package(args.root, args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
