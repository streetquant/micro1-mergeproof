from __future__ import annotations

from pathlib import Path

import pytest

from scripts import render_demo_video
from scripts.render_demo_video import _svg, _transcript, build_video_plan, video_readiness
from scripts.verify_demo_video import VideoVerificationError, verify_video_delivery

ROOT = Path(__file__).resolve().parents[1]


def test_video_plan_is_evidence_derived_and_conservative() -> None:
    plan = build_video_plan(ROOT)
    metrics = plan["metrics"]

    assert plan["protocol"] == "driftproof.demo-video-plan.v1"
    assert len(plan["scenes"]) == 9
    assert metrics["cases"] == 24
    assert metrics["safe_total"] == 12
    assert metrics["unsafe_total"] == 12
    assert metrics["baseline_macro_f1"] == pytest.approx(1 / 3)
    assert metrics["advanced_macro_f1"] == pytest.approx(0.6812144212523719)
    assert metrics["baseline_accuracy"] == 0.5
    assert metrics["advanced_accuracy"] == pytest.approx(17 / 24)
    assert metrics["baseline_escape"] == 1.0
    assert metrics["advanced_escape"] == 0.0
    assert metrics["safe_approved"] == 5
    assert metrics["unsafe_blocked"] == 12
    assert metrics["human_reviews"] == 7
    assert plan["human_approval_required"] is True
    assert plan["consequential_action_taken"] is False


def test_transcript_and_slides_expose_metrics_without_unsupported_claims() -> None:
    plan = build_video_plan(ROOT)
    transcript = _transcript(plan)

    assert transcript.startswith("# DriftProof solution video transcript\n")
    assert "0.333" in transcript
    assert "0.681" in transcript
    assert "50.0%" in transcript
    assert "70.8%" in transcript
    assert "100.0%" in transcript
    assert "0.0%" in transcript
    assert "not universal correctness" in transcript.lower()
    assert "hash-bound bundle" in transcript.lower()
    assert "signed bundle" not in transcript.lower()
    assert "guaranteed rank 1" not in transcript.lower()
    assert "100% accuracy" not in transcript.lower()

    for index, scene in enumerate(plan["scenes"], 1):
        assert scene["index"] == index
        svg = _svg(scene)
        assert svg.startswith("<svg")
        assert 'width="1920"' in svg
        assert 'height="1080"' in svg
        assert str(scene["title"]).split()[0] in svg


def test_video_verifier_fails_closed_on_incomplete_delivery(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    delivery.mkdir()

    with pytest.raises(VideoVerificationError, match="missing or unsafe"):
        verify_video_delivery(delivery)


def test_video_readiness_is_non_mutating_and_machine_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = tmp_path / "font.ttf"
    bold = tmp_path / "font-bold.ttf"
    font.write_bytes(b"font")
    bold.write_bytes(b"bold")
    commit = "a" * 40

    monkeypatch.setattr(render_demo_video, "_FONT", font)
    monkeypatch.setattr(render_demo_video, "_FONT_BOLD", bold)
    monkeypatch.setattr(
        render_demo_video,
        "_first_available",
        lambda *names: f"/usr/bin/{names[0]}",
    )

    def fake_git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return commit
        if args == ("status", "--porcelain=v1"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(render_demo_video, "_git", fake_git)
    payload = video_readiness(tmp_path, expected_commit=commit)

    assert payload["protocol"] == "driftproof.demo-video-readiness.v1"
    assert payload["ready"] is True
    assert payload["worktree_clean"] is True
    assert payload["source_commit"] == commit
    assert payload["problems"] == []
    assert payload["candidate_code_executed"] is False
    assert payload["files_created"] is False
    assert payload["render_argv"][-1] == commit
    assert set(tmp_path.iterdir()) == {font, bold}


def test_video_readiness_rejects_dirty_source_without_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font = tmp_path / "font.ttf"
    bold = tmp_path / "font-bold.ttf"
    font.write_bytes(b"font")
    bold.write_bytes(b"bold")
    commit = "b" * 40

    monkeypatch.setattr(render_demo_video, "_FONT", font)
    monkeypatch.setattr(render_demo_video, "_FONT_BOLD", bold)
    monkeypatch.setattr(
        render_demo_video,
        "_first_available",
        lambda *names: f"/usr/bin/{names[0]}",
    )

    def fake_git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return commit
        if args == ("status", "--porcelain=v1"):
            return " M evidence.json"
        raise AssertionError(args)

    monkeypatch.setattr(render_demo_video, "_git", fake_git)
    payload = video_readiness(tmp_path, expected_commit=commit)

    assert payload["ready"] is False
    assert payload["worktree_clean"] is False
    assert "video source worktree must be clean" in payload["problems"]
    assert "--allow-dirty" not in (ROOT / "scripts/render_demo_video.py").read_text(
        encoding="utf-8"
    )
