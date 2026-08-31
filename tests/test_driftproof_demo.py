from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from driftproof.cli import app
from driftproof.models import DriftProofDemoResponse
from driftproof.reporting import verify_gate_bundle

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("dbt") is None,
    reason="installed demo requires the qualified dbt and bubblewrap runtime",
)
def test_installed_demo_publishes_a_verified_transparent_pair(tmp_path: Path) -> None:
    output = tmp_path / "installed demo with spaces"

    result = runner.invoke(app, ["demo", "--output", str(output), "--json"])

    assert result.exit_code == 0, result.output
    response = DriftProofDemoResponse.model_validate_json(result.stdout)
    receipt = DriftProofDemoResponse.model_validate_json(
        (output / "demo-receipt.json").read_text(encoding="utf-8")
    )
    assert response == receipt
    assert response.safe.baseline_verdict == "approve"
    assert response.unsafe.baseline_verdict == "approve"
    assert response.safe.driftproof_verdict == "approve"
    assert response.safe.driftproof_exit_code == 0
    assert response.unsafe.driftproof_verdict == "reject"
    assert response.unsafe.driftproof_exit_code == 10
    assert response.unsafe.failed_check_ids
    assert response.human_approval_required is True
    assert response.consequential_action_taken is False
    assert verify_gate_bundle(Path(response.safe.bundle))["verified"] is True
    assert verify_gate_bundle(Path(response.unsafe.bundle))["verified"] is True
    assert Path(response.safe.human_report).is_file()
    assert Path(response.unsafe.human_report).is_file()
    assert not (output / "work").exists()


def test_demo_refuses_existing_output_without_deleting_it(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep-me.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")

    result = runner.invoke(app, ["demo", "--output", str(output), "--json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["status"] == "invalid_review"
    assert payload["partial_result_trusted"] is False
    assert payload["human_approval_required"] is True
    assert payload["consequential_action_taken"] is False
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    assert list(output.iterdir()) == [sentinel]


def test_demo_reports_missing_dbt_as_one_fail_closed_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("driftproof.demo.find_dbt_executable", lambda: None)

    result = runner.invoke(app, ["demo", "--json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "review_execution_failed"
    assert "driftproof[dbt]" in payload["detail"]
    assert payload["partial_result_trusted"] is False
    assert payload["consequential_action_taken"] is False
    assert len([line for line in result.stdout.splitlines() if line.strip()]) > 1
    assert result.stdout.lstrip().startswith("{")
    assert result.stdout.rstrip().endswith("}")


def test_demo_is_machine_discoverable() -> None:
    capabilities = runner.invoke(app, ["capabilities"])
    schema = runner.invoke(app, ["schema", "demo-response"])
    help_result = runner.invoke(app, ["demo", "--help"])

    assert capabilities.exit_code == 0
    payload = json.loads(capabilities.stdout)
    assert payload["commands"]["demo"] == "driftproof demo"
    assert payload["usage"]["demo"].startswith("driftproof demo")
    assert schema.exit_code == 0
    assert json.loads(schema.stdout)["title"] == "DriftProofDemoResponse"
    assert help_result.exit_code == 0
    assert "credential-free" in help_result.stdout


def test_demo_module_is_importable_without_source_tree_helpers() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "from driftproof.demo import run_demo; print(run_demo.__name__)"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout.strip() == "run_demo"
