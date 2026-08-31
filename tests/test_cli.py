from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from mergeproof.cli import app
from mergeproof.models import AuditResult, CaseInput, Decision
from mergeproof.reporting import verify_review_bundle

runner = CliRunner()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "MergeProof CLI Tests")
    git(repo, "config", "user.email", "mergeproof-cli@example.invalid")
    (repo / "value.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "value.py")
    git(repo, "commit", "-q", "-m", "baseline")
    return repo


def test_schemas_are_machine_readable() -> None:
    result = runner.invoke(app, ["schemas"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["request"]["properties"]["schema_version"]["const"] == 1
    assert payload["result"]["properties"]["schema_version"]["const"] == 2
    assert {
        "command",
        "evidence_record",
        "finding",
        "agent_trace",
        "preparation_response",
        "navigation_response",
        "error_response",
        "agent_response",
    } <= set(payload)
    assert payload["error_response"]["properties"]["exit_code"]["const"] == 30
    assert {"error_code", "hint"} <= set(payload["error_response"]["required"])
    assert {
        "bundle_verified",
        "bundle_manifest_sha256",
    } <= set(payload["navigation_response"]["required"])
    assert payload["exit_codes"] == {
        "0": "approve_for_human_checkpoint",
        "10": "reject",
        "20": "human_review",
        "30": "invalid_review_or_tool_error",
    }


def test_one_schema_can_be_discovered_without_parsing_the_catalog() -> None:
    result = runner.invoke(app, ["schema", "navigation_response"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["title"] == "ReviewNavigationResponse"
    assert payload["additionalProperties"] is False
    assert "bundle_manifest_sha256" in payload["properties"]


def test_doctor_requires_a_working_bubblewrap_namespace(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("mergeproof.cli._bubblewrap_available", lambda: False)
    monkeypatch.setattr(
        "mergeproof.cli.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["ready_for_verified_mode"] is False
    assert payload["checks"]["bubblewrap"]["installed"] is True
    assert payload["checks"]["bubblewrap"]["ready"] is False


def test_review_json_emits_bundle_and_stable_exit_code(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "cli-case",
                "title": "CLI case",
                "task": "Review the candidate.",
                "before": {"value.py": "x = 1\n"},
                "candidate": {"value.py": "x = 2\n"},
                "trajectory": [],
                "verification_commands": [],
                "allowed_changed_globs": ["value.py"],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    def fake_run(case: object, mode: object, provider: object) -> AuditResult:
        return AuditResult(
            case_id="cli-case",
            mode="verified",
            decision=Decision.REJECT,
            summary="Fixture rejection.",
            confidence=1.0,
            provider="deterministic",
            model="fixture",
        )

    monkeypatch.setattr("mergeproof.cli._run_case", fake_run)
    bundle = tmp_path / "bundle"
    result = runner.invoke(
        app,
        ["review", str(request), "--output", str(bundle), "--json"],
    )

    assert result.exit_code == 10
    payload = json.loads(result.stdout)
    assert payload["decision"] == "reject"
    assert payload["exit_code"] == 10
    assert payload["human_approval_required"] is True
    assert payload["bundle_verified"] is True
    assert len(payload["bundle_manifest_sha256"]) == 64
    assert payload["response_file"] is None
    assert verify_review_bundle(bundle)["verified"] is True


def test_review_internal_error_uses_stable_fail_closed_protocol(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "internal-error-case",
                "title": "Internal error case",
                "task": "Review the candidate.",
                "before": {"value.py": "x = 1\n"},
                "candidate": {"value.py": "x = 2\n"},
                "trajectory": [],
                "verification_commands": [],
                "allowed_changed_globs": ["value.py"],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "mergeproof.cli._run_case",
        lambda case, mode, provider: AuditResult(
            case_id="internal-error-case",
            mode="verified",
            decision=Decision.APPROVE,
            summary="Fixture approval.",
            confidence=1.0,
            provider="deterministic",
            model="fixture",
        ),
    )

    def fail_bundle(**_: object) -> None:
        raise RuntimeError("internal fixture failure")

    monkeypatch.setattr("mergeproof.cli._emit_bundle_result", fail_bundle)
    result = runner.invoke(app, ["review", str(request), "--json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid_review"
    assert payload["decision"] == "human_review"
    assert payload["exit_code"] == 30
    assert payload["error"] == "RuntimeError"
    assert payload["error_code"] == "internal_error"
    assert payload["hint"]
    assert payload["retryable"] is False
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False
    assert "internal fixture failure" not in payload["detail"]


def test_prepare_requires_explicit_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    result = runner.invoke(app, ["prepare", str(repo), "--json"])
    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["detail"] == "--task or --task-file is required"
    assert payload["error_code"] == "input_invalid"


def test_missing_machine_paths_use_the_json_error_protocol(tmp_path: Path) -> None:
    missing_repo = tmp_path / "missing-repo"
    missing_bundle = tmp_path / "missing-bundle"
    cases = [
        [
            "prepare",
            str(missing_repo),
            "--task",
            "Review the change.",
            "--output",
            str(tmp_path / "request.json"),
            "--json",
        ],
        [
            "review-git",
            str(missing_repo),
            "--task",
            "Review the change.",
            "--output",
            str(tmp_path / "review"),
            "--json",
        ],
        ["verify-bundle", str(missing_bundle), "--json"],
    ]

    for argv in cases:
        result = runner.invoke(app, argv)
        assert result.exit_code == 30, result.output
        payload = json.loads(result.stdout)
        assert payload["status"] == "invalid_review"
        assert payload["decision"] == "human_review"
        assert payload["exit_code"] == 30
        assert payload["error_code"] in {"input_invalid", "bundle_invalid"}
        assert payload["human_approval_required"] is True
        assert payload["consequential_action_taken"] is False


def test_response_publication_failure_removes_the_review_bundle(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "transaction-case",
                "title": "Transaction case",
                "task": "Review the candidate.",
                "before": {"value.py": "x = 1\n"},
                "candidate": {"value.py": "x = 2\n"},
                "trajectory": [],
                "verification_commands": [],
                "allowed_changed_globs": ["value.py"],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mergeproof.cli._run_case",
        lambda case, mode, provider: AuditResult(
            case_id="transaction-case",
            mode="verified",
            decision=Decision.APPROVE,
            summary="Fixture approval.",
            confidence=1.0,
            provider="deterministic",
            model="fixture",
        ),
    )

    def fail_response(path: Path, payload: dict[str, object]) -> None:
        raise OSError("deliberate response publication failure")

    monkeypatch.setattr("mergeproof.cli._write_response_file", fail_response)
    bundle = tmp_path / "bundle"
    response = tmp_path / "response.json"

    result = runner.invoke(
        app,
        [
            "review",
            str(request),
            "--output",
            str(bundle),
            "--response-file",
            str(response),
            "--json",
        ],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid_review"
    assert payload["error_code"] == "filesystem_error"
    assert not bundle.exists()
    assert not response.exists()


def test_review_git_excludes_task_response_and_bundle_control_paths(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    task = repo / "TASK.md"
    task.write_text("Return 2 while preserving the integer return type.\n", encoding="utf-8")
    response = repo / "decision.json"
    bundle = repo / "review"

    def fake_run(
        case: CaseInput,
        mode: object,
        provider: object,
    ) -> AuditResult:
        return AuditResult(
            case_id=case.id,
            mode="verified",
            decision=Decision.APPROVE,
            summary="Fixture approval.",
            confidence=1.0,
            provider="deterministic",
            model="fixture",
        )

    monkeypatch.setattr("mergeproof.cli._run_case", fake_run)
    result = runner.invoke(
        app,
        [
            "review-git",
            str(repo),
            "--task-file",
            str(task),
            "--command",
            "python -m py_compile value.py",
            "--output",
            str(bundle),
            "--response-file",
            str(response),
            "--json",
        ],
    )

    assert result.exit_code == 0
    stdout_payload = json.loads(result.stdout)
    file_payload = json.loads(response.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert stdout_payload["response_file"] == str(response)
    request = json.loads((bundle / "request.json").read_text(encoding="utf-8"))
    assert request["task"] == task.read_text(encoding="utf-8")
    assert request["metadata"]["changed_paths"] == ["value.py"]
    assert request["metadata"]["excluded_control_paths"] == [
        "TASK.md",
        "decision.json",
        "review",
    ]


def test_response_file_receives_the_same_fail_closed_error(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{bad json", encoding="utf-8")
    response = tmp_path / "error.json"
    bundle = tmp_path / "bundle"

    result = runner.invoke(
        app,
        [
            "review",
            str(invalid),
            "--output",
            str(bundle),
            "--response-file",
            str(response),
            "--json",
        ],
    )

    assert result.exit_code == 30
    assert json.loads(result.stdout) == json.loads(response.read_text(encoding="utf-8"))
    assert not bundle.exists()


def test_replace_output_removes_stale_bundle_before_failed_rerun(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "replace-case",
                "title": "Replace case",
                "task": "Review the candidate.",
                "before": {"value.py": "x = 1\n"},
                "candidate": {"value.py": "x = 2\n"},
                "trajectory": [],
                "verification_commands": [],
                "allowed_changed_globs": ["value.py"],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    bundle = tmp_path / "bundle"
    monkeypatch.setattr(
        "mergeproof.cli._run_case",
        lambda case, mode, provider: AuditResult(
            case_id="replace-case",
            mode="verified",
            decision=Decision.APPROVE,
            summary="Fixture approval.",
            confidence=1.0,
            provider="deterministic",
            model="fixture",
        ),
    )
    assert runner.invoke(app, ["review", str(request), "--output", str(bundle)]).exit_code == 0
    assert bundle.exists()

    def fail_run(case: object, mode: object, provider: object) -> AuditResult:
        raise RuntimeError("deliberate rerun failure")

    monkeypatch.setattr("mergeproof.cli._run_case", fail_run)
    rerun = runner.invoke(
        app,
        ["review", str(request), "--output", str(bundle), "--replace-output", "--json"],
    )

    assert rerun.exit_code == 30
    assert not bundle.exists()


def test_schema_aliases_and_capabilities_are_machine_discoverable() -> None:
    agent_schema = runner.invoke(app, ["schema", "agent-response"])
    navigation_schema = runner.invoke(app, ["schema", "navigation-response"])
    invalid_schema = runner.invoke(app, ["schema", "not-a-schema"])
    capabilities = runner.invoke(app, ["capabilities"])

    assert agent_schema.exit_code == 0
    agent_payload = json.loads(agent_schema.stdout)
    assert agent_payload["title"] == "AgentProtocolResponse"
    assert {item["$ref"] for item in agent_payload["anyOf"]} == {
        "#/$defs/ReviewErrorResponse",
        "#/$defs/ReviewNavigationResponse",
    }
    assert navigation_schema.exit_code == 0
    assert json.loads(navigation_schema.stdout)["title"] == "ReviewNavigationResponse"
    assert invalid_schema.exit_code == 30
    invalid_payload = json.loads(invalid_schema.stdout)
    assert invalid_payload["error_code"] == "input_invalid"
    assert capabilities.exit_code == 0
    capability_payload = json.loads(capabilities.stdout)
    assert capability_payload["commands"]["machine_review"] == "mergeproof agent"
    assert capability_payload["external_provider_consent_required"] is True
    assert capability_payload["safety_boundary"] == {
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def test_agent_command_emits_one_versioned_object_and_navigation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "agent-case",
                "title": "Agent case",
                "task": "Review the candidate.",
                "before": {"value.py": "x = 1\n"},
                "candidate": {"value.py": "x = 2\n"},
                "trajectory": [],
                "verification_commands": [],
                "allowed_changed_globs": ["value.py"],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mergeproof.cli._run_case",
        lambda case, mode, provider: AuditResult(
            case_id="agent-case",
            mode="verified",
            decision=Decision.APPROVE,
            summary="Fixture approval.",
            confidence=1.0,
            provider="deterministic",
            model="fixture",
        ),
    )
    bundle = tmp_path / "review"
    response = tmp_path / "response.json"

    result = runner.invoke(
        app,
        [
            "agent",
            str(request),
            "--output",
            str(bundle),
            "--response-file",
            str(response),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "mergeproof.agent.v1"
    assert payload["status"] == "valid_review"
    assert payload["decision"] == "approve"
    assert payload["recommended_action"] == "human_approval"
    assert payload["request"] == str(bundle / "request.json")
    assert payload["evidence_ledger"] == str(bundle / "evidence.jsonl")
    assert payload["agent_traces"] == str(bundle / "agent-traces.json")
    assert payload["human_report_markdown"] == str(bundle / "report.md")
    assert result.stdout == response.read_text(encoding="utf-8")
    verified = runner.invoke(app, ["verify-report", str(bundle)])
    assert verified.exit_code == 0
    assert json.loads(verified.stdout)["verified"] is True
    inspected = runner.invoke(app, ["inspect", str(bundle), "--json"])
    assert inspected.exit_code == 0
    assert json.loads(inspected.stdout)["recommended_action"] == "human_approval"


def test_agent_command_error_is_one_versioned_object(tmp_path: Path) -> None:
    result = runner.invoke(app, ["agent", str(tmp_path / "missing.json")])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "mergeproof.agent.v1"
    assert payload["status"] == "invalid_review"
    assert payload["decision"] == "human_review"
    assert payload["exit_code"] == 30
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False


def test_live_external_provider_requires_explicit_consent(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "consent-case",
                "title": "Consent case",
                "task": "Review the candidate.",
                "before": {"value.py": "x = 1\n"},
                "candidate": {"value.py": "x = 2\n"},
                "trajectory": [],
                "verification_commands": [],
                "allowed_changed_globs": ["value.py"],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent",
            str(request),
            "--mode",
            "advanced",
            "--provider",
            "groq",
            "--output",
            str(tmp_path / "review"),
        ],
    )

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "external_provider_consent_required"
    assert payload["retryable"] is False
    assert not (tmp_path / "review").exists()


def test_prepare_success_uses_versioned_machine_response(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    output = tmp_path / "request.json"
    response = tmp_path / "preparation.json"

    result = runner.invoke(
        app,
        [
            "prepare",
            str(repo),
            "--task",
            "Return two.",
            "--output",
            str(output),
            "--response-file",
            str(response),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["protocol"] == "mergeproof.prepare.v1"
    assert payload["status"] == "request_prepared"
    assert payload["request"] == str(output)
    assert payload["changed_paths"] == ["value.py"]
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False
    assert result.stdout == response.read_text(encoding="utf-8")
