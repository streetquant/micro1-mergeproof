from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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
    trajectories = {
        "schema_version": 1,
        "protocol": "driftproof.agent-trajectories.v1",
        "coverage_complete": True,
        "declared_workflow_agents": ["baseline_reviewer", "contract_clarifier"],
        "observed_workflow_agents": ["baseline_reviewer", "contract_clarifier"],
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    trajectories_path = repo / "submission/AGENT_TRAJECTORIES.json"
    trajectories_path.write_text(
        json.dumps(trajectories, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    trace_index = {
        "schema_version": 1,
        "protocol": "driftproof.trace-index.v1",
        "coverage_complete": True,
        "representative_packet": {
            "path": "submission/AGENT_TRAJECTORIES.json",
            "bytes": trajectories_path.stat().st_size,
            "sha256": PACKAGING._sha256(trajectories_path),
        },
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    (repo / "submission/TRACE_INDEX.json").write_text(
        json.dumps(trace_index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    claim_ledger = {
        "schema_version": 1,
        "protocol": "driftproof.claim-ledger.v1",
        "claim_count": 1,
        "claims": [{"id": "fixture", "status": "supported"}],
        "all_claims_supported": True,
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    claim_path = repo / "submission/CLAIM_LEDGER.json"
    claim_path.write_text(
        json.dumps(claim_ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rubric = {
        "schema_version": 1,
        "protocol": "driftproof.rubric-map.v1",
        "total_points": 100,
        "criteria": [{"criterion": "fixture", "points": 100}],
        "claim_ledger": {
            "path": "submission/CLAIM_LEDGER.json",
            "bytes": claim_path.stat().st_size,
            "sha256": PACKAGING._sha256(claim_path),
        },
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    (repo / "submission/RUBRIC_MAP.json").write_text(
        json.dumps(rubric, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (repo / "submission/JUDGE_CHECKLIST.md").write_text(
        "# Judge checklist\n\nFixture checklist.\n", encoding="utf-8"
    )
    generated_files = {
        Path(source).name: {
            "bytes": (repo / source).stat().st_size,
            "sha256": PACKAGING._sha256(repo / source),
        }
        for source in PACKAGING._DELIVERY_SOURCES.values()
        if source != "submission/manifest.json"
    }
    submission_manifest = {
        "schema_version": 1,
        "protocol": "driftproof.submission-manifest.v1",
        "entry_points": {
            "human": "submission/START_HERE.md",
            "browser": "submission/START_HERE.html",
            "machine": "submission/manifest.json",
            "judge_checklist": "submission/JUDGE_CHECKLIST.md",
            "claim_ledger": "submission/CLAIM_LEDGER.json",
            "rubric_map": "submission/RUBRIC_MAP.json",
            "agent_trajectories": "submission/AGENT_TRAJECTORIES.json",
            "trace_index": "submission/TRACE_INDEX.json",
        },
        "generated_files": generated_files,
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    (repo / "submission/manifest.json").write_text(
        json.dumps(submission_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts/standalone_release_verifier.py").write_bytes(
        (ROOT / "scripts/standalone_release_verifier.py").read_bytes()
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
    assert "submission/AGENT_TRAJECTORIES.json" in str(exc.value)
    assert "submission/CLAIM_LEDGER.json" in str(exc.value)
    assert "reviews/2026-08-31-round-6-response-binding/qualification.json" in str(exc.value)


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
    assert PACKAGING._is_evidence("submission/AGENT_TRAJECTORIES.json")
    assert PACKAGING._is_evidence("submission/CLAIM_LEDGER.json")
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
    verifier = first_output / "verify-release.pyz"
    with zipfile.ZipFile(verifier) as archive:
        assert archive.namelist() == ["__main__.py"]
        assert archive.testzip() is None
    outside = tmp_path / "outside-repository"
    outside.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = ""
    environment["PYTHONNOUSERSITE"] = "1"
    standalone = subprocess.run(
        [sys.executable, str(verifier), str(first_output)],
        cwd=outside,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    standalone_payload = json.loads(standalone.stdout)
    assert standalone.stderr == ""
    assert standalone_payload["protocol"] == ("driftproof.standalone-release-verification.v1")
    assert standalone_payload["verified"] is True
    assert standalone_payload["commit"] == first["manifest"]["commit"]
    assert (first_output / "START_HERE.md").read_text(encoding="utf-8") == "# Start here\n"
    for name, source in PACKAGING._DELIVERY_SOURCES.items():
        assert (first_output / name).read_bytes() == (repo / source).read_bytes()

    start_here = first_output / "START_HERE.md"
    start_here.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(PackagingError, match="checksum mismatch"):
        PACKAGING.verify_release_directory(first_output)


def test_release_verifier_rejects_rehashed_trajectory_substitution(tmp_path: Path) -> None:
    repo = initialized_release_repo(tmp_path)
    output = tmp_path / "release"
    PACKAGING.package(repo, output)

    trajectories = output / "AGENT_TRAJECTORIES.json"
    payload = json.loads(trajectories.read_text(encoding="utf-8"))
    payload["declared_workflow_agents"].append("substituted_agent")
    trajectories.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksum_path = output / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    replacement = f"{PACKAGING._sha256(trajectories)}  AGENT_TRAJECTORIES.json"
    checksum_path.write_text(
        "\n".join(
            replacement if line.endswith("  AGENT_TRAJECTORIES.json") else line for line in lines
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PackagingError, match=r"trajectory|trace index"):
        PACKAGING.verify_release_directory(output)

    outside = tmp_path / "outside-repository"
    outside.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = ""
    environment["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(output / "verify-release.pyz"), str(output)],
        cwd=outside,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 30
    assert completed.stderr == ""
    assert payload["verified"] is False
    assert payload["error_code"] == "release_invalid"
    assert "trajectory" in payload["detail"] or "trace index" in payload["detail"]


def test_standalone_verifier_rejects_rehashed_source_substitution(tmp_path: Path) -> None:
    repo = initialized_release_repo(tmp_path)
    output = tmp_path / "release"
    PACKAGING.package(repo, output)

    verifier = output / "verify-release.pyz"
    with zipfile.ZipFile(verifier) as archive:
        replacement_source = archive.read("__main__.py") + b"\n# substituted verifier\n"
    with zipfile.ZipFile(
        verifier,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(PACKAGING._zip_info("__main__.py", 0o755), replacement_source)

    manifest_path = output / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = manifest["standalone_verifier"]
    metadata["bytes"] = verifier.stat().st_size
    metadata["sha256"] = PACKAGING._sha256(verifier)
    metadata["member_sha256"] = hashlib.sha256(replacement_source).hexdigest()
    metadata["source_sha256"] = metadata["member_sha256"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    checksum_path = output / "SHA256SUMS"
    replacements = {
        "verify-release.pyz": PACKAGING._sha256(verifier),
        "release-manifest.json": PACKAGING._sha256(manifest_path),
    }
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    checksum_path.write_text(
        "\n".join(
            f"{replacements.get(name, digest)}  {name}"
            for digest, name in (line.split("  ", 1) for line in lines)
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PackagingError, match="archive does not bind"):
        PACKAGING.verify_release_directory(output)

    outside = tmp_path / "outside-repository"
    outside.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = ""
    environment["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(verifier), str(output)],
        cwd=outside,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 30
    assert completed.stderr == ""
    assert payload["verified"] is False
    assert payload["error_code"] == "release_invalid"
    assert "archive does not bind" in payload["detail"]


def _fake_media_delivery(
    repo: Path,
    destination: Path,
) -> tuple[Path, dict[str, object]]:
    for relative in PACKAGING._MEDIA_SOURCE_SCRIPTS:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture for {relative}\n", encoding="utf-8")
    git(repo, "add", "scripts")
    git(repo, "commit", "-q", "-m", "video source fixtures")
    git(repo, "push", "-q", "origin", "main")
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    destination.mkdir()
    video_path = destination / "driftproof-demo.mp4"
    transcript_path = destination / "driftproof-demo-transcript.md"
    storyboard_path = destination / "driftproof-demo-storyboard.json"
    source_manifest_path = destination / "driftproof-demo-source-manifest.json"
    durations_path = destination / "driftproof-demo-scene-durations.json"
    verification_path = destination / "driftproof-demo-verification.json"

    video_path.write_bytes(b"fixture h264/aac delivery")
    transcript_path.write_text("# DriftProof solution video transcript\n", encoding="utf-8")
    storyboard_path.write_text('{"fixture": true}\n', encoding="utf-8")
    durations_path.write_text('{"fixture": true}\n', encoding="utf-8")
    script_hashes = {
        relative: PACKAGING._sha256(repo / relative) for relative in PACKAGING._MEDIA_SOURCE_SCRIPTS
    }
    source_manifest = {
        "schema_version": 1,
        "protocol": "driftproof.demo-video-source.v1",
        "source_commit": commit,
        "renderer": {
            "path": "scripts/render_demo_video.py",
            "sha256": script_hashes["scripts/render_demo_video.py"],
        },
        "verifier": {
            "path": "scripts/verify_demo_video.py",
            "sha256": script_hashes["scripts/verify_demo_video.py"],
        },
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    source_manifest_path.write_text(
        json.dumps(source_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    asset_paths = {
        transcript_path.name: transcript_path,
        storyboard_path.name: storyboard_path,
        source_manifest_path.name: source_manifest_path,
        durations_path.name: durations_path,
    }
    verification: dict[str, object] = {
        "schema_version": 1,
        "protocol": "driftproof.demo-video-verification.v1",
        "verified": True,
        "source_commit": commit,
        "video": {
            "file": video_path.name,
            "bytes": video_path.stat().st_size,
            "sha256": PACKAGING._sha256(video_path),
            "duration_seconds": 120.0,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "pixel_format": "yuv420p",
            "audio_codec": "aac",
            "audio_sample_rate": 48000,
            "complete_decode": True,
        },
        "assets": {
            name: {"bytes": path.stat().st_size, "sha256": PACKAGING._sha256(path)}
            for name, path in asset_paths.items()
        },
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    verification_path.write_text(
        json.dumps(verification, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination, verification


def test_release_with_video_is_bound_at_root_and_inside_archives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = initialized_release_repo(tmp_path)
    media, verification = _fake_media_delivery(repo, tmp_path / "media")
    monkeypatch.setattr(PACKAGING, "verify_video_delivery", lambda *args, **kwargs: verification)
    output = tmp_path / "release-with-video"

    result = PACKAGING.package(repo, output, media_directory=media)

    assert result["verification"]["video_delivery"]["verified"] is True
    assert result["manifest"]["video_delivery"]["required"] is True
    for name in PACKAGING._MEDIA_FILES:
        assert (output / name).read_bytes() == (media / name).read_bytes()
    for asset in result["manifest"]["assets"]:
        label = asset["file"].split("-final-", 1)[1].split("-", 1)[0]
        with zipfile.ZipFile(output / asset["file"]) as archive:
            top = asset["top_level"]
            for name in PACKAGING._MEDIA_FILES:
                member = f"{top}/media/{name}"
                if label in PACKAGING._MEDIA_ARCHIVE_LABELS:
                    assert archive.read(member) == (media / name).read_bytes()
                else:
                    assert member not in archive.namelist()
    standalone = subprocess.run(
        [sys.executable, str(output / "verify-release.pyz"), str(output)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads(standalone.stdout)
    assert payload["video_delivery"]["verified"] is True
    assert payload["video_delivery"]["source_commit"] == result["manifest"]["commit"]


def test_release_rejects_symlinked_video_delivery(
    tmp_path: Path,
) -> None:
    repo = initialized_release_repo(tmp_path)
    target = tmp_path / "real-media"
    target.mkdir()
    link = tmp_path / "media-link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(PackagingError, match="may not be a symlink"):
        PACKAGING.package(repo, tmp_path / "release", media_directory=link)
