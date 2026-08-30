from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from mergeproof.cli import app
from mergeproof.models import AuditResult, Decision
from mergeproof.reporting import verify_review_bundle

runner = CliRunner()


def test_schemas_are_machine_readable() -> None:
    result = runner.invoke(app, ["schemas"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["request"]["properties"]["schema_version"]["const"] == 1
    assert payload["result"]["properties"]["schema_version"]["const"] == 2
    assert payload["exit_codes"] == {
        "0": "approve",
        "10": "reject",
        "20": "human_review",
        "30": "tool_or_input_error",
    }


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
    assert verify_review_bundle(bundle)["verified"] is True


def test_prepare_requires_explicit_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    result = runner.invoke(app, ["prepare", str(repo), "--json"])
    assert result.exit_code == 30
    assert json.loads(result.stdout)["detail"] == "--task is required and may not be blank"
