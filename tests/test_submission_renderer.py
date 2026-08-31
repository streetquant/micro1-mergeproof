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
        "CHANGELOG.md",
        "oracle/problem-brief.md",
        "docs/architecture.md",
        "docs/requirements.md",
        "docs/driftdoctor-upstream.md",
        "scripts/reproduce.sh",
        "src/driftproof/demo.py",
        "src/driftproof/runner.py",
        "src/driftproof/reporting.py",
        "upstream/driftdoctor.lock.json",
        "upstream/DriftDoctor-LICENSE",
        "results/driftproof-comparison/comparison.json",
        "results/driftproof-benchmark-validation/summary.json",
        "results/baseline-live-groq-gpt-oss-20b/raw-results.jsonl",
        "results/baseline-replay-gpt-oss-20b/replay-verification.json",
        "results/agent-fallback-live/gate-report.json",
        "results/agent-fallback-replay/gate-report.json",
        "benchmark_dbt/manifest.json",
        "benchmark_dbt/cases.json",
        "schemas/manifest.json",
        "schemas/driftproof/agent-response.schema.json",
        "schemas/driftproof/response-verification.schema.json",
        "fixtures/agent/driftproof-contract-clarifier/8048ba79613d4758495aee5c4e0cc11eed6f2f459b85510d3903512e8c042080.json",
        "reviews/recovery-promotion/qualification.json",
        "reviews/replay-nonmutating/qualification.json",
        "reviews/2026-08-31-round-1-human-judge/qualification.json",
        "reviews/2026-08-31-round-2-agent-sdk/qualification.json",
        "reviews/2026-08-31-round-3-release-delivery/qualification.json",
        "reviews/2026-08-31-round-4-consumer-verifier/qualification.json",
        "reviews/2026-08-31-round-5-installed-demo/qualification.json",
        "reviews/2026-08-31-round-6-response-binding/qualification.json",
    ]
    files.extend(
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "fixtures/replay/groq-gpt-oss-20b").glob("*.json"))
    )
    for relative in files:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def test_committed_submission_is_bound_to_authoritative_metrics() -> None:
    receipt = check_submission(ROOT)
    manifest = json.loads((ROOT / "submission/manifest.json").read_text(encoding="utf-8"))
    trajectories = json.loads(
        (ROOT / "submission/AGENT_TRAJECTORIES.json").read_text(encoding="utf-8")
    )
    claims = json.loads((ROOT / "submission/CLAIM_LEDGER.json").read_text(encoding="utf-8"))
    rubric = json.loads((ROOT / "submission/RUBRIC_MAP.json").read_text(encoding="utf-8"))
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
    assert trajectories["coverage_complete"] is True
    assert trajectories["declared_workflow_agents"] == [
        "baseline_reviewer",
        "contract_clarifier",
    ]
    assert trajectories["agents"]["baseline_reviewer"]["canonical_case_count"] == 24
    assert claims["claim_count"] == 8
    assert claims["all_claims_supported"] is True
    assert rubric["total_points"] == 100
    assert sum(item["points"] for item in rubric["criteria"]) == 100
    assert manifest["entry_points"]["agent_trajectories"] == ("submission/AGENT_TRAJECTORIES.json")
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


def test_submission_check_fails_when_a_trace_source_changes_without_regeneration(
    tmp_path: Path,
) -> None:
    _copy_source_tree(tmp_path)
    write_submission(tmp_path)
    trace_path = tmp_path / "results/baseline-live-groq-gpt-oss-20b/raw-results.jsonl"
    rows = trace_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["summary"] = "tampered trace summary"
    rows[0] = json.dumps(first, sort_keys=True)
    trace_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(SubmissionRenderError, match="differ from committed evidence"):
        check_submission(tmp_path)
