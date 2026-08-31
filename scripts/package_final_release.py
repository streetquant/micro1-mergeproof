from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, overload

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from scripts.verify_demo_video import verify_video_delivery  # noqa: E402

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
    "reviews/2026-08-31-round-5-installed-demo/qualification.json",
    "reviews/2026-08-31-round-6-response-binding/qualification.json",
    "reviews/2026-08-31-round-7-judge-packet/qualification.json",
    "reviews/2026-08-31-round-8-standalone-verifier/qualification.json",
    "reviews/2026-08-31-round-11-exact-source-video/qualification.json",
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
    "submission/JUDGE_CHECKLIST.md",
    "submission/CLAIM_LEDGER.json",
    "submission/RUBRIC_MAP.json",
    "submission/AGENT_TRAJECTORIES.json",
    "submission/TRACE_INDEX.json",
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
    "JUDGE_CHECKLIST.md",
    "CLAIM_LEDGER.json",
    "RUBRIC_MAP.json",
    "AGENT_TRAJECTORIES.json",
    "TRACE_INDEX.json",
    "verify-release.pyz",
    "submission-manifest.json",
    "release-verification.json",
    "SHA256SUMS",
}
_OWNED_RELEASE_ARCHIVE = re.compile(
    r"^mergeproof-final-(?:full|source|evidence)-[0-9a-f]{12}\.zip$"
)
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
_MEDIA_FILES = (
    "driftproof-demo.mp4",
    "driftproof-demo-transcript.md",
    "driftproof-demo-storyboard.json",
    "driftproof-demo-source-manifest.json",
    "driftproof-demo-scene-durations.json",
    "driftproof-demo-verification.json",
)
_MEDIA_ARCHIVE_LABELS = {"full", "evidence"}
_MEDIA_SOURCE_SCRIPTS = (
    "scripts/render_demo_video.py",
    "scripts/verify_demo_video.py",
)
_OWNED_RELEASE_FILES.update(_MEDIA_FILES)
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


def _write_standalone_verifier(root: Path, path: Path) -> dict[str, object]:
    source_relative = "scripts/standalone_release_verifier.py"
    source = _run(root, "git", "show", f"HEAD:{source_relative}", text=False)
    assert isinstance(source, bytes)
    if len(source) > 2_000_000:
        raise PackagingError("standalone verifier source exceeds the safety limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(_zip_info("__main__.py", 0o755), source)
    verified = _verify_standalone_verifier(path)
    return {
        **verified,
        "source": source_relative,
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }


def _verify_standalone_verifier(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise PackagingError(f"standalone verifier must be a regular file: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if [entry.filename for entry in entries] != ["__main__.py"]:
                raise PackagingError("standalone verifier must contain exactly __main__.py")
            entry = entries[0]
            if entry.flag_bits & 0x1 or entry.file_size > 2_000_000:
                raise PackagingError("standalone verifier member is unsafe")
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in {0, stat.S_IFREG}:
                raise PackagingError("standalone verifier member is not a regular file")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PackagingError(f"standalone verifier CRC verification failed: {bad_member}")
            source = archive.read(entry)
    except PackagingError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
        raise PackagingError(f"invalid standalone verifier: {path}") from exc
    if b"driftproof.standalone-release-verification.v1" not in source:
        raise PackagingError("standalone verifier source lacks its protocol identity")
    return {
        "file": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "member": "__main__.py",
        "member_sha256": hashlib.sha256(source).hexdigest(),
        "crc_verified": True,
    }


def _run_standalone_verifier(directory: Path) -> dict[str, object]:
    verifier = directory / "verify-release.pyz"
    with tempfile.TemporaryDirectory(prefix="driftproof-standalone-cwd-") as raw_cwd:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = ""
        environment["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            [sys.executable, str(verifier), str(directory)],
            cwd=raw_cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PackagingError("standalone verifier did not emit exactly one JSON object") from exc
    if not isinstance(payload, dict):
        raise PackagingError("standalone verifier response must be a JSON object")
    if completed.stderr.strip():
        raise PackagingError("standalone verifier wrote unexpected stderr output")
    if completed.returncode != 0 or payload.get("verified") is not True:
        raise PackagingError(
            f"standalone verifier rejected the generated release: {payload.get('detail')}"
        )
    if payload.get("protocol") != "driftproof.standalone-release-verification.v1":
        raise PackagingError("standalone verifier returned an unexpected protocol")
    return payload


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


def _require_safety_boundary(payload: dict[str, object], label: str) -> None:
    if payload.get("human_approval_required") is not True:
        raise PackagingError(f"{label} weakened the human approval boundary")
    if payload.get("consequential_action_taken") is not False:
        raise PackagingError(f"{label} claims a consequential action")


def _verify_submission_packet(
    directory: Path,
    submission: dict[str, object],
) -> dict[str, object]:
    trajectories = _load_json_object(directory / "AGENT_TRAJECTORIES.json")
    trace_index = _load_json_object(directory / "TRACE_INDEX.json")
    claims = _load_json_object(directory / "CLAIM_LEDGER.json")
    rubric = _load_json_object(directory / "RUBRIC_MAP.json")
    for label, payload in (
        ("submission manifest", submission),
        ("agent trajectories", trajectories),
        ("trace index", trace_index),
        ("claim ledger", claims),
        ("rubric map", rubric),
    ):
        _require_safety_boundary(payload, label)

    if trajectories.get("protocol") != "driftproof.agent-trajectories.v1":
        raise PackagingError("unexpected agent trajectory protocol")
    declared = trajectories.get("declared_workflow_agents")
    observed = trajectories.get("observed_workflow_agents")
    if (
        trajectories.get("coverage_complete") is not True
        or not isinstance(declared, list)
        or not declared
        or declared != observed
        or any(not isinstance(agent, str) or not agent for agent in declared)
    ):
        raise PackagingError("agent trajectory coverage is incomplete or inconsistent")

    if trace_index.get("protocol") != "driftproof.trace-index.v1":
        raise PackagingError("unexpected trace-index protocol")
    representative = trace_index.get("representative_packet")
    trajectories_path = directory / "AGENT_TRAJECTORIES.json"
    if (
        trace_index.get("coverage_complete") is not True
        or not isinstance(representative, dict)
        or representative.get("path") != "submission/AGENT_TRAJECTORIES.json"
        or representative.get("bytes") != trajectories_path.stat().st_size
        or representative.get("sha256") != _sha256(trajectories_path)
    ):
        raise PackagingError("trace index does not bind the representative trajectory packet")

    claim_rows = claims.get("claims")
    if (
        claims.get("protocol") != "driftproof.claim-ledger.v1"
        or claims.get("all_claims_supported") is not True
        or not isinstance(claim_rows, list)
        or claims.get("claim_count") != len(claim_rows)
        or not claim_rows
    ):
        raise PackagingError("claim ledger is incomplete or contains unsupported claims")

    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise PackagingError("rubric map lacks criteria")
    points = 0
    for criterion in criteria:
        if not isinstance(criterion, dict) or not isinstance(criterion.get("points"), int):
            raise PackagingError("rubric criterion is malformed")
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
        or claim_binding.get("sha256") != _sha256(claim_path)
    ):
        raise PackagingError("rubric map is not bound to the 100-point claim ledger")

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
        raise PackagingError("submission manifest entry points are incomplete or unexpected")
    for name, source in _DELIVERY_SOURCES.items():
        if source == "submission/manifest.json":
            continue
        metadata = generated_files.get(Path(source).name)
        path = directory / name
        if (
            not isinstance(metadata, dict)
            or metadata.get("bytes") != path.stat().st_size
            or metadata.get("sha256") != _sha256(path)
        ):
            raise PackagingError(f"submission manifest does not bind generated file: {name}")

    return {
        "verified": True,
        "workflow_agents": declared,
        "claim_count": len(claim_rows),
        "rubric_points": points,
        "generated_files": len(generated_files),
    }


def _media_file_records(directory: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for name in _MEDIA_FILES:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise PackagingError(f"video delivery file is missing or unsafe: {name}")
        records[name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return records


def _verify_media_delivery_metadata(
    directory: Path,
    media_metadata: object,
    *,
    commit: str,
) -> dict[str, object] | None:
    observed_media = {
        name
        for name in _MEDIA_FILES
        if (directory / name).exists() or (directory / name).is_symlink()
    }
    if media_metadata is None:
        if observed_media:
            raise PackagingError("release contains video files without video-delivery metadata")
        return None
    if not isinstance(media_metadata, dict) or media_metadata.get("required") is not True:
        raise PackagingError("video-delivery metadata is malformed")
    if observed_media != set(_MEDIA_FILES):
        raise PackagingError(
            f"video delivery file set mismatch; missing={sorted(set(_MEDIA_FILES) - observed_media)}"
        )
    records = _media_file_records(directory)
    declared_files = media_metadata.get("files")
    if not isinstance(declared_files, dict) or set(declared_files) != set(_MEDIA_FILES):
        raise PackagingError("video-delivery metadata file set is incomplete")
    for name, metadata in records.items():
        if declared_files.get(name) != metadata:
            raise PackagingError(f"video-delivery metadata mismatch: {name}")

    verification_path = directory / "driftproof-demo-verification.json"
    source_manifest_path = directory / "driftproof-demo-source-manifest.json"
    verification = _load_json_object(verification_path)
    source_manifest = _load_json_object(source_manifest_path)
    if (
        verification.get("protocol") != "driftproof.demo-video-verification.v1"
        or verification.get("verified") is not True
        or verification.get("source_commit") != commit
        or verification.get("human_approval_required") is not True
        or verification.get("consequential_action_taken") is not False
    ):
        raise PackagingError("video verification receipt is invalid or commit-mismatched")
    if (
        source_manifest.get("protocol") != "driftproof.demo-video-source.v1"
        or source_manifest.get("source_commit") != commit
        or source_manifest.get("human_approval_required") is not True
        or source_manifest.get("consequential_action_taken") is not False
    ):
        raise PackagingError("video source manifest is invalid or commit-mismatched")

    video = verification.get("video")
    video_path = directory / "driftproof-demo.mp4"
    if (
        not isinstance(video, dict)
        or video.get("file") != "driftproof-demo.mp4"
        or video.get("bytes") != video_path.stat().st_size
        or video.get("sha256") != _sha256(video_path)
        or video.get("complete_decode") is not True
        or video.get("duration_seconds") is None
    ):
        raise PackagingError("video verification receipt does not bind the MP4")
    duration = video.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not 90 <= float(duration) < 300:
        raise PackagingError("video verification receipt has an invalid duration")
    if (
        video.get("width") != 1920
        or video.get("height") != 1080
        or video.get("video_codec") != "h264"
        or video.get("pixel_format") != "yuv420p"
        or video.get("audio_codec") != "aac"
        or video.get("audio_sample_rate") != 48000
    ):
        raise PackagingError("video verification receipt has unexpected media properties")

    verification_assets = verification.get("assets")
    expected_assets = set(_MEDIA_FILES) - {
        "driftproof-demo.mp4",
        "driftproof-demo-verification.json",
    }
    if not isinstance(verification_assets, dict) or set(verification_assets) != expected_assets:
        raise PackagingError("video verification receipt asset set is incomplete")
    for name in expected_assets:
        if verification_assets.get(name) != records[name]:
            raise PackagingError(f"video verification receipt does not bind {name}")

    source_scripts = media_metadata.get("source_scripts")
    if not isinstance(source_scripts, dict) or set(source_scripts) != set(_MEDIA_SOURCE_SCRIPTS):
        raise PackagingError("video-delivery source-script metadata is incomplete")
    for field, relative in (
        ("renderer", "scripts/render_demo_video.py"),
        ("verifier", "scripts/verify_demo_video.py"),
    ):
        source = source_manifest.get(field)
        if (
            not isinstance(source, dict)
            or source.get("path") != relative
            or source.get("sha256") != source_scripts.get(relative)
        ):
            raise PackagingError(f"video source manifest does not bind the {field}")

    if media_metadata.get("verification") != verification:
        raise PackagingError("release manifest and video verification receipt differ")
    if media_metadata.get("archive_directory") != "media":
        raise PackagingError("video archive directory is unexpected")
    return {
        "verified": True,
        "source_commit": commit,
        "files": records,
        "duration_seconds": float(duration),
        "source_scripts": source_scripts,
        "verification_sha256": _sha256(verification_path),
        "source_manifest_sha256": _sha256(source_manifest_path),
    }


def _prepare_media_delivery(
    root: Path,
    media_directory: Path,
    *,
    commit: str,
) -> tuple[dict[str, object], dict[str, Path]]:
    media_input = media_directory.expanduser()
    if media_input.is_symlink():
        raise PackagingError("video delivery directory may not be a symlink")
    media_directory = media_input.resolve(strict=True)
    if not media_directory.is_dir():
        raise PackagingError("video delivery directory is missing or unsafe")
    observed = {path.name for path in media_directory.iterdir()}
    if observed != set(_MEDIA_FILES):
        raise PackagingError(
            f"video delivery source file set mismatch; missing={sorted(set(_MEDIA_FILES) - observed)}, "
            f"unexpected={sorted(observed - set(_MEDIA_FILES))}"
        )
    try:
        verification = verify_video_delivery(
            media_directory,
            source_root=root,
            expected_commit=commit,
        )
    except Exception as exc:
        raise PackagingError(f"video delivery verification failed: {exc}") from exc
    paths = {name: media_directory / name for name in _MEDIA_FILES}
    records = _media_file_records(media_directory)
    source_scripts: dict[str, str] = {}
    for relative in _MEDIA_SOURCE_SCRIPTS:
        payload = _run(root, "git", "show", f"HEAD:{relative}", text=False)
        assert isinstance(payload, bytes)
        source_scripts[relative] = hashlib.sha256(payload).hexdigest()
    metadata: dict[str, object] = {
        "required": True,
        "archive_directory": "media",
        "files": records,
        "source_scripts": source_scripts,
        "verification": verification,
    }
    return metadata, paths


def _copy_media_to_release(output: Path, paths: dict[str, Path]) -> None:
    for name in _MEDIA_FILES:
        source = paths[name]
        destination = output / name
        if destination.exists() or destination.is_symlink():
            raise PackagingError(f"video release destination already exists: {destination}")
        shutil.copyfile(source, destination)
        if _sha256(destination) != _sha256(source):
            raise PackagingError(f"video release copy verification failed: {name}")


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
        "verify-release.pyz",
        *_DELIVERY_SOURCES,
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
    submission_packet = _verify_submission_packet(directory, submission)
    video_delivery = _verify_media_delivery_metadata(
        directory,
        manifest.get("video_delivery"),
        commit=commit,
    )

    verifier_path = directory / "verify-release.pyz"
    verified_verifier = _verify_standalone_verifier(verifier_path)
    verifier_metadata = manifest.get("standalone_verifier")
    if (
        not isinstance(verifier_metadata, dict)
        or verifier_metadata.get("file") != "verify-release.pyz"
        or verifier_metadata.get("bytes") != verifier_path.stat().st_size
        or verifier_metadata.get("sha256") != _sha256(verifier_path)
        or verifier_metadata.get("member") != verified_verifier.get("member")
        or verifier_metadata.get("member_sha256") != verified_verifier.get("member_sha256")
        or verifier_metadata.get("source") != "scripts/standalone_release_verifier.py"
        or verifier_metadata.get("source_sha256") != verified_verifier.get("member_sha256")
        or verifier_metadata.get("crc_verified") is not True
    ):
        raise PackagingError("release manifest does not bind the standalone verifier")

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
    if not isinstance(delivery_files, list) or len(delivery_files) != len(_DELIVERY_SOURCES):
        raise PackagingError(
            f"release manifest must describe {len(_DELIVERY_SOURCES)} delivery entry points"
        )
    expected_delivery = _DELIVERY_SOURCES
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
            for delivery_name, relative in _DELIVERY_SOURCES.items():
                extracted_path = extracted[label] / relative
                if not extracted_path.is_file() or _sha256(extracted_path) != _sha256(
                    directory / delivery_name
                ):
                    raise PackagingError(
                        f"{label} archive delivery entry differs from the release root: {relative}"
                    )

        verifier_source = str(verifier_metadata["source"])
        for label in ("full", "source"):
            extracted_source = extracted[label] / verifier_source
            if (
                not extracted_source.is_file()
                or extracted_source.is_symlink()
                or _sha256(extracted_source) != verifier_metadata.get("source_sha256")
            ):
                raise PackagingError(
                    f"{label} archive does not bind the standalone verifier source"
                )

        if video_delivery is not None:
            for label in _MEDIA_ARCHIVE_LABELS:
                for media_name in _MEDIA_FILES:
                    archived_media = extracted[label] / "media" / media_name
                    root_media = directory / media_name
                    if (
                        archived_media.is_symlink()
                        or not archived_media.is_file()
                        or _sha256(archived_media) != _sha256(root_media)
                    ):
                        raise PackagingError(
                            f"{label} archive video delivery differs from the release root: {media_name}"
                        )
            unexpected_source_media = [
                name for name in _MEDIA_FILES if (extracted["source"] / "media" / name).exists()
            ]
            if unexpected_source_media:
                raise PackagingError(
                    "source archive unexpectedly contains generated video delivery: "
                    + ", ".join(sorted(unexpected_source_media))
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

    standalone_verification = _run_standalone_verifier(directory)
    if standalone_verification.get("commit") != commit:
        raise PackagingError("standalone verifier returned a different release commit")

    return {
        "schema_version": 1,
        "protocol": "driftproof.release-verification.v1",
        "verified": True,
        "commit": commit,
        "tree": manifest.get("tree"),
        "files": len(records),
        "archives": verified_assets,
        "standalone_verifier": verified_verifier,
        "standalone_verification": standalone_verification,
        "submission_packet": submission_packet,
        "video_delivery": video_delivery,
        "sha256sums_sha256": _sha256(directory / "SHA256SUMS"),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def package(
    root: Path,
    output: Path,
    media_directory: Path | None = None,
) -> dict[str, object]:
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

    video_delivery: dict[str, object] | None = None
    media_paths: dict[str, Path] = {}
    if media_directory is not None:
        video_delivery, media_paths = _prepare_media_delivery(
            root,
            media_directory,
            commit=head,
        )

    _prepare_output_directory(output)
    standalone_verifier = _write_standalone_verifier(root, output / "verify-release.pyz")
    if video_delivery is not None:
        _copy_media_to_release(output, media_paths)

    with tempfile.TemporaryDirectory(prefix="mergeproof-release-") as raw_temp:
        temp = Path(raw_temp)
        full_stage = temp / "full"
        source_stage = temp / "source"
        evidence_stage = temp / "evidence"
        full_members = _materialize_commit(root, tracked, full_stage)
        source_members = _materialize_commit(root, source_paths, source_stage)
        evidence_members = _materialize_commit(root, evidence_paths, evidence_stage)
        if video_delivery is not None:
            media_members = [Member(f"media/{name}", media_paths[name]) for name in _MEDIA_FILES]
            full_members.extend(media_members)
            evidence_members.extend(media_members)

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
            missing_delivery = sorted(
                relative
                for relative in _DELIVERY_SOURCES.values()
                if not (extraction_root / relative).is_file()
            )
            if missing_delivery:
                raise PackagingError(
                    f"{label} archive is missing submission delivery files: {missing_delivery}"
                )

        delivery_payloads: dict[str, bytes] = {}
        delivery_files: list[dict[str, object]] = []
        for name, source in _DELIVERY_SOURCES.items():
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
            "standalone_verifier": standalone_verifier,
            "video_delivery": video_delivery,
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
    parser.add_argument(
        "--media-directory",
        type=Path,
        help=(
            "Verified solution-video delivery directory to include at the release root "
            "and in the full/evidence archives."
        ),
    )
    args = parser.parse_args()
    manifest = package(args.root, args.output, media_directory=args.media_directory)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
