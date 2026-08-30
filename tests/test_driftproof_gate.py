from __future__ import annotations

from pathlib import Path

import pytest

from driftproof.certificate import verify_certificate
from driftproof.gate import GateExecutionError, baseline_green_gate, review_project
from driftproof.models import CheckStatus, Verdict


def make_dbt_project(tmp_path: Path, *, model_sql: str, context: str) -> Path:
    (tmp_path / "models").mkdir(parents=True)
    (tmp_path / "dbt_project.yml").write_text(
        "name: gate_fixture\nversion: 1.0.0\nprofile: gate_fixture\n"
        "model-paths: [models]\ntarget-path: target\nclean-targets: [target, logs]\n",
        encoding="utf-8",
    )
    (tmp_path / "profiles.yml").write_text(
        "gate_fixture:\n  target: dev\n  outputs:\n    dev:\n"
        "      type: duckdb\n      path: gate.duckdb\n      threads: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "models" / "fixture.sql").write_text(model_sql, encoding="utf-8")
    (tmp_path / "BUSINESS_CONTEXT.md").write_text(context, encoding="utf-8")
    (tmp_path / ".driftproof-candidate").write_text("DP-INTEGRATION\n", encoding="utf-8")
    return tmp_path


def test_green_baseline_and_advanced_approval(tmp_path: Path) -> None:
    project = make_dbt_project(
        tmp_path / "project",
        model_sql="select 1 as customer_id\n",
        context="The public contract must expose `customer_id`.",
    )
    source_before = (project / "models" / "fixture.sql").read_bytes()

    assert (
        baseline_green_gate(
            project,
            work_root=tmp_path / "baseline-work",
            isolation="disposable_copy",
            allow_unconfined=True,
        )
        == Verdict.APPROVE
    )
    report, certificate = review_project(
        project,
        work_root=tmp_path / "advanced-work",
        output_dir=tmp_path / "bundle",
        isolation="disposable_copy",
        allow_unconfined=True,
    )

    assert report.verdict == Verdict.APPROVE
    assert report.candidate_id == "DP-INTEGRATION"
    assert report.build.passed
    assert all(check.status == CheckStatus.PASS for check in report.checks)
    assert report.human_approval_required
    assert not report.consequential_action_taken
    assert verify_certificate(report, certificate) == []
    assert (project / "models" / "fixture.sql").read_bytes() == source_before
    assert not (project / "target").exists()
    assert (tmp_path / "bundle" / "gate-report.json").is_file()
    assert (tmp_path / "bundle" / "approval-certificate.json").is_file()


def test_invalid_build_is_rejected(tmp_path: Path) -> None:
    project = make_dbt_project(
        tmp_path / "project",
        model_sql="select from definitely_invalid\n",
        context="The public contract must expose `customer_id`.",
    )
    report, _ = review_project(
        project,
        work_root=tmp_path / "work",
        isolation="disposable_copy",
        allow_unconfined=True,
    )
    assert report.verdict == Verdict.REJECT
    assert not report.build.passed
    assert report.failed_check_ids


def test_unsupported_context_escalates_to_human(tmp_path: Path) -> None:
    project = make_dbt_project(
        tmp_path / "project",
        model_sql="select 1 as value\n",
        context="Make this result intuitively delightful for the finance team.",
    )
    report, _ = review_project(
        project,
        work_root=tmp_path / "work",
        isolation="disposable_copy",
        allow_unconfined=True,
    )
    assert report.verdict == Verdict.HUMAN_REVIEW
    assert report.inconclusive_check_ids


def test_remote_profile_is_rejected_before_execution(tmp_path: Path) -> None:
    project = make_dbt_project(
        tmp_path / "project",
        model_sql="select 1 as customer_id\n",
        context="The public contract must expose `customer_id`.",
    )
    (project / "profiles.yml").write_text(
        "gate_fixture:\n  target: dev\n  outputs:\n    dev:\n"
        "      type: duckdb\n      path: md:remote_database\n",
        encoding="utf-8",
    )
    with pytest.raises(GateExecutionError, match=r"project-relative|remote"):
        review_project(
            project,
            work_root=tmp_path / "work",
            isolation="disposable_copy",
        )


def test_symlink_is_rejected(tmp_path: Path) -> None:
    project = make_dbt_project(
        tmp_path / "project",
        model_sql="select 1 as customer_id\n",
        context="The public contract must expose `customer_id`.",
    )
    outside = tmp_path / "outside.sql"
    outside.write_text("select 2 as customer_id\n", encoding="utf-8")
    (project / "models" / "linked.sql").symlink_to(outside)
    with pytest.raises(GateExecutionError, match="symlinks"):
        review_project(
            project,
            work_root=tmp_path / "work",
            isolation="disposable_copy",
        )
