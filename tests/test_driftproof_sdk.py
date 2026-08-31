from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from driftproof.cli import app
from driftproof.models import DriftProofErrorResponse, DriftProofNavigationResponse
from driftproof.reporting import verify_gate_bundle
from driftproof.sdk import (
    ReviewRequest,
    SDKProtocolError,
    fingerprint_for_agent,
    review_for_agent,
)

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def copy_demo(tmp_path: Path) -> Path:
    project = tmp_path / "candidate with spaces"
    shutil.copytree(ROOT / "examples/judge-demo-safe", project)
    return project


def test_fingerprint_binds_content_but_excludes_control_destinations(tmp_path: Path) -> None:
    project = copy_demo(tmp_path)
    first_request = ReviewRequest(
        project=str(project),
        context=str(project / "BUSINESS_CONTEXT.md"),
        output=str(tmp_path / "first-output"),
        work_root=str(tmp_path / "first-work"),
        response_file=str(tmp_path / "first-response.json"),
        run_id="first",
    )
    second_request = first_request.model_copy(
        update={
            "output": str(tmp_path / "second-output"),
            "work_root": str(tmp_path / "second-work"),
            "response_file": str(tmp_path / "second-response.json"),
            "run_id": "second",
            "replace_output": True,
        }
    )

    first = fingerprint_for_agent(first_request)
    second = fingerprint_for_agent(second_request)

    assert first.configuration_request_sha256 == second.configuration_request_sha256
    assert first.content_fingerprint_sha256 == second.content_fingerprint_sha256
    assert first.candidate_code_executed is False
    assert first.external_provider_response_bound is False
    assert first.agent_argv == ["driftproof", "agent", "-"]
    assert not (project / "target").exists()

    model = project / "models/revenue.sql"
    model.write_text(model.read_text(encoding="utf-8").replace("100 - 20", "101 - 20"))
    changed = fingerprint_for_agent(first_request)
    assert changed.configuration_request_sha256 == first.configuration_request_sha256
    assert changed.project_sha256 != first.project_sha256
    assert changed.content_fingerprint_sha256 != first.content_fingerprint_sha256


def test_fingerprint_cli_accepts_request_json_and_exports_schema(tmp_path: Path) -> None:
    project = copy_demo(tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        ReviewRequest(project="candidate with spaces").model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    fingerprint = runner.invoke(app, ["fingerprint", str(request_path)])
    schema = runner.invoke(app, ["schema", "fingerprint-response"])

    assert fingerprint.exit_code == 0, fingerprint.output
    payload = json.loads(fingerprint.stdout)
    assert payload["protocol"] == "driftproof.fingerprint.v1"
    assert payload["project"] == str(project.resolve())
    assert payload["candidate_code_executed"] is False
    assert schema.exit_code == 0
    assert json.loads(schema.stdout)["title"] == "DriftProofFingerprintResponse"


def test_sdk_returns_typed_fail_closed_error_for_invalid_project(tmp_path: Path) -> None:
    response = review_for_agent(
        ReviewRequest(project="missing-project"),
        base_dir=tmp_path,
        process_timeout_seconds=30,
    )

    assert isinstance(response, DriftProofErrorResponse)
    assert response.exit_code == 30
    assert response.status == "invalid_review"
    assert response.human_approval_required is True
    assert response.consequential_action_taken is False


def test_sdk_rejects_non_protocol_process_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "driftproof.sdk.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["driftproof"],
            returncode=1,
            stdout="not-json\n",
            stderr="provider token=DEMO_ONLY_NOT_A_REAL_CREDENTIAL_123456\n",
        ),
    )

    with pytest.raises(SDKProtocolError, match="one valid protocol object") as exc_info:
        review_for_agent(ReviewRequest(project="candidate"), base_dir=tmp_path)
    assert "DEMO_ONLY_NOT_A_REAL_CREDENTIAL" not in str(exc_info.value)


@pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("dbt") is None,
    reason="concurrent SDK integration requires the qualified dbt and bubblewrap runtime",
)
def test_concurrent_sdk_callers_receive_disjoint_verified_bundles(tmp_path: Path) -> None:
    project = copy_demo(tmp_path)
    request = ReviewRequest(
        project=str(project),
        context=str(project / "BUSINESS_CONTEXT.md"),
    )
    fingerprint = fingerprint_for_agent(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: review_for_agent(request), range(2)))

    assert all(isinstance(response, DriftProofNavigationResponse) for response in responses)
    navigation = [
        response for response in responses if isinstance(response, DriftProofNavigationResponse)
    ]
    assert len({response.run_id for response in navigation}) == 2
    assert len({response.bundle for response in navigation}) == 2
    for response in navigation:
        assert response.request_sha256 == fingerprint.configuration_request_sha256
        assert response.exit_code == 0
        assert response.verdict == "approve"
        assert verify_gate_bundle(Path(response.bundle))["verified"] is True
