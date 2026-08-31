from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from driftproof.certificate import build_certificate, verify_certificate
from driftproof.models import (
    ApprovalCertificate,
    BuildResult,
    ContractSpec,
    GateReport,
    Verdict,
)
from driftproof.reporting import GateBundleError, verify_gate_bundle, write_gate_bundle


def report_and_certificate(
    *,
    summary: str = "Evidence supports qualified-human approval.",
) -> tuple[GateReport, ApprovalCertificate]:
    report = GateReport(
        candidate_id="DP-REPORTING",
        verdict=Verdict.APPROVE,
        summary=summary,
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
    )
    certificate = build_certificate(report)
    report = report.model_copy(update={"certificate_sha256": certificate.self_sha256})
    assert verify_certificate(report, certificate) == []
    return report, certificate


def test_gate_bundle_round_trip_requires_explicit_replacement(tmp_path: Path) -> None:
    output = tmp_path / "review"
    report, certificate = report_and_certificate()

    write_gate_bundle(output, report, certificate)
    verification = verify_gate_bundle(output)

    assert verification["verified"] is True
    assert verification["verdict"] == "approve"
    assert len(str(verification["bundle_manifest_sha256"])) == 64

    with pytest.raises(GateBundleError, match="already exists"):
        write_gate_bundle(output, report, certificate)

    replacement_report, replacement_certificate = report_and_certificate(
        summary="Replacement bundle was generated deliberately."
    )
    write_gate_bundle(
        output,
        replacement_report,
        replacement_certificate,
        replace=True,
    )
    assert verify_gate_bundle(output)["verified"] is True
    assert "Replacement bundle" in (output / "report.md").read_text(encoding="utf-8")


def test_gate_bundle_refuses_unrelated_directory_contents(tmp_path: Path) -> None:
    output = tmp_path / "review"
    output.mkdir()
    sentinel = output / "judge-notes.txt"
    sentinel.write_text("preserve me\n", encoding="utf-8")
    report, certificate = report_and_certificate()

    with pytest.raises(GateBundleError, match="unrelated entries"):
        write_gate_bundle(output, report, certificate, replace=True)

    assert sentinel.read_text(encoding="utf-8") == "preserve me\n"


def test_gate_bundle_refuses_symlink_destination(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    output = tmp_path / "review"
    output.symlink_to(real, target_is_directory=True)
    report, certificate = report_and_certificate()

    with pytest.raises(GateBundleError, match="may not be a symlink"):
        write_gate_bundle(output, report, certificate)

    assert not any(real.iterdir())


def test_gate_bundle_publication_failure_leaves_no_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "review"
    report, certificate = report_and_certificate()

    def fail_writer(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("deliberate bundle construction failure")

    monkeypatch.setattr("driftproof.reporting._write_gate_bundle_files", fail_writer)

    with pytest.raises(RuntimeError, match="construction failure"):
        write_gate_bundle(output, report, certificate)

    assert not output.exists()
    assert not list(tmp_path.glob(".review.*"))


def test_concurrent_gate_writers_leave_one_complete_verified_bundle(tmp_path: Path) -> None:
    output = tmp_path / "review"
    report, certificate = report_and_certificate()

    def publish() -> str:
        try:
            write_gate_bundle(output, report, certificate)
        except GateBundleError:
            return "lost-race"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: publish(), range(2)))

    assert "published" in outcomes
    assert verify_gate_bundle(output)["verified"] is True
    assert {path.name for path in output.iterdir()} == {
        "approval-certificate.json",
        "gate-report.json",
        "manifest.json",
        "report.html",
        "report.md",
    }
