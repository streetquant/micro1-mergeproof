from __future__ import annotations

from driftproof.certificate import build_certificate, verify_certificate
from driftproof.models import (
    BuildResult,
    CheckResult,
    CheckStatus,
    ContractSpec,
    GateReport,
    Verdict,
)


def sample_report() -> GateReport:
    return GateReport(
        candidate_id="DP-EXAMPLE",
        verdict=Verdict.APPROVE,
        summary="fixture",
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
            worktree_sha256="a" * 64,
        ),
        contract=ContractSpec(context_sha256="b" * 64, rules=[]),
        checks=[
            CheckResult(
                id="C-PASS",
                status=CheckStatus.PASS,
                title="pass",
                detail="fixture",
            )
        ],
    )


def test_certificate_is_self_verifying() -> None:
    report = sample_report()
    certificate = build_certificate(report)
    report = report.model_copy(update={"certificate_sha256": certificate.self_sha256})
    assert verify_certificate(report, certificate) == []


def test_tampered_report_is_rejected() -> None:
    report = sample_report()
    certificate = build_certificate(report)
    tampered = report.model_copy(update={"summary": "tampered"})
    assert "report hash mismatch" in verify_certificate(tampered, certificate)


def test_tampered_certificate_is_rejected() -> None:
    report = sample_report()
    certificate = build_certificate(report)
    tampered = certificate.model_copy(update={"project_sha256": "c" * 64})
    errors = verify_certificate(report, tampered)
    assert "project hash mismatch" in errors
    assert "certificate self-hash mismatch" in errors
