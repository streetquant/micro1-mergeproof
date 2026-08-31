from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from driftproof.cli import _request_identity, _resolve_control_paths, app
from driftproof.contracts import compile_contract
from driftproof.models import (
    ApprovalCertificate,
    BuildResult,
    ContractSpec,
    DriftProofReviewRequest,
    GateReport,
    Verdict,
)

runner = CliRunner()


def dbt_project(path: Path, *, context: str | None = None) -> Path:
    path.mkdir(parents=True)
    (path / "models").mkdir()
    (path / "dbt_project.yml").write_text(
        "name: protocol_fixture\nversion: 1.0.0\nprofile: protocol_fixture\n"
        "model-paths: [models]\ntarget-path: target\nclean-targets: [target, logs]\n",
        encoding="utf-8",
    )
    (path / "profiles.yml").write_text(
        "protocol_fixture:\n  target: dev\n  outputs:\n    dev:\n"
        "      type: duckdb\n      path: protocol.duckdb\n      threads: 1\n",
        encoding="utf-8",
    )
    (path / "models" / "customers.sql").write_text(
        "select 1 as customer_id\n",
        encoding="utf-8",
    )
    (path / "BUSINESS_CONTEXT.md").write_text(
        context or "The public contract must expose `customer_id`.\n",
        encoding="utf-8",
    )
    return path


def review_pair(verdict: Verdict = Verdict.APPROVE) -> tuple[GateReport, ApprovalCertificate]:
    report = GateReport(
        candidate_id="DP-PROTOCOL",
        verdict=verdict,
        summary=f"Protocol fixture {verdict.value}.",
        project_sha256="a" * 64,
        context_sha256="b" * 64,
        build=BuildResult(
            command=["dbt", "build"],
            returncode=0,
            passed=True,
            stdout="",
            stderr="",
            duration_ms=1,
            isolation="bubblewrap",
            worktree_sha256="c" * 64,
        ),
        contract=ContractSpec(
            context_sha256="b" * 64,
            rules=[],
            unknown_sentences=[],
        ),
        checks=[],
        failed_check_ids=["C-FAIL"] if verdict == Verdict.REJECT else [],
        inconclusive_check_ids=["C-UNKNOWN"] if verdict == Verdict.HUMAN_REVIEW else [],
        certificate_sha256="d" * 64,
    )
    certificate = ApprovalCertificate(
        candidate_id=report.candidate_id,
        verdict=verdict,
        report_sha256="e" * 64,
        project_sha256=report.project_sha256,
        context_sha256=report.context_sha256,
        build_worktree_sha256=report.build.worktree_sha256,
        passed_check_ids=[],
        failed_check_ids=report.failed_check_ids,
        inconclusive_check_ids=report.inconclusive_check_ids,
        self_sha256="d" * 64,
    )
    return report, certificate


def install_review_mocks(
    monkeypatch: pytest.MonkeyPatch,
    verdict: Verdict = Verdict.APPROVE,
) -> None:
    report, certificate = review_pair(verdict)
    monkeypatch.setattr(
        "driftproof.cli.review_project",
        lambda *args, **kwargs: (report, certificate),
    )
    monkeypatch.setattr(
        "driftproof.cli.verify_gate_bundle",
        lambda output: {
            "verified": True,
            "verdict": verdict.value,
            "bundle_manifest_sha256": "f" * 64,
        },
    )


def test_request_file_resolves_paths_relative_to_itself_and_binds_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_review_mocks(monkeypatch)
    root = tmp_path / "request-root"
    project = dbt_project(root / "candidate")
    request_path = root / "review-request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "driftproof.request.v1",
                "project": "candidate",
                "output": "control/review",
                "response_file": "control/response.json",
                "run_id": "ci-17",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["agent", str(request_path)])

    assert result.exit_code == 0, result.output
    response = root / "control" / "response.json"
    assert result.stdout == response.read_text(encoding="utf-8")
    payload = json.loads(result.stdout)
    assert payload["tool_version"]
    assert payload["request_sha256"] and len(payload["request_sha256"]) == 64
    assert payload["run_id"] == "ci-17"
    assert payload["project"] == str(project.resolve())
    assert payload["context"] == str((project / "BUSINESS_CONTEXT.md").resolve())
    assert payload["project_sha256"] == "a" * 64
    assert payload["context_sha256"] == "b" * 64
    assert payload["build_worktree_sha256"] == "c" * 64
    assert payload["verify_argv"] == [
        "driftproof",
        "verify-report",
        str((root / "control" / "review").resolve()),
    ]


def test_request_json_on_stdin_produces_one_protocol_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_review_mocks(monkeypatch)
    project = dbt_project(tmp_path / "candidate")
    output = tmp_path / "review"
    request = {
        "schema_version": 1,
        "protocol": "driftproof.request.v1",
        "project": str(project),
        "output": str(output),
    }

    result = runner.invoke(app, ["agent", "-"], input=json.dumps(request))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "valid_review"
    assert payload["bundle"] == str(output.resolve())
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False


def test_request_mode_rejects_ambiguous_cli_overrides(tmp_path: Path) -> None:
    project = dbt_project(tmp_path / "candidate")
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "driftproof.request.v1",
                "project": str(project),
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["agent", str(request), "--output", str(tmp_path / "override")],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "validation_failed"
    assert "may not be combined" in payload["detail"]
    assert payload["partial_result_trusted"] is False


def test_request_schema_forbids_unknown_fields(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "driftproof.request.v1",
                "project": "candidate",
                "invented_authority": True,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["agent", str(request)])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "validation_failed"
    assert payload["status"] == "invalid_review"


def test_request_identity_ignores_only_control_destinations() -> None:
    first = DriftProofReviewRequest(
        project="/candidate",
        context="/candidate/BUSINESS_CONTEXT.md",
        output="/tmp/one",
        response_file="/tmp/one.json",
        run_id="one",
    )
    second = first.model_copy(
        update={
            "output": "/tmp/two",
            "response_file": "/tmp/two.json",
            "run_id": "two",
            "replace_output": True,
        }
    )
    changed_contract = first.model_copy(update={"timeout_seconds": 121})

    assert _request_identity(first) == _request_identity(second)
    assert _request_identity(first) != _request_identity(changed_contract)


def test_context_template_is_compilable_and_requires_explicit_replacement(
    tmp_path: Path,
) -> None:
    output = tmp_path / "BUSINESS_CONTEXT.md"

    first = runner.invoke(app, ["context-template", "--output", str(output), "--json"])
    blocked = runner.invoke(app, ["context-template", "--output", str(output), "--json"])
    replaced = runner.invoke(
        app,
        ["context-template", "--output", str(output), "--replace-output", "--json"],
    )

    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.stdout)
    assert first_payload["output"] == str(output.resolve())
    assert len(first_payload["content_sha256"]) == 64
    contract = compile_contract(output.read_text(encoding="utf-8"))
    assert contract.rules
    assert contract.unknown_sentences == []
    assert blocked.exit_code == 30
    assert json.loads(blocked.stdout)["error_code"] == "review_execution_failed"
    assert replaced.exit_code == 0


def test_onboard_plans_without_execution_and_safely_creates_context(tmp_path: Path) -> None:
    project = tmp_path / "candidate with spaces"
    project.mkdir()
    context = project / "BUSINESS_CONTEXT.md"

    planned = runner.invoke(app, ["onboard", str(project), "--run-id", "judge-1", "--json"])

    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.stdout)
    assert plan["protocol"] == "driftproof.onboard.v1"
    assert plan["status"] == "planning"
    assert plan["recommended_action"] == "create_business_context"
    assert plan["context_exists"] is False
    assert plan["context_created"] is False
    assert plan["candidate_code_executed"] is False
    assert plan["create_context_argv"][-2:] == ["--apply", "--json"]
    assert plan["review_argv"][-2:] == ["--run-id", "judge-1"]
    assert not context.exists()

    applied = runner.invoke(
        app,
        ["onboard", str(project), "--run-id", "judge-1", "--apply", "--json"],
    )

    assert applied.exit_code == 0, applied.output
    created = json.loads(applied.stdout)
    assert created["status"] == "context_created"
    assert created["recommended_action"] == "edit_business_context"
    assert created["context_exists"] is True
    assert created["context_created"] is True
    assert created["created_files"] == [str(context.resolve())]
    assert created["create_context_argv"] is None
    assert compile_contract(context.read_text(encoding="utf-8")).rules

    context.write_text("Human-authored contract.\n", encoding="utf-8")
    repeated = runner.invoke(app, ["onboard", str(project), "--apply", "--json"])
    assert repeated.exit_code == 0, repeated.output
    existing = json.loads(repeated.stdout)
    assert existing["status"] == "context_present"
    assert existing["context_created"] is False
    assert context.read_text(encoding="utf-8") == "Human-authored contract.\n"


def test_preflight_reports_compiled_and_unresolved_contracts(tmp_path: Path) -> None:
    complete = dbt_project(
        tmp_path / "complete",
        context="The public contract must expose `customer_id`.\n",
    )
    unresolved = dbt_project(
        tmp_path / "unresolved",
        context="Make this intuitively delightful for finance.\n",
    )

    complete_result = runner.invoke(app, ["preflight", str(complete), "--json"])
    unresolved_result = runner.invoke(app, ["preflight", str(unresolved), "--json"])

    assert complete_result.exit_code == 0, complete_result.output
    complete_payload = json.loads(complete_result.stdout)
    assert complete_payload["deterministic_contract_complete"] is True
    assert complete_payload["recommended_action"] == "run_review"
    assert complete_payload["compiled_rules"] >= 1
    assert complete_payload["review_can_run"] is True

    assert unresolved_result.exit_code == 0, unresolved_result.output
    unresolved_payload = json.loads(unresolved_result.stdout)
    assert unresolved_payload["deterministic_contract_complete"] is False
    assert unresolved_payload["recommended_action"] == "clarify_business_context"
    assert unresolved_payload["unresolved_sentences"]


def test_default_paths_do_not_collide_for_equal_project_basenames(tmp_path: Path) -> None:
    first = tmp_path / "one" / "candidate"
    second = tmp_path / "two" / "candidate"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    first_output, first_work = _resolve_control_paths(first, None, None, None)
    second_output, second_work = _resolve_control_paths(second, None, None, None)
    run_output, run_work = _resolve_control_paths(first, None, None, "retry-2")

    assert first_output != second_output
    assert first_work != second_work
    assert first_output != run_output
    assert first_work != run_work
    assert "retry-2" in str(run_output)


def test_request_file_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "driftproof.request.v1",
                "project": "candidate",
            }
        ),
        encoding="utf-8",
    )
    link = tmp_path / "request.json"
    link.symlink_to(target)

    result = runner.invoke(app, ["agent", str(link)])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "review_execution_failed"
    assert "regular JSON file" in payload["detail"]
