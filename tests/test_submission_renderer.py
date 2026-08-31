from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.render_submission import (
    README_END,
    README_START,
    SubmissionRenderError,
    check_submission,
    write_submission,
)

ROOT = Path(__file__).resolve().parents[1]


def _copy_source_tree(destination: Path) -> None:
    files = [
        "README.md",
        "results/driftproof-comparison/comparison.json",
        "benchmark_dbt/manifest.json",
        "schemas/manifest.json",
        "results/baseline-replay-gpt-oss-20b/replay-verification.json",
        "reviews/recovery-promotion/qualification.json",
        "reviews/replay-nonmutating/qualification.json",
        "reviews/2026-08-31-round-1-human-judge/qualification.json",
        "reviews/2026-08-31-round-2-agent-sdk/qualification.json",
    ]
    for relative in files:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def test_committed_submission_is_bound_to_authoritative_metrics() -> None:
    receipt = check_submission(ROOT)
    manifest = json.loads((ROOT / "submission/manifest.json").read_text(encoding="utf-8"))
    metrics = manifest["metrics"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert receipt["verified"] is True
    assert receipt["readme_metrics_bound"] is True
    assert metrics["baseline_macro_f1"] == pytest.approx(1 / 3)
    assert metrics["advanced_macro_f1"] == pytest.approx(0.6812144212523719)
    assert metrics["advanced_accuracy"] == pytest.approx(17 / 24)
    assert metrics["advanced_unsafe_escape_rate"] == 0.0
    assert metrics["advanced_safe_approved"] == 5
    assert metrics["advanced_human_reviews"] == 7
    assert README_START in readme and README_END in readme
    assert "**1.000**" not in readme.split(README_END, 1)[0]
    assert manifest["human_approval_required"] is True
    assert manifest["consequential_action_taken"] is False


def test_submission_renderer_is_deterministic_and_detects_drift(tmp_path: Path) -> None:
    _copy_source_tree(tmp_path)

    first = write_submission(tmp_path)
    first_payloads = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in (tmp_path / "submission").iterdir()
        if path.is_file()
    }
    second = write_submission(tmp_path)
    second_payloads = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in (tmp_path / "submission").iterdir()
        if path.is_file()
    }

    assert first == second
    assert first_payloads == second_payloads
    assert check_submission(tmp_path)["verified"] is True

    start_here = tmp_path / "submission/START_HERE.md"
    start_here.write_text(start_here.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")
    with pytest.raises(SubmissionRenderError, match="differ from committed evidence"):
        check_submission(tmp_path)


def test_submission_check_fails_when_comparison_changes_without_regeneration(
    tmp_path: Path,
) -> None:
    _copy_source_tree(tmp_path)
    write_submission(tmp_path)
    comparison_path = tmp_path / "results/driftproof-comparison/comparison.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    comparison["advanced"]["accuracy"] = 0.75
    comparison_path.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n")

    with pytest.raises(SubmissionRenderError, match="differ from committed evidence"):
        check_submission(tmp_path)
