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
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, overload

_FIXED_ZIP_TIME = (2026, 8, 30, 0, 0, 0)
_MAX_MEMBER_BYTES = 95_000_000
_MAX_ARCHIVE_BYTES = 1_500_000_000
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_MEMBER_NAME_BYTES = 4_096
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
    "submission/",
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
    "reviews/",
    "submission/",
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
_REVIEW_QUALIFICATION_ARTIFACTS = (
    "reviews/recovery-promotion/qualification.json",
    "reviews/replay-nonmutating/qualification.json",
    "reviews/2026-08-31-round-1-human-judge/qualification.json",
    "reviews/2026-08-31-round-2-agent-sdk/qualification.json",
    "reviews/2026-08-31-round-3-release-delivery/qualification.json",
    "reviews/2026-08-31-round-4-consumer-verifier/qualification.json",
)
_REQUIRED_EVIDENCE_ARTIFACTS = (
    "benchmark/manifest.json",
    "benchmark_dbt/manifest.json",
    "results/benchmark-validation.json",
    "results/baseline-replay-gpt-oss-20b/replay-verification.json",
    "results/driftproof-comparison/comparison.json",
    "results/agent-fallback-deterministic-verification.json",
    "results/agent-fallback-live-verification.json",
    "results/agent-fallback-replay-verification.json",
    "submission/manifest.json",
    *_REVIEW_QUALIFICATION_ARTIFACTS,
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
_OWNED_RELEASE_FILES = {
    "release-manifest.json",
    "final-release-attestation.json",
    "START_HERE.md",
    "START_HERE.html",
    "submission-manifest.json",
    "release-verification.json",
    "SHA256SUMS",
}
_OWNED_RELEASE_ARCHIVE = re.compile(
    r"^mergeproof-final-(?:full|source|evidence)-[0-9a-f]{12}\.zip$"
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
    if path.is_symlink() or not path.is_file():
        raise PackagingError(f"archive must be a regular file: {path}")
    seen: set[str] = set()
    top_levels: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ARCHIVE_MEMBERS:
                raise PackagingError(f"archive has too many members ({len(entries)}): {path.name}")
            for info in entries:
                name = info.filename
                if len(name.encode("utf-8")) > _MAX_MEMBER_NAME_BYTES:
                    raise PackagingError(f"archive member name is too long: {path.name}")
                if name in seen:
                    raise PackagingError(f"duplicate member in {path.name}: {name}")
                seen.add(name)
                if info.flag_bits & 0x1:
                    raise PackagingError(f"encrypted member in {path.name}: {name}")
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
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PackagingError(f"CRC verification failed in {path.name}: {bad_member}")
    except PackagingError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile, zlib.error) as exc:
        raise PackagingError(f"invalid or unreadable archive {path.name}: {exc}") from exc
    if len(top_levels) != 1:
        raise PackagingError(f"archive must have one top-level directory: {path.name}")
    return {
        "file": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "members": len(seen),
        "uncompressed_bytes": total,
        "top_level": next(iter(top_levels)),
        "crc_verified": True,
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

    owned: list[Path] = []
    unexpected: list[str] = []
    for path in output.iterdir():
        recognized = path.name in _OWNED_RELEASE_FILES or bool(
            _OWNED_RELEASE_ARCHIVE.fullmatch(path.name)
        )
        if not recognized:
            unexpected.append(path.name)
            continue
        if path.is_symlink() or not path.is_file():
            raise PackagingError(f"unsafe owned release output: {path}")
        owned.append(path)
    if unexpected:
        raise PackagingError(
            "release output contains unrelated entries; choose a dedicated path: "
            + ", ".join(sorted(unexpected))
        )
    for path in owned:
        path.unlink()


def _load_json_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise PackagingError(f"required release JSON is missing or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackagingError(f"invalid release JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PackagingError(f"release JSON must be an object: {path}")
    return value


def _checksum_records(directory: Path) -> dict[str, str]:
    path = directory / "SHA256SUMS"
    if path.is_symlink() or not path.is_file():
        raise PackagingError("SHA256SUMS is missing or unsafe")
    records: dict[str, str] = {}
    pattern = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$")
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        match = pattern.fullmatch(line)
        if match is None:
            raise PackagingError(f"malformed SHA256SUMS line: {line!r}")
        digest, name = match.groups()
        if name == "SHA256SUMS" or name in records:
            raise PackagingError(f"duplicate or self-referential checksum entry: {name}")
        records[name] = digest
    if not records:
        raise PackagingError("SHA256SUMS is empty")
    return records


def verify_release_directory(directory: Path) -> dict[str, object]:
    """Verify a downloaded release directory without trusting path existence alone."""

    if directory.is_symlink() or not directory.is_dir():
        raise PackagingError(f"release directory is missing or unsafe: {directory}")
    records = _checksum_records(directory)
    observed: set[str] = set()
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise PackagingError(f"release directory contains a non-regular entry: {path.name}")
        observed.add(path.name)
    expected = {*records, "SHA256SUMS"}
    if observed != expected:
        raise PackagingError(
            f"release file set mismatch; missing={sorted(expected - observed)}, "
            f"unexpected={sorted(observed - expected)}"
        )
    for name, digest in records.items():
        if _sha256(directory / name) != digest:
            raise PackagingError(f"release checksum mismatch: {name}")

    required_names = {
        "release-manifest.json",
        "final-release-attestation.json",
        "START_HERE.md",
        "START_HERE.html",
        "submission-manifest.json",
    }
    missing_names = sorted(required_names - set(records))
    if missing_names:
        raise PackagingError(
            "release is missing required entry points: " + ", ".join(missing_names)
        )

    manifest = _load_json_object(directory / "release-manifest.json")
    attestation = _load_json_object(directory / "final-release-attestation.json")
    submission = _load_json_object(directory / "submission-manifest.json")
    if manifest.get("schema_version") != 1:
        raise PackagingError("unsupported release manifest schema")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise PackagingError("release manifest commit is invalid")
    if manifest.get("origin_main") != commit or manifest.get("private") is not True:
        raise PackagingError("release manifest is not bound to private origin/main")
    if manifest.get("attestation") != attestation:
        raise PackagingError("release manifest and attestation differ")
    if attestation.get("commit") != commit or attestation.get("origin_main") != commit:
        raise PackagingError("release attestation commit binding is inconsistent")
    if submission.get("protocol") != "driftproof.submission-manifest.v1":
        raise PackagingError("unexpected submission manifest protocol")
    if submission.get("human_approval_required") is not True:
        raise PackagingError("submission manifest weakened the human approval boundary")
    if submission.get("consequential_action_taken") is not False:
        raise PackagingError("submission manifest claims a consequential action")

    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 3:
        raise PackagingError("release manifest must describe exactly three archives")
    expected_archives = {name for name in records if _OWNED_RELEASE_ARCHIVE.fullmatch(name)}
    if len(expected_archives) != 3:
        raise PackagingError("release checksum set must contain exactly three archives")
    verified_assets: list[dict[str, object]] = []
    asset_names: set[str] = set()
    for raw_asset in assets:
        if not isinstance(raw_asset, dict):
            raise PackagingError("release asset metadata must be an object")
        asset_file = raw_asset.get("file")
        if (
            not isinstance(asset_file, str)
            or asset_file not in expected_archives
            or asset_file in asset_names
        ):
            raise PackagingError(f"release asset identity is invalid: {asset_file!r}")
        asset_names.add(asset_file)
        verified = verify_zip(directory / asset_file)
        for field in ("sha256", "bytes", "members", "uncompressed_bytes", "top_level"):
            if raw_asset.get(field) != verified.get(field):
                raise PackagingError(f"release asset metadata mismatch for {asset_file}: {field}")
        if raw_asset.get("crc_verified") is not True or verified.get("crc_verified") is not True:
            raise PackagingError(f"release asset lacks CRC verification: {asset_file}")
        verified_assets.append(verified)
    if asset_names != expected_archives:
        raise PackagingError("release manifest archive set is incomplete")

    delivery_files = manifest.get("delivery_files")
    if not isinstance(delivery_files, list) or len(delivery_files) != 3:
        raise PackagingError("release manifest must describe three delivery entry points")
    expected_delivery = {
        "START_HERE.md": "submission/START_HERE.md",
        "START_HERE.html": "submission/START_HERE.html",
        "submission-manifest.json": "submission/manifest.json",
    }
    delivery_names: set[str] = set()
    for raw_file in delivery_files:
        if not isinstance(raw_file, dict):
            raise PackagingError("delivery file metadata must be an object")
        delivery_file = raw_file.get("file")
        if (
            not isinstance(delivery_file, str)
            or delivery_file not in expected_delivery
            or delivery_file in delivery_names
            or raw_file.get("source") != expected_delivery[delivery_file]
        ):
            raise PackagingError(f"unexpected delivery file: {delivery_file!r}")
        delivery_names.add(delivery_file)
        path = directory / delivery_file
        if raw_file.get("bytes") != path.stat().st_size or raw_file.get("sha256") != _sha256(path):
            raise PackagingError(f"delivery file metadata mismatch: {delivery_file}")
    if delivery_names != set(expected_delivery):
        raise PackagingError("release manifest delivery entry set is incomplete")

    with tempfile.TemporaryDirectory(prefix="mergeproof-release-verify-") as raw_temp:
        temp = Path(raw_temp)
        extracted: dict[str, Path] = {}
        for name in sorted(expected_archives):
            match = re.fullmatch(r"mergeproof-final-(full|source|evidence)-[0-9a-f]{12}\.zip", name)
            if match is None:
                raise PackagingError(f"unexpected archive name: {name}")
            label = match.group(1)
            extracted[label] = safe_extract(directory / name, temp / label)
            for relative, delivery_name in (
                ("submission/START_HERE.md", "START_HERE.md"),
                ("submission/START_HERE.html", "START_HERE.html"),
                ("submission/manifest.json", "submission-manifest.json"),
            ):
                extracted_path = extracted[label] / relative
                if not extracted_path.is_file() or _sha256(extracted_path) != _sha256(
                    directory / delivery_name
                ):
                    raise PackagingError(
                        f"{label} archive delivery entry differs from the release root: {relative}"
                    )

        full_attestation = extracted["full"] / "RELEASE-ATTESTATION.json"
        if not full_attestation.is_file() or _load_json_object(full_attestation) != attestation:
            raise PackagingError("full archive attestation differs from the release root")
        evidence_root = extracted["evidence"]
        review_paths = attestation.get("review_qualification_paths")
        if not isinstance(review_paths, list) or not review_paths:
            raise PackagingError("release attestation lacks review qualification paths")
        for relative in review_paths:
            if not isinstance(relative, str) or not (evidence_root / relative).is_file():
                raise PackagingError(f"evidence archive omits review qualification: {relative}")

        embedded_bundle = extracted["full"] / "repository.bundle"
        if not embedded_bundle.is_file():
            raise PackagingError("full archive is missing repository.bundle")
        verify_repo = temp / "bundle-verifier"
        _run(temp, "git", "init", "-q", str(verify_repo))
        _run(verify_repo, "git", "bundle", "verify", str(embedded_bundle))
        heads = str(_run(verify_repo, "git", "bundle", "list-heads", str(embedded_bundle)))
        if f"{commit} refs/heads/main" not in heads.splitlines():
            raise PackagingError("embedded Git bundle does not bind the release main commit")

    return {
        "schema_version": 1,
        "protocol": "driftproof.release-verification.v1",
        "verified": True,
        "commit": commit,
        "tree": manifest.get("tree"),
        "files": len(records),
        "archives": verified_assets,
        "sha256sums_sha256": _sha256(directory / "SHA256SUMS"),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


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
            "review_qualification_paths": list(_REVIEW_QUALIFICATION_ARTIFACTS),
            "submission_manifest_path": "submission/manifest.json",
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

        archive_paths = {
            "full": full_zip,
            "source": source_zip,
            "evidence": evidence_zip,
        }
        assets = [verify_zip(path) for path in archive_paths.values()]
        extracted_roots = {
            label: safe_extract(path, temp / f"extracted-{label}")
            for label, path in archive_paths.items()
        }
        embedded_bundle = extracted_roots["full"] / "repository.bundle"
        if not embedded_bundle.is_file() or _sha256(embedded_bundle) != _sha256(bundle):
            raise PackagingError("embedded Git bundle mismatch")
        _run(root, "git", "bundle", "verify", str(embedded_bundle))
        for label, extraction_root in extracted_roots.items():
            start_here = extraction_root / "submission" / "START_HERE.md"
            manifest_file = extraction_root / "submission" / "manifest.json"
            if not start_here.is_file() or not manifest_file.is_file():
                raise PackagingError(
                    f"{label} archive is missing the human or machine submission entry point"
                )

        delivery_sources = {
            "START_HERE.md": "submission/START_HERE.md",
            "START_HERE.html": "submission/START_HERE.html",
            "submission-manifest.json": "submission/manifest.json",
        }
        delivery_payloads: dict[str, bytes] = {}
        delivery_files: list[dict[str, object]] = []
        for name, source in delivery_sources.items():
            payload = _run(root, "git", "show", f"HEAD:{source}", text=False)
            assert isinstance(payload, bytes)
            delivery_payloads[name] = payload
            delivery_files.append(
                {
                    "file": name,
                    "source": source,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )

        manifest = {
            "schema_version": 1,
            "commit": head,
            "tree": tree,
            "origin_main": remote,
            "private": True,
            "prefix": prefix,
            "assets": assets,
            "delivery_files": delivery_files,
            "verification_protocol": "driftproof.release-verification.v1",
            "attestation": attestation,
        }
        manifest_path = output / "release-manifest.json"
        attestation_output = output / "final-release-attestation.json"
        _write_json(manifest_path, manifest)
        _write_json(attestation_output, attestation)
        for name, payload in delivery_payloads.items():
            (output / name).write_bytes(payload)

        checksums: list[tuple[str, str]] = []
        for path in sorted(output.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                checksums.append((_sha256(path), path.name))
        (output / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for digest, name in checksums),
            encoding="utf-8",
        )
        verification = verify_release_directory(output)
        return {"manifest": manifest, "verification": verification}


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
