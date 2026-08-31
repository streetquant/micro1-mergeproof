from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from driftproof.cli import app
from driftproof.models import (
    DriftProofErrorResponse,
    DriftProofNavigationResponse,
    DriftProofResponseVerification,
)
from driftproof.response import ResponseVerificationError, verify_response_file
from driftproof.sdk import (
    ReviewRequest,
    SDKProtocolError,
    configuration_request_sha256_for_agent,
    fingerprint_for_agent,
    request_with_stable_run_id,
    review_and_verify_for_agent,
    review_for_agent,
    stable_run_id_for_agent,
    verify_response_for_agent,
)

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


@dataclass(frozen=True)
class CompletedResponse:
    root: Path
    project: Path
    request: ReviewRequest
    fingerprint_sha256: str
    response: DriftProofNavigationResponse
    response_file: Path


@pytest.fixture(scope="module")
def completed_response(tmp_path_factory: pytest.TempPathFactory) -> CompletedResponse:
    if shutil.which("bwrap") is None or shutil.which("dbt") is None:
        pytest.skip("response binding integration requires dbt and bubblewrap")
    root = tmp_path_factory.mktemp("response-binding")
    project = root / "candidate with spaces"
    shutil.copytree(ROOT / "examples/judge-demo-safe", project)
    request = ReviewRequest(
        project=str(project),
        context=str(project / "BUSINESS_CONTEXT.md"),
        output=str(root / "review bundle"),
        work_root=str(root / "work root"),
        response_file=str(root / "agent response.json"),
        run_id="binding",
    )
    fingerprint = fingerprint_for_agent(request)
    response = review_for_agent(request, unique_default_run=False)
    assert isinstance(response, DriftProofNavigationResponse)
    response_file = Path(request.response_file or "")
    assert response_file.is_file()
    return CompletedResponse(
        root=root,
        project=project,
        request=request,
        fingerprint_sha256=fingerprint.configuration_request_sha256,
        response=response,
        response_file=response_file,
    )


def test_response_file_binds_every_bundle_claim(completed_response: CompletedResponse) -> None:
    verification = verify_response_file(
        completed_response.response_file,
        expected_request_sha256=completed_response.fingerprint_sha256,
    )

    assert verification.response_envelope_verified is True
    assert verification.response_status == "valid_review"
    assert verification.request_identity_verified is True
    assert verification.review_result_trusted is True
    assert verification.bundle_verified is True
    assert verification.candidate_id == completed_response.response.candidate_id
    assert verification.bundle_manifest_sha256 == completed_response.response.bundle_manifest_sha256
    assert "request_sha256" in verification.verified_binding_scope
    assert "project" in verification.unbound_response_fields
    assert verification.human_approval_required is True
    assert verification.consequential_action_taken is False


def test_verify_response_cli_emits_one_machine_object(
    completed_response: CompletedResponse,
) -> None:
    result = runner.invoke(
        app,
        [
            "verify-response",
            str(completed_response.response_file),
            "--expected-request-sha256",
            completed_response.fingerprint_sha256,
        ],
    )

    assert result.exit_code == 0, result.output
    payload = DriftProofResponseVerification.model_validate_json(result.stdout)
    assert payload.review_result_trusted is True
    assert payload.bundle_verified is True
    assert payload.request_identity_verified is True


def test_response_manifest_hash_tampering_fails_closed(
    completed_response: CompletedResponse,
) -> None:
    payload = completed_response.response.model_dump(mode="json")
    payload["bundle_manifest_sha256"] = "0" * 64
    tampered = completed_response.root / "tampered hash.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ResponseVerificationError, match="bundle_manifest_sha256"):
        verify_response_file(tampered)

    result = runner.invoke(app, ["verify-response", str(tampered)])
    assert result.exit_code == 30
    error = json.loads(result.stdout)
    assert error["status"] == "invalid_review"
    assert error["error_code"] == "bundle_invalid"
    assert error["partial_result_trusted"] is False


def test_response_path_substitution_fails_even_for_the_same_bundle(
    completed_response: CompletedResponse,
) -> None:
    payload = completed_response.response.model_dump(mode="json")
    payload["human_report"] = payload["human_report_markdown"]
    tampered = completed_response.root / "tampered path.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ResponseVerificationError, match="human_report"):
        verify_response_file(tampered)


def test_response_file_symlink_and_symlinked_parent_are_rejected(
    completed_response: CompletedResponse,
    tmp_path: Path,
) -> None:
    direct = tmp_path / "direct-link.json"
    direct.symlink_to(completed_response.response_file)
    with pytest.raises(ResponseVerificationError, match="regular JSON file"):
        verify_response_file(direct)

    parent = tmp_path / "linked-parent"
    parent.symlink_to(completed_response.root, target_is_directory=True)
    through_parent = parent / completed_response.response_file.name
    with pytest.raises(ResponseVerificationError, match="symlinks"):
        verify_response_file(through_parent)


def test_expected_request_identity_mismatch_fails_closed(
    completed_response: CompletedResponse,
) -> None:
    with pytest.raises(ResponseVerificationError, match="request SHA-256"):
        verify_response_file(
            completed_response.response_file,
            expected_request_sha256="f" * 64,
        )


def test_invalid_response_envelope_is_authenticated_but_not_trusted(tmp_path: Path) -> None:
    request = ReviewRequest(
        project="missing-project",
        response_file="control/error.json",
        run_id="invalid",
    )
    expected = configuration_request_sha256_for_agent(request, base_dir=tmp_path)
    response = review_for_agent(
        request,
        base_dir=tmp_path,
        unique_default_run=False,
        process_timeout_seconds=30,
    )
    assert isinstance(response, DriftProofErrorResponse)

    verification = verify_response_for_agent(
        response,
        expected_request_sha256=expected,
    )

    assert verification.response_status == "invalid_review"
    assert verification.response_exit_code == 30
    assert verification.response_envelope_verified is True
    assert verification.request_identity_verified is True
    assert verification.review_result_trusted is False
    assert verification.bundle_verified is False
    assert verification.bundle is None
    assert verification.verdict == "human_review"
    assert verification.human_approval_required is True
    assert verification.consequential_action_taken is False


def test_stable_run_id_tracks_content_not_control_destinations(tmp_path: Path) -> None:
    project = tmp_path / "candidate"
    shutil.copytree(ROOT / "examples/judge-demo-safe", project)
    request = ReviewRequest(
        project=str(project),
        context=str(project / "BUSINESS_CONTEXT.md"),
        output=str(tmp_path / "first"),
    )
    changed_control = request.model_copy(update={"output": str(tmp_path / "second")})

    first = stable_run_id_for_agent(request, prefix="retry")
    second = stable_run_id_for_agent(changed_control, prefix="retry")
    bound = request_with_stable_run_id(request, prefix="retry")

    assert first == second == bound.run_id
    assert first.startswith("retry-")
    assert len(first) <= 64

    model = project / "models/revenue.sql"
    model.write_text(model.read_text(encoding="utf-8").replace("100 - 20", "101 - 20"))
    assert stable_run_id_for_agent(request, prefix="retry") != first
    with pytest.raises(ValueError, match="prefix"):
        stable_run_id_for_agent(request, prefix="contains space")


def test_combined_sdk_rejects_response_file_process_disagreement(
    completed_response: CompletedResponse,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_path = tmp_path / "recorded.json"
    response = completed_response.response.model_copy(update={"response_file": str(recorded_path)})
    recorded = response.model_copy(update={"run_id": "different"})
    recorded_path.write_text(recorded.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr("driftproof.sdk.review_for_agent", lambda *args, **kwargs: response)

    with pytest.raises(SDKProtocolError, match="does not match"):
        review_and_verify_for_agent(
            completed_response.request,
            unique_default_run=False,
        )


@pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("dbt") is None,
    reason="combined SDK integration requires dbt and bubblewrap",
)
def test_review_and_verify_sdk_returns_a_bundle_bound_result(tmp_path: Path) -> None:
    project = tmp_path / "candidate"
    shutil.copytree(ROOT / "examples/judge-demo-safe", project)
    request = ReviewRequest(
        project=str(project),
        context=str(project / "BUSINESS_CONTEXT.md"),
        output=str(tmp_path / "review"),
        work_root=str(tmp_path / "work"),
        response_file=str(tmp_path / "response.json"),
        run_id="combined",
    )

    response, verification = review_and_verify_for_agent(
        request,
        unique_default_run=False,
    )

    assert isinstance(response, DriftProofNavigationResponse)
    assert response.verdict == "approve"
    assert verification.review_result_trusted is True
    assert verification.bundle_verified is True
    assert verification.request_identity_verified is True
    assert (
        verification.request_sha256 == fingerprint_for_agent(request).configuration_request_sha256
    )


def test_response_verification_schema_and_capability_are_discoverable() -> None:
    schema = runner.invoke(app, ["schema", "response-verification"])
    capabilities = runner.invoke(app, ["capabilities"])

    assert schema.exit_code == 0
    assert json.loads(schema.stdout)["title"] == "DriftProofResponseVerification"
    assert capabilities.exit_code == 0
    payload = json.loads(capabilities.stdout)
    assert payload["commands"]["verify_response"] == "driftproof verify-response"
    assert "--expected-request-sha256" in payload["usage"]["verify_response"]
