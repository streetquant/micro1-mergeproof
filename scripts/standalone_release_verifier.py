from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

_MAX_MEMBER_BYTES = 95_000_000
_MAX_ARCHIVE_BYTES = 1_500_000_000
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_MEMBER_NAME_BYTES = 4_096
_ARCHIVE_PATTERN = re.compile(
    r"^mergeproof-final-(?P<label>full|source|evidence)-(?P<short>[0-9a-f]{12})\.zip$"
)
_CHECKSUM_PATTERN = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$")
_DELIVERY_SOURCES = {
    "START_HERE.md": "submission/START_HERE.md",
    "START_HERE.html": "submission/START_HERE.html",
    "JUDGE_CHECKLIST.md": "submission/JUDGE_CHECKLIST.md",
    "CLAIM_LEDGER.json": "submission/CLAIM_LEDGER.json",
    "RUBRIC_MAP.json": "submission/RUBRIC_MAP.json",
    "AGENT_TRAJECTORIES.json": "submission/AGENT_TRAJECTORIES.json",
    "TRACE_INDEX.json": "submission/TRACE_INDEX.json",
    "submission-manifest.json": "submission/manifest.json",
}
_REQUIRED_ROOT = {
    "release-manifest.json",
    "final-release-attestation.json",
    "verify-release.pyz",
    *_DELIVERY_SOURCES,
}


class ReleaseVerificationError(RuntimeError):
    """Raised when a downloaded release cannot be authenticated safely."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ReleaseVerificationError(f"{label} is missing or unsafe: {path.name}")
    return path


def _load_object(path: Path, label: str) -> dict[str, Any]:
    _regular_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseVerificationError(f"{label} must be a JSON object")
    return value


def _checksum_records(directory: Path) -> dict[str, str]:
    path = _regular_file(directory / "SHA256SUMS", "SHA256SUMS")
    records: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        match = _CHECKSUM_PATTERN.fullmatch(line)
        if match is None:
            raise ReleaseVerificationError(f"malformed SHA256SUMS line: {line!r}")
        digest, name = match.groups()
        if name == "SHA256SUMS" or name in records:
            raise ReleaseVerificationError(f"duplicate or self-referential checksum entry: {name}")
        records[name] = digest
    if not records:
        raise ReleaseVerificationError("SHA256SUMS is empty")
    return records


def _safe_zip_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise ReleaseVerificationError(f"ambiguous archive member: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts or not pure.parts:
        raise ReleaseVerificationError(f"unsafe archive member: {name}")
    return pure


def _verify_zip(path: Path) -> dict[str, Any]:
    _regular_file(path, "release archive")
    seen: set[str] = set()
    top_levels: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ARCHIVE_MEMBERS:
                raise ReleaseVerificationError(
                    f"archive has too many members ({len(entries)}): {path.name}"
                )
            for info in entries:
                name = info.filename
                if len(name.encode("utf-8")) > _MAX_MEMBER_NAME_BYTES:
                    raise ReleaseVerificationError(f"archive member name is too long: {path.name}")
                if name in seen:
                    raise ReleaseVerificationError(f"duplicate member in {path.name}: {name}")
                seen.add(name)
                if info.flag_bits & 0x1:
                    raise ReleaseVerificationError(f"encrypted member in {path.name}: {name}")
                pure = _safe_zip_name(name)
                top_levels.add(pure.parts[0])
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in {0, stat.S_IFREG}:
                    raise ReleaseVerificationError(f"special archive member in {path.name}: {name}")
                if info.file_size > _MAX_MEMBER_BYTES:
                    raise ReleaseVerificationError(
                        f"oversized archive member in {path.name}: {name}"
                    )
                total += info.file_size
                if total > _MAX_ARCHIVE_BYTES:
                    raise ReleaseVerificationError(
                        f"archive exceeds uncompressed limit: {path.name}"
                    )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ReleaseVerificationError(
                    f"CRC verification failed in {path.name}: {bad_member}"
                )
    except ReleaseVerificationError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
        raise ReleaseVerificationError(f"invalid or unreadable archive {path.name}") from exc
    if len(top_levels) != 1:
        raise ReleaseVerificationError(f"archive must contain one top-level directory: {path.name}")
    return {
        "file": path.name,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
        "members": len(seen),
        "uncompressed_bytes": total,
        "top_level": next(iter(top_levels)),
        "crc_verified": True,
    }


def _verify_zipapp(path: Path) -> dict[str, Any]:
    _regular_file(path, "standalone verifier")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if [entry.filename for entry in entries] != ["__main__.py"]:
                raise ReleaseVerificationError(
                    "standalone verifier must contain exactly __main__.py"
                )
            entry = entries[0]
            if entry.flag_bits & 0x1 or entry.file_size > 2_000_000:
                raise ReleaseVerificationError("standalone verifier entry is unsafe")
            if archive.testzip() is not None:
                raise ReleaseVerificationError("standalone verifier CRC verification failed")
            source = archive.read(entry)
    except ReleaseVerificationError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
        raise ReleaseVerificationError("standalone verifier is not a valid zipapp") from exc
    if b"driftproof.standalone-release-verification.v1" not in source:
        raise ReleaseVerificationError("standalone verifier source lacks its protocol identity")
    return {
        "file": path.name,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
        "member": "__main__.py",
        "member_sha256": _sha256_bytes(source),
        "crc_verified": True,
    }


def _require_safety(payload: dict[str, Any], label: str) -> None:
    if payload.get("human_approval_required") is not True:
        raise ReleaseVerificationError(f"{label} weakened the human approval boundary")
    if payload.get("consequential_action_taken") is not False:
        raise ReleaseVerificationError(f"{label} claims a consequential action")


def _verify_submission_packet(
    directory: Path,
    submission: dict[str, Any],
) -> dict[str, Any]:
    trajectories = _load_object(directory / "AGENT_TRAJECTORIES.json", "agent trajectories")
    trace_index = _load_object(directory / "TRACE_INDEX.json", "trace index")
    claims = _load_object(directory / "CLAIM_LEDGER.json", "claim ledger")
    rubric = _load_object(directory / "RUBRIC_MAP.json", "rubric map")
    for label, payload in (
        ("submission manifest", submission),
        ("agent trajectories", trajectories),
        ("trace index", trace_index),
        ("claim ledger", claims),
        ("rubric map", rubric),
    ):
        _require_safety(payload, label)

    declared = trajectories.get("declared_workflow_agents")
    observed = trajectories.get("observed_workflow_agents")
    if (
        trajectories.get("protocol") != "driftproof.agent-trajectories.v1"
        or trajectories.get("coverage_complete") is not True
        or not isinstance(declared, list)
        or not declared
        or declared != observed
        or any(not isinstance(agent, str) or not agent for agent in declared)
    ):
        raise ReleaseVerificationError("agent trajectory coverage is incomplete or inconsistent")

    representative = trace_index.get("representative_packet")
    trajectories_path = directory / "AGENT_TRAJECTORIES.json"
    if (
        trace_index.get("protocol") != "driftproof.trace-index.v1"
        or trace_index.get("coverage_complete") is not True
        or not isinstance(representative, dict)
        or representative.get("path") != "submission/AGENT_TRAJECTORIES.json"
        or representative.get("bytes") != trajectories_path.stat().st_size
        or representative.get("sha256") != _sha256_file(trajectories_path)
    ):
        raise ReleaseVerificationError(
            "trace index does not bind the representative trajectory packet"
        )

    claim_rows = claims.get("claims")
    if (
        claims.get("protocol") != "driftproof.claim-ledger.v1"
        or claims.get("all_claims_supported") is not True
        or not isinstance(claim_rows, list)
        or not claim_rows
        or claims.get("claim_count") != len(claim_rows)
    ):
        raise ReleaseVerificationError("claim ledger is incomplete or unsupported")

    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ReleaseVerificationError("rubric map lacks criteria")
    points = 0
    for criterion in criteria:
        if not isinstance(criterion, dict) or not isinstance(criterion.get("points"), int):
            raise ReleaseVerificationError("rubric criterion is malformed")
        points += int(criterion["points"])
    claim_binding = rubric.get("claim_ledger")
    claim_path = directory / "CLAIM_LEDGER.json"
    if (
        rubric.get("protocol") != "driftproof.rubric-map.v1"
        or rubric.get("total_points") != 100
        or points != 100
        or not isinstance(claim_binding, dict)
        or claim_binding.get("path") != "submission/CLAIM_LEDGER.json"
        or claim_binding.get("bytes") != claim_path.stat().st_size
        or claim_binding.get("sha256") != _sha256_file(claim_path)
    ):
        raise ReleaseVerificationError("rubric map is not bound to the claim ledger")

    entry_points = submission.get("entry_points")
    generated_files = submission.get("generated_files")
    expected_entry_points = {
        "human": "submission/START_HERE.md",
        "browser": "submission/START_HERE.html",
        "machine": "submission/manifest.json",
        "judge_checklist": "submission/JUDGE_CHECKLIST.md",
        "claim_ledger": "submission/CLAIM_LEDGER.json",
        "rubric_map": "submission/RUBRIC_MAP.json",
        "agent_trajectories": "submission/AGENT_TRAJECTORIES.json",
        "trace_index": "submission/TRACE_INDEX.json",
    }
    if entry_points != expected_entry_points or not isinstance(generated_files, dict):
        raise ReleaseVerificationError("submission entry points are incomplete or unexpected")
    for name, source in _DELIVERY_SOURCES.items():
        if source == "submission/manifest.json":
            continue
        metadata = generated_files.get(Path(source).name)
        release_path = directory / name
        if (
            not isinstance(metadata, dict)
            or metadata.get("bytes") != release_path.stat().st_size
            or metadata.get("sha256") != _sha256_file(release_path)
        ):
            raise ReleaseVerificationError(
                f"submission manifest does not bind generated file: {name}"
            )
    return {
        "verified": True,
        "workflow_agents": declared,
        "claim_count": len(claim_rows),
        "rubric_points": points,
        "generated_files": len(generated_files),
    }


def _run_git_bundle_verification(bundle: bytes, commit: str) -> dict[str, Any]:
    git = shutil.which("git")
    if git is None:
        raise ReleaseVerificationError("Git is required to verify the embedded repository bundle")
    with tempfile.TemporaryDirectory(prefix="driftproof-standalone-git-") as raw_temp:
        temp = Path(raw_temp)
        bundle_path = temp / "repository.bundle"
        bundle_path.write_bytes(bundle)
        repo = temp / "verifier"
        completed = subprocess.run(
            [git, "init", "-q", str(repo)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0:
            raise ReleaseVerificationError("could not initialize Git bundle verifier")
        verify = subprocess.run(
            [git, "-C", str(repo), "bundle", "verify", str(bundle_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if verify.returncode != 0:
            raise ReleaseVerificationError("embedded repository bundle verification failed")
        heads = subprocess.run(
            [git, "-C", str(repo), "bundle", "list-heads", str(bundle_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if heads.returncode != 0 or f"{commit} refs/heads/main" not in heads.stdout.splitlines():
            raise ReleaseVerificationError(
                "embedded repository bundle does not bind the release main commit"
            )
    return {
        "verified": True,
        "bytes": len(bundle),
        "sha256": _sha256_bytes(bundle),
        "main_commit": commit,
    }


def verify_release(directory: Path) -> dict[str, Any]:
    directory = directory.expanduser().resolve(strict=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ReleaseVerificationError("release directory is missing or unsafe")
    records = _checksum_records(directory)
    observed: set[str] = set()
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ReleaseVerificationError(
                f"release directory contains a non-regular entry: {path.name}"
            )
        observed.add(path.name)
    expected = {*records, "SHA256SUMS"}
    if observed != expected:
        raise ReleaseVerificationError(
            f"release file set mismatch; missing={sorted(expected - observed)}, "
            f"unexpected={sorted(observed - expected)}"
        )
    for name, digest in records.items():
        if _sha256_file(directory / name) != digest:
            raise ReleaseVerificationError(f"release checksum mismatch: {name}")
    missing = sorted(_REQUIRED_ROOT - set(records))
    if missing:
        raise ReleaseVerificationError(
            "release is missing required entry points: " + ", ".join(missing)
        )

    manifest = _load_object(directory / "release-manifest.json", "release manifest")
    attestation = _load_object(directory / "final-release-attestation.json", "release attestation")
    submission = _load_object(directory / "submission-manifest.json", "submission manifest")
    if manifest.get("schema_version") != 1:
        raise ReleaseVerificationError("unsupported release manifest schema")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ReleaseVerificationError("release manifest commit is invalid")
    if manifest.get("origin_main") != commit or manifest.get("private") is not True:
        raise ReleaseVerificationError("release manifest is not bound to private origin/main")
    if manifest.get("attestation") != attestation:
        raise ReleaseVerificationError("release manifest and attestation differ")
    if attestation.get("commit") != commit or attestation.get("origin_main") != commit:
        raise ReleaseVerificationError("release attestation commit binding is inconsistent")
    if submission.get("protocol") != "driftproof.submission-manifest.v1":
        raise ReleaseVerificationError("unexpected submission manifest protocol")
    packet = _verify_submission_packet(directory, submission)

    verifier_metadata = manifest.get("standalone_verifier")
    verifier_path = directory / "verify-release.pyz"
    verified_verifier = _verify_zipapp(verifier_path)
    if (
        not isinstance(verifier_metadata, dict)
        or verifier_metadata.get("file") != "verify-release.pyz"
        or verifier_metadata.get("bytes") != verifier_path.stat().st_size
        or verifier_metadata.get("sha256") != _sha256_file(verifier_path)
        or verifier_metadata.get("member") != verified_verifier.get("member")
        or verifier_metadata.get("member_sha256") != verified_verifier.get("member_sha256")
        or verifier_metadata.get("source") != "scripts/standalone_release_verifier.py"
        or verifier_metadata.get("source_sha256") != verified_verifier.get("member_sha256")
        or verifier_metadata.get("crc_verified") is not True
    ):
        raise ReleaseVerificationError("release manifest does not bind the standalone verifier")

    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 3:
        raise ReleaseVerificationError("release manifest must describe three archives")
    expected_archives = {name for name in records if _ARCHIVE_PATTERN.fullmatch(name)}
    if len(expected_archives) != 3:
        raise ReleaseVerificationError("checksum set must contain exactly three archives")
    asset_names: set[str] = set()
    verified_assets: list[dict[str, Any]] = []
    archive_roots: dict[str, str] = {}
    for raw_asset in assets:
        if not isinstance(raw_asset, dict):
            raise ReleaseVerificationError("archive metadata must be an object")
        raw_name = raw_asset.get("file")
        if (
            not isinstance(raw_name, str)
            or raw_name not in expected_archives
            or raw_name in asset_names
        ):
            raise ReleaseVerificationError(f"invalid archive identity: {raw_name!r}")
        name = raw_name
        asset_names.add(name)
        verified = _verify_zip(directory / name)
        for field in ("sha256", "bytes", "members", "uncompressed_bytes", "top_level"):
            if raw_asset.get(field) != verified.get(field):
                raise ReleaseVerificationError(f"archive metadata mismatch for {name}: {field}")
        if raw_asset.get("crc_verified") is not True:
            raise ReleaseVerificationError(f"archive lacks CRC verification: {name}")
        match = _ARCHIVE_PATTERN.fullmatch(name)
        assert match is not None
        archive_roots[match.group("label")] = str(verified["top_level"])
        verified_assets.append(verified)
    if asset_names != expected_archives or set(archive_roots) != {"full", "source", "evidence"}:
        raise ReleaseVerificationError("release archive set is incomplete")

    delivery_files = manifest.get("delivery_files")
    if not isinstance(delivery_files, list) or len(delivery_files) != len(_DELIVERY_SOURCES):
        raise ReleaseVerificationError("release delivery metadata is incomplete")
    delivery_names: set[str] = set()
    for raw_file in delivery_files:
        if not isinstance(raw_file, dict):
            raise ReleaseVerificationError("delivery metadata must be an object")
        raw_name = raw_file.get("file")
        if not isinstance(raw_name, str):
            raise ReleaseVerificationError(f"unexpected delivery file: {raw_name!r}")
        name = raw_name
        if (
            name not in _DELIVERY_SOURCES
            or name in delivery_names
            or raw_file.get("source") != _DELIVERY_SOURCES[name]
        ):
            raise ReleaseVerificationError(f"unexpected delivery file: {name!r}")
        delivery_names.add(name)
        path = directory / name
        if raw_file.get("bytes") != path.stat().st_size or raw_file.get("sha256") != _sha256_file(
            path
        ):
            raise ReleaseVerificationError(f"delivery metadata mismatch: {name}")
    if delivery_names != set(_DELIVERY_SOURCES):
        raise ReleaseVerificationError("release delivery entry set is incomplete")

    bundle_verification: dict[str, Any] | None = None
    review_paths = attestation.get("review_qualification_paths")
    if not isinstance(review_paths, list) or not review_paths:
        raise ReleaseVerificationError("release attestation lacks review qualification paths")
    for name in sorted(expected_archives):
        match = _ARCHIVE_PATTERN.fullmatch(name)
        assert match is not None
        label = match.group("label")
        top = archive_roots[label]
        with zipfile.ZipFile(directory / name) as archive:
            names = set(archive.namelist())
            for root_name, source in _DELIVERY_SOURCES.items():
                member = f"{top}/{source}"
                if (
                    member not in names
                    or archive.read(member) != (directory / root_name).read_bytes()
                ):
                    raise ReleaseVerificationError(
                        f"{label} archive delivery entry differs from release root: {source}"
                    )
            if label in {"full", "source"}:
                verifier_source = str(verifier_metadata["source"])
                verifier_member = f"{top}/{verifier_source}"
                if verifier_member not in names or _sha256_bytes(
                    archive.read(verifier_member)
                ) != verifier_metadata.get("source_sha256"):
                    raise ReleaseVerificationError(
                        f"{label} archive does not bind the standalone verifier source"
                    )
            if label == "evidence":
                for relative in review_paths:
                    if not isinstance(relative, str) or f"{top}/{relative}" not in names:
                        raise ReleaseVerificationError(
                            f"evidence archive omits review qualification: {relative}"
                        )
            if label == "full":
                bundle_member = f"{top}/repository.bundle"
                if bundle_member not in names:
                    raise ReleaseVerificationError("full archive is missing repository.bundle")
                bundle_verification = _run_git_bundle_verification(
                    archive.read(bundle_member), commit
                )
    if bundle_verification is None:
        raise ReleaseVerificationError("embedded Git bundle was not verified")

    return {
        "schema_version": 1,
        "protocol": "driftproof.standalone-release-verification.v1",
        "verified": True,
        "commit": commit,
        "tree": manifest.get("tree"),
        "files": len(records),
        "archives": verified_assets,
        "standalone_verifier": verified_verifier,
        "submission_packet": packet,
        "repository_bundle": bundle_verification,
        "sha256sums_sha256": _sha256_file(directory / "SHA256SUMS"),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def _fail(exc: Exception) -> NoReturn:
    payload = {
        "schema_version": 1,
        "protocol": "driftproof.standalone-release-verification.v1",
        "verified": False,
        "error_code": "release_invalid",
        "error": type(exc).__name__,
        "detail": str(exc)[:4_000],
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    raise SystemExit(30)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a downloaded DriftProof release using only Python's standard library and Git."
        )
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path(sys.argv[0]).resolve().parent,
        help="Downloaded release directory; defaults to the verifier's own directory.",
    )
    args = parser.parse_args()
    try:
        payload = verify_release(args.directory)
    except Exception as exc:
        _fail(exc)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
