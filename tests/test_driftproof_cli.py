from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from driftproof.cli import app
from driftproof.models import (
    ApprovalCertificate,
    BuildResult,
    ContractSpec,
    GateReport,
    Verdict,
)

runner = CliRunner()


def project_with_context(path: Path) -> Path:
    path.mkdir()
    (path / "BUSINESS_CONTEXT.md").write_text(
        "The public contract must expose `customer_id`.\n",
        encoding="utf-8",
    )
    return path


def result_pair(verdict: Verdict) -> tuple[GateReport, ApprovalCertificate]:
    report = GateReport(
        candidate_id="DP-CLI",
        verdict=verdict,
        summary=f"Fixture {verdict.value}.",
        project_sha256="a" * 64,
        context_sha256="b" * 64,
        build=BuildResult(
            command=["dbt", "build"],
            returncode=0,
            passed=True,
            stdout="",
            stderr="",
            duration_ms=1,
            isolation="disposable_copy",
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


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [
        (Verdict.APPROVE, 0),
        (Verdict.REJECT, 10),
        (Verdict.HUMAN_REVIEW, 20),
    ],
)
def test_review_json_uses_shared_stable_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verdict: Verdict,
    exit_code: int,
) -> None:
    monkeypatch.setattr(
        "driftproof.cli.review_project",
        lambda *args, **kwargs: result_pair(verdict),
    )
    monkeypatch.setattr(
        "driftproof.cli.verify_gate_bundle",
        lambda output: {
            "verified": True,
            "verdict": verdict.value,
            "bundle_manifest_sha256": "f" * 64,
        },
    )
    project = project_with_context(tmp_path / "project")
    output = tmp_path / "bundle"

    result = runner.invoke(
        app,
        ["review", str(project), "--output", str(output), "--json"],
    )

    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "driftproof.agent.v1"
    assert payload["status"] == "valid_review"
    assert payload["candidate_id"] == "DP-CLI"
    assert payload["verdict"] == verdict.value
    assert payload["exit_code"] == exit_code
    assert (
        payload["recommended_action"]
        == {
            Verdict.APPROVE: "human_approval",
            Verdict.REJECT: "repair_required",
            Verdict.HUMAN_REVIEW: "evidence_or_human_escalation",
        }[verdict]
    )
    assert payload["bundle"] == str(output)
    assert payload["bundle_verified"] is True
    assert payload["bundle_manifest_sha256"] == "f" * 64
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False


def test_review_unexpected_error_is_generic_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> tuple[GateReport, ApprovalCertificate]:
        raise RuntimeError("private path /secret/location")

    monkeypatch.setattr("driftproof.cli.review_project", fail)
    project = project_with_context(tmp_path / "project")

    result = runner.invoke(app, ["review", str(project), "--json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid_review"
    assert payload["verdict"] == "human_review"
    assert payload["exit_code"] == 30
    assert payload["retryable"] is False
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False
    assert "/secret/location" not in payload["detail"]


def test_doctor_returns_actionable_machine_next_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("driftproof.cli._bubblewrap_available", lambda: True)
    monkeypatch.setattr("driftproof.cli.find_dbt_executable", lambda: None)
    monkeypatch.setattr("driftproof.cli.shutil.which", lambda name: f"/usr/bin/{name}")

    missing = runner.invoke(app, ["doctor", "--json"])

    assert missing.exit_code == 30
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["missing_requirements"] == ["dbt"]
    assert missing_payload["recommended_action"] == "repair_environment"
    assert missing_payload["next_argv"] is None
    assert any("dbt-core" in item for item in missing_payload["remediation"])

    monkeypatch.setattr("driftproof.cli.find_dbt_executable", lambda: "/usr/bin/dbt")
    ready = runner.invoke(app, ["doctor", "--json"])

    assert ready.exit_code == 0
    ready_payload = json.loads(ready.stdout)
    assert ready_payload["missing_requirements"] == []
    assert ready_payload["recommended_action"] == "run_onboard"
    assert ready_payload["next_argv"] == ["driftproof", "onboard", ".", "--json"]


def test_capabilities_and_schema_aliases_are_machine_discoverable() -> None:
    capabilities = runner.invoke(app, ["capabilities"])
    agent_schema = runner.invoke(app, ["schema", "agent-response"])
    navigation_schema = runner.invoke(app, ["schema", "navigation_response"])
    onboarding_schema = runner.invoke(app, ["schema", "onboard-response"])
    invalid_schema = runner.invoke(app, ["schema", "not-a-schema"])

    assert capabilities.exit_code == 0
    capability_payload = json.loads(capabilities.stdout)
    assert capability_payload["commands"]["machine_review"] == "driftproof agent"
    assert capability_payload["commands"]["onboard"] == "driftproof onboard"
    assert capability_payload["usage"]["onboard"].startswith("driftproof onboard")
    assert capability_payload["external_provider_consent_required"] is True
    assert capability_payload["safety_boundary"] == {
        "human_approval_required": True,
        "consequential_action_taken": False,
    }

    assert agent_schema.exit_code == 0
    agent_payload = json.loads(agent_schema.stdout)
    assert agent_payload["title"] == "DriftProofAgentProtocolResponse"
    assert {item["$ref"] for item in agent_payload["anyOf"]} == {
        "#/$defs/DriftProofErrorResponse",
        "#/$defs/DriftProofNavigationResponse",
    }
    assert navigation_schema.exit_code == 0
    assert json.loads(navigation_schema.stdout)["title"] == "DriftProofNavigationResponse"
    assert onboarding_schema.exit_code == 0
    onboarding_payload = json.loads(onboarding_schema.stdout)
    assert onboarding_payload["title"] == "DriftProofOnboardingResponse"
    assert onboarding_payload["additionalProperties"] is False
    assert invalid_schema.exit_code == 30
    assert json.loads(invalid_schema.stdout)["error_code"] == "validation_failed"


def test_agent_writes_the_same_single_protocol_object_to_stdout_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, certificate = result_pair(Verdict.APPROVE)
    monkeypatch.setattr(
        "driftproof.cli.review_project",
        lambda *args, **kwargs: (report, certificate),
    )
    monkeypatch.setattr(
        "driftproof.cli.verify_gate_bundle",
        lambda output: {
            "verified": True,
            "verdict": "approve",
            "bundle_manifest_sha256": "f" * 64,
        },
    )
    project = project_with_context(tmp_path / "project")
    output = tmp_path / "bundle"
    response = tmp_path / "response.json"

    result = runner.invoke(
        app,
        [
            "agent",
            str(project),
            "--output",
            str(output),
            "--response-file",
            str(response),
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == response.read_text(encoding="utf-8")
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "driftproof.agent.v1"
    assert payload["status"] == "valid_review"
    assert payload["recommended_action"] == "human_approval"
    assert payload["response_file"] == str(response)
    assert payload["bundle_verified"] is True
    assert payload["human_report_markdown"] == str(output / "report.md")


def test_external_clarifier_requires_explicit_consent(tmp_path: Path) -> None:
    project = project_with_context(tmp_path / "project")
    output = tmp_path / "bundle"

    result = runner.invoke(
        app,
        [
            "agent",
            str(project),
            "--output",
            str(output),
            "--agent-provider",
            "groq",
        ],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "driftproof.agent.v1"
    assert payload["error_code"] == "external_provider_consent_required"
    assert payload["retryable"] is False
    assert not output.exists()


def test_control_paths_inside_candidate_are_rejected(tmp_path: Path) -> None:
    project = project_with_context(tmp_path / "project")
    output = project / "review"

    result = runner.invoke(
        app,
        ["agent", str(project), "--output", str(output)],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "review_execution_failed"
    assert "must be outside the candidate project" in payload["detail"]
    assert not output.exists()


def test_response_file_may_not_be_inside_the_verified_bundle(tmp_path: Path) -> None:
    project = project_with_context(tmp_path / "project")
    output = tmp_path / "bundle"
    response = output / "decision.json"

    result = runner.invoke(
        app,
        [
            "agent",
            str(project),
            "--output",
            str(output),
            "--response-file",
            str(response),
        ],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "review_execution_failed"
    assert "must be disjoint" in payload["detail"]
    assert not output.exists()


def test_provider_control_paths_may_not_be_inside_candidate(tmp_path: Path) -> None:
    project = project_with_context(tmp_path / "project")
    replay = project / "fixtures"
    replay.mkdir()
    output = tmp_path / "bundle"

    result = runner.invoke(
        app,
        [
            "agent",
            str(project),
            "--output",
            str(output),
            "--agent-provider",
            "replay",
            "--agent-replay-dir",
            str(replay),
        ],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "review_execution_failed"
    assert "provider replay directory must be outside" in payload["detail"]
    assert not output.exists()


def test_default_control_paths_are_outside_the_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_with_context(tmp_path / f"candidate-{tmp_path.name}")
    report, certificate = result_pair(Verdict.APPROVE)
    captured: dict[str, Path] = {}

    def fake_review(*args: object, **kwargs: object) -> tuple[GateReport, ApprovalCertificate]:
        captured["output"] = Path(str(kwargs["output_dir"]))
        captured["work_root"] = Path(str(kwargs["work_root"]))
        return report, certificate

    monkeypatch.setattr("driftproof.cli.review_project", fake_review)
    monkeypatch.setattr(
        "driftproof.cli.verify_gate_bundle",
        lambda output: {
            "verified": True,
            "verdict": "approve",
            "bundle_manifest_sha256": "f" * 64,
        },
    )

    result = runner.invoke(app, ["agent", str(project)])

    assert result.exit_code == 0, result.output
    project_root = project.resolve()
    for path in captured.values():
        resolved = path.resolve(strict=False)
        assert resolved != project_root
        assert not resolved.is_relative_to(project_root)


def test_missing_project_uses_the_one_object_error_protocol(tmp_path: Path) -> None:
    result = runner.invoke(app, ["agent", str(tmp_path / "missing")])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "driftproof.agent.v1"
    assert payload["status"] == "invalid_review"
    assert payload["exit_code"] == 30
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False
