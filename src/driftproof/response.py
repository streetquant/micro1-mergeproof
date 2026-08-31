from __future__ import annotations

from pathlib import Path

from .models import (
    ApprovalCertificate,
    DriftProofAgentProtocolResponse,
    DriftProofErrorResponse,
    DriftProofNavigationResponse,
    DriftProofResponseVerification,
    GateReport,
    Verdict,
)
from .reporting import GateBundleError, verdict_exit_code, verify_gate_bundle

_MAX_RESPONSE_BYTES = 1_000_000
_VALID_BOUND_FIELDS = [
    "bundle",
    "bundle_manifest_sha256",
    "build_worktree_sha256",
    "candidate_id",
    "certificate",
    "certificate_sha256",
    "consequential_action_taken",
    "exit_code",
    "failed_check_ids",
    "failed_checks",
    "human_approval_required",
    "human_report",
    "human_report_markdown",
    "inconclusive_check_ids",
    "inconclusive_checks",
    "manifest",
    "project_sha256",
    "report",
    "summary",
    "verdict",
    "verify_argv",
]
_VALID_UNBOUND_FIELDS = [
    "context",
    "project",
    "response_file",
    "run_id",
    "tool_version",
]
_INVALID_BOUND_FIELDS = [
    "consequential_action_taken",
    "exit_code",
    "human_approval_required",
    "partial_result_trusted",
    "protocol",
    "status",
]
_INVALID_UNBOUND_FIELDS = [
    "context",
    "detail",
    "error",
    "error_code",
    "hint",
    "response_file",
    "retryable",
    "run_id",
    "tool_version",
]


class ResponseVerificationError(GateBundleError):
    """Raised when a response cannot be bound to the evidence it references."""


def _canonical_absolute(value: str, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise ResponseVerificationError(f"{label} must be an absolute canonical path: {value}")
    resolved = raw.resolve(strict=False)
    if raw != resolved:
        raise ResponseVerificationError(f"{label} may not contain symlinks or aliases: {value}")
    return resolved


def _load_response_file(path: Path) -> DriftProofNavigationResponse | DriftProofErrorResponse:
    lexical = path.expanduser()
    absolute = lexical if lexical.is_absolute() else Path.cwd() / lexical
    absolute = absolute.absolute()
    if absolute.is_symlink() or not absolute.is_file():
        raise ResponseVerificationError(f"response must be a regular JSON file: {lexical}")
    resolved = absolute.resolve(strict=False)
    if absolute != resolved:
        raise ResponseVerificationError(f"response path may not contain symlinks: {lexical}")
    payload = absolute.read_bytes()
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ResponseVerificationError(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")
    try:
        return DriftProofAgentProtocolResponse.model_validate_json(payload).root
    except ValueError as exc:
        raise ResponseVerificationError(
            f"response is not one valid DriftProof protocol object: {exc}"
        ) from exc


def _expect_equal(actual: object, expected: object, *, field: str) -> None:
    if actual != expected:
        raise ResponseVerificationError(
            f"response field {field!r} does not match the verified bundle: "
            f"{actual!r} != {expected!r}"
        )


def _verify_request_identity(
    response: DriftProofNavigationResponse | DriftProofErrorResponse,
    expected_request_sha256: str | None,
) -> bool:
    if expected_request_sha256 is None:
        return False
    if response.request_sha256 != expected_request_sha256:
        raise ResponseVerificationError(
            "response request SHA-256 does not match the independently computed request identity"
        )
    return True


def verify_response_object(
    response: DriftProofNavigationResponse | DriftProofErrorResponse,
    *,
    response_file: Path | None = None,
    expected_request_sha256: str | None = None,
) -> DriftProofResponseVerification:
    """Authenticate one response envelope and every claim bound by its referenced bundle."""

    request_verified = _verify_request_identity(response, expected_request_sha256)
    response_path = str(response_file.resolve()) if response_file is not None else None

    if isinstance(response, DriftProofErrorResponse):
        return DriftProofResponseVerification(
            response_status=response.status,
            response_exit_code=response.exit_code,
            response_file=response_path,
            request_sha256=response.request_sha256,
            expected_request_sha256=expected_request_sha256,
            request_identity_verified=request_verified,
            run_id=response.run_id,
            review_result_trusted=False,
            bundle=None,
            bundle_verified=False,
            bundle_manifest_sha256=None,
            candidate_id=None,
            verdict=Verdict.HUMAN_REVIEW,
            verify_argv=None,
            verified_binding_scope=(
                [*_INVALID_BOUND_FIELDS, "request_sha256"]
                if request_verified
                else _INVALID_BOUND_FIELDS
            ),
            unbound_response_fields=(
                _INVALID_UNBOUND_FIELDS
                if request_verified
                else [*_INVALID_UNBOUND_FIELDS, "request_sha256"]
            ),
        )

    bundle = _canonical_absolute(response.bundle, label="bundle")
    verification = verify_gate_bundle(bundle)
    report = GateReport.model_validate_json(
        (bundle / "gate-report.json").read_text(encoding="utf-8")
    )
    certificate = ApprovalCertificate.model_validate_json(
        (bundle / "approval-certificate.json").read_text(encoding="utf-8")
    )

    expected_paths = {
        "bundle": str(bundle),
        "report": str(bundle / "gate-report.json"),
        "certificate": str(bundle / "approval-certificate.json"),
        "manifest": str(bundle / "manifest.json"),
        "human_report": str(bundle / "report.html"),
        "human_report_markdown": str(bundle / "report.md"),
    }
    for field, expected in expected_paths.items():
        _expect_equal(getattr(response, field), expected, field=field)

    _expect_equal(response.candidate_id, report.candidate_id, field="candidate_id")
    _expect_equal(response.verdict, report.verdict, field="verdict")
    _expect_equal(response.exit_code, verdict_exit_code(report.verdict), field="exit_code")
    _expect_equal(response.summary, report.summary, field="summary")
    _expect_equal(response.project_sha256, report.project_sha256, field="project_sha256")
    _expect_equal(response.context_sha256, report.context_sha256, field="context_sha256")
    _expect_equal(
        response.build_worktree_sha256,
        report.build.worktree_sha256,
        field="build_worktree_sha256",
    )
    _expect_equal(
        response.certificate_sha256,
        certificate.self_sha256,
        field="certificate_sha256",
    )
    _expect_equal(
        response.bundle_manifest_sha256,
        verification["bundle_manifest_sha256"],
        field="bundle_manifest_sha256",
    )
    _expect_equal(response.failed_check_ids, report.failed_check_ids, field="failed_check_ids")
    _expect_equal(
        response.inconclusive_check_ids,
        report.inconclusive_check_ids,
        field="inconclusive_check_ids",
    )
    _expect_equal(response.failed_checks, len(report.failed_check_ids), field="failed_checks")
    _expect_equal(
        response.inconclusive_checks,
        len(report.inconclusive_check_ids),
        field="inconclusive_checks",
    )
    expected_verify_argv = ["driftproof", "verify-report", str(bundle)]
    _expect_equal(response.verify_argv, expected_verify_argv, field="verify_argv")
    _expect_equal(response.bundle_verified, True, field="bundle_verified")
    _expect_equal(response.human_approval_required, True, field="human_approval_required")
    _expect_equal(
        response.consequential_action_taken,
        False,
        field="consequential_action_taken",
    )

    return DriftProofResponseVerification(
        response_status=response.status,
        response_exit_code=response.exit_code,
        response_file=response_path,
        request_sha256=response.request_sha256,
        expected_request_sha256=expected_request_sha256,
        request_identity_verified=request_verified,
        run_id=response.run_id,
        review_result_trusted=True,
        bundle=str(bundle),
        bundle_verified=True,
        bundle_manifest_sha256=str(verification["bundle_manifest_sha256"]),
        candidate_id=report.candidate_id,
        verdict=report.verdict,
        verify_argv=expected_verify_argv,
        verified_binding_scope=(
            [*_VALID_BOUND_FIELDS, "request_sha256"] if request_verified else _VALID_BOUND_FIELDS
        ),
        unbound_response_fields=(
            _VALID_UNBOUND_FIELDS
            if request_verified
            else [*_VALID_UNBOUND_FIELDS, "request_sha256"]
        ),
    )


def verify_response_file(
    path: Path,
    *,
    expected_request_sha256: str | None = None,
) -> DriftProofResponseVerification:
    """Load and authenticate a regular response file plus its referenced bundle."""

    response = _load_response_file(path)
    return verify_response_object(
        response,
        response_file=path.expanduser().resolve(strict=False),
        expected_request_sha256=expected_request_sha256,
    )


__all__ = [
    "ResponseVerificationError",
    "verify_response_file",
    "verify_response_object",
]
