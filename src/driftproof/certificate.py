from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mergeproof.utils import canonical_json, sha256_text

from .models import ApprovalCertificate, CheckStatus, GateReport


def _report_payload(report: GateReport) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    payload.pop("certificate_sha256", None)
    return payload


def build_certificate(report: GateReport) -> ApprovalCertificate:
    report_sha256 = sha256_text(canonical_json(_report_payload(report)))
    passed = [check.id for check in report.checks if check.status == CheckStatus.PASS]
    certificate = ApprovalCertificate(
        candidate_id=report.candidate_id,
        verdict=report.verdict,
        report_sha256=report_sha256,
        project_sha256=report.project_sha256,
        context_sha256=report.context_sha256,
        build_worktree_sha256=report.build.worktree_sha256,
        passed_check_ids=passed,
        failed_check_ids=report.failed_check_ids,
        inconclusive_check_ids=report.inconclusive_check_ids,
    )
    unsigned = certificate.model_dump(mode="json")
    unsigned["self_sha256"] = ""
    return certificate.model_copy(update={"self_sha256": sha256_text(canonical_json(unsigned))})


def verify_certificate(report: GateReport, certificate: ApprovalCertificate) -> list[str]:
    errors: list[str] = []
    expected_report = sha256_text(canonical_json(_report_payload(report)))
    if certificate.report_sha256 != expected_report:
        errors.append("report hash mismatch")
    if certificate.candidate_id != report.candidate_id:
        errors.append("candidate identity mismatch")
    if certificate.verdict != report.verdict:
        errors.append("verdict mismatch")
    if certificate.project_sha256 != report.project_sha256:
        errors.append("project hash mismatch")
    if certificate.context_sha256 != report.context_sha256:
        errors.append("context hash mismatch")
    if certificate.build_worktree_sha256 != report.build.worktree_sha256:
        errors.append("build worktree hash mismatch")
    unsigned = certificate.model_dump(mode="json")
    observed_self = str(unsigned.pop("self_sha256"))
    unsigned["self_sha256"] = ""
    expected_self = sha256_text(canonical_json(unsigned))
    if observed_self != expected_self:
        errors.append("certificate self-hash mismatch")
    return errors


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_bundle(
    output_dir: Path,
    report: GateReport,
    certificate: ApprovalCertificate,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "gate-report.json"
    certificate_path = output_dir / "approval-certificate.json"
    _atomic_json(report_path, report.model_dump(mode="json"))
    _atomic_json(certificate_path, certificate.model_dump(mode="json"))
    return report_path, certificate_path
