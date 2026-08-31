from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY_RELEASE_SCRIPT = ROOT / "scripts" / "verify_release.py"


def load_packaging_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "package_final_release.py"
    spec = importlib.util.spec_from_file_location("mergeproof_release_packaging", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PACKAGING = load_packaging_module()
PackagingError = PACKAGING.PackagingError
Member = PACKAGING.Member


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "MergeProof Packaging Tests")
    git(repo, "config", "user.email", "mergeproof-packaging@example.invalid")
    (repo / "README.md").write_text("release fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "baseline")
    return repo


def initialized_release_repo(tmp_path: Path) -> Path:
    repo = initialized_repo(tmp_path)
    git(repo, "branch", "-M", "main")
    for relative in PACKAGING._REQUIRED_EVIDENCE_ARTIFACTS:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: object = {"fixture": relative}
        if relative == "submission/manifest.json":
            payload = {
                "schema_version": 1,
                "protocol": "driftproof.submission-manifest.v1",
                "human_approval_required": True,
                "consequential_action_taken": False,
            }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (repo / "submission/START_HERE.md").write_text("# Start here\n", encoding="utf-8")
    (repo / "submission/START_HERE.html").write_text(
        "<!doctype html><title>Start here</title>\n", encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "release evidence")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def test_safe_extract_round_trip_uses_a_fresh_root(tmp_path: Path) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("verified payload\n", encoding="utf-8")
    archive = tmp_path / "release.zip"
    PACKAGING._write_zip(
        archive,
        prefix="mergeproof-test",
        members=[Member("payload.txt", source)],
    )

    extracted = PACKAGING.safe_extract(archive, tmp_path / "extract")

    assert extracted.name == "mergeproof-test"
    assert (extracted / "payload.txt").read_text(encoding="utf-8") == "verified payload\n"


def test_safe_extract_refuses_nonempty_or_symlink_destination(tmp_path: Path) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("verified payload\n", encoding="utf-8")
    archive = tmp_path / "release.zip"
    PACKAGING._write_zip(
        archive,
        prefix="mergeproof-test",
        members=[Member("payload.txt", source)],
    )
    destination = tmp_path / "extract"
    destination.mkdir()
    sentinel = destination / "keep-me.txt"
    sentinel.write_text("important\n", encoding="utf-8")

    with pytest.raises(PackagingError, match="must be empty"):
        PACKAGING.safe_extract(archive, destination)
    assert sentinel.read_text(encoding="utf-8") == "important\n"

    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(PackagingError, match="may not be a symlink"):
        PACKAGING.safe_extract(archive, link)


def test_verify_zip_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("mergeproof-test/../escape.txt", "escape\n")

    with pytest.raises(PackagingError, match="unsafe member"):
        PACKAGING.verify_zip(archive)


def test_output_cleanup_refuses_unrelated_files_without_deleting_them(tmp_path: Path) -> None:
    output = tmp_path / "release"
    output.mkdir()
    unrelated = output / "judge-notes.json"
    unrelated.write_text('{"keep": true}\n', encoding="utf-8")
    owned = output / "release-manifest.json"
    owned.write_text("old\n", encoding="utf-8")

    with pytest.raises(PackagingError, match="contains unrelated entries"):
        PACKAGING._prepare_output_directory(output)

    assert unrelated.read_text(encoding="utf-8") == '{"keep": true}\n'
    assert owned.read_text(encoding="utf-8") == "old\n"


def test_output_cleanup_refuses_owned_symlink(tmp_path: Path) -> None:
    output = tmp_path / "release"
    output.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("important\n", encoding="utf-8")
    (output / "release-manifest.json").symlink_to(target)

    with pytest.raises(PackagingError, match="unsafe owned release output"):
        PACKAGING._prepare_output_directory(output)

    assert target.read_text(encoding="utf-8") == "important\n"


def test_release_requires_a_clean_worktree(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    PACKAGING._require_clean_worktree(repo)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(PackagingError, match="requires a clean worktree"):
        PACKAGING._require_clean_worktree(repo)


def test_required_evidence_preflight_lists_missing_head_artifacts(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    tracked = PACKAGING._tracked_files(repo)

    with pytest.raises(PackagingError, match="required submission evidence is missing") as exc:
        PACKAGING._required_evidence_hashes(repo, tracked)

    assert "results/driftproof-comparison/comparison.json" in str(exc.value)
    assert "submission/manifest.json" in str(exc.value)
    assert "reviews/2026-08-31-round-3-release-delivery/qualification.json" in str(exc.value)


def test_required_evidence_preflight_hashes_actual_head_artifacts(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    for relative in PACKAGING._REQUIRED_EVIDENCE_ARTIFACTS:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "evidence")

    tracked = PACKAGING._tracked_files(repo)
    hashes = PACKAGING._required_evidence_hashes(repo, tracked)

    assert set(hashes) == set(PACKAGING._REQUIRED_EVIDENCE_ARTIFACTS)
    assert all(len(digest) == 64 for digest in hashes.values())
    assert all(PACKAGING._is_evidence(path) for path in hashes)
    assert PACKAGING._is_source("benchmark_dbt/manifest.json")
    assert PACKAGING._is_source("upstream/driftdoctor.lock.json")


def test_release_content_audit_rejects_private_host_paths(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    marker = "/" + "storage" + "/private/workspace"
    (repo / "evidence.json").write_text(f'{{"path": "{marker}"}}\n', encoding="utf-8")
    git(repo, "add", "evidence.json")
    git(repo, "commit", "-q", "-m", "private path")

    with pytest.raises(PackagingError, match="private host paths"):
        PACKAGING._audit_release_content(repo, PACKAGING._tracked_files(repo))


def test_release_content_audit_rejects_credential_shapes(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    token = "ghp" + "_" + "A" * 32
    (repo / "evidence.json").write_text(f'{{"token": "{token}"}}\n', encoding="utf-8")
    git(repo, "add", "evidence.json")
    git(repo, "commit", "-q", "-m", "credential")

    with pytest.raises(PackagingError, match="credential-shaped"):
        PACKAGING._audit_release_content(repo, PACKAGING._tracked_files(repo))


def test_release_content_audit_accepts_portable_content(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    audit = PACKAGING._audit_release_content(repo, PACKAGING._tracked_files(repo))

    assert audit == {
        "schema_version": 1,
        "files_scanned": 1,
        "private_host_path_hits": 0,
        "credential_shape_hits": 0,
        "passed": True,
    }


def test_evidence_selection_contains_reviews_and_submission_entry_points() -> None:
    assert PACKAGING._is_evidence("reviews/2026-08-31-round-2-agent-sdk/qualification.json")
    assert PACKAGING._is_evidence("submission/START_HERE.md")
    assert PACKAGING._is_source("submission/START_HERE.html")
    assert set(PACKAGING._REVIEW_QUALIFICATION_ARTIFACTS).issubset(
        PACKAGING._REQUIRED_EVIDENCE_ARTIFACTS
    )


def test_deterministic_zip_is_crc_verified(tmp_path: Path) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("verified payload\n" * 100, encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    members = [Member("payload.txt", source)]

    PACKAGING._write_zip(first, prefix="mergeproof-test", members=members)
    PACKAGING._write_zip(second, prefix="mergeproof-test", members=members)

    assert first.read_bytes() == second.read_bytes()
    verification = PACKAGING.verify_zip(first)
    assert verification["crc_verified"] is True
    assert verification["members"] == 1


def test_verify_zip_rejects_corrupted_compressed_payload(tmp_path: Path) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("compressible payload\n" * 1_000, encoding="utf-8")
    archive = tmp_path / "corrupt.zip"
    PACKAGING._write_zip(
        archive,
        prefix="mergeproof-test",
        members=[Member("payload.txt", source)],
    )

    with zipfile.ZipFile(archive) as handle:
        info = handle.infolist()[0]
    payload = bytearray(archive.read_bytes())
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    payload[data_offset + max(0, info.compress_size // 2)] ^= 0xFF
    archive.write_bytes(payload)

    with pytest.raises(PackagingError, match=r"CRC|invalid or unreadable"):
        PACKAGING.verify_zip(archive)


def test_complete_release_is_deterministic_and_independently_verifiable(
    tmp_path: Path,
) -> None:
    repo = initialized_release_repo(tmp_path)
    first_output = tmp_path / "first-release"
    second_output = tmp_path / "second-release"

    first = PACKAGING.package(repo, first_output)
    second = PACKAGING.package(repo, second_output)

    assert first["verification"]["verified"] is True
    assert second["verification"]["verified"] is True
    first_files = {
        path.name: path.read_bytes() for path in first_output.iterdir() if path.is_file()
    }
    second_files = {
        path.name: path.read_bytes() for path in second_output.iterdir() if path.is_file()
    }
    assert first_files == second_files
    assert PACKAGING.verify_release_directory(first_output)["verified"] is True
    completed = subprocess.run(
        [sys.executable, str(VERIFY_RELEASE_SCRIPT), str(first_output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert json.loads(completed.stdout)["verified"] is True
    assert (first_output / "START_HERE.md").read_text(encoding="utf-8") == "# Start here\n"

    start_here = first_output / "START_HERE.md"
    start_here.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(PackagingError, match="checksum mismatch"):
        PACKAGING.verify_release_directory(first_output)
