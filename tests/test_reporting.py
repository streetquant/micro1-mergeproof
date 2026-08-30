from __future__ import annotations

from pathlib import Path

import pytest

from mergeproof.models import (
    AgentTrace,
    AuditResult,
    CaseInput,
    Decision,
    EvidenceRecord,
    Finding,
    FindingCategory,
    FindingStatus,
    ModelUsage,
    Severity,
)
from mergeproof.reporting import (
    BundleVerificationError,
    decision_exit_code,
    verify_review_bundle,
    write_review_bundle,
)
from mergeproof.utils import sha256_text, stable_evidence_id


def sample_bundle() -> tuple[CaseInput, AuditResult]:
    case = CaseInput(
        id="reporting-case",
        title="Reporting case",
        task="Preserve the integer return type.",
        before={"value.py": "def value():\n    return 1\n"},
        candidate={"value.py": "def value():\n    return '2'\n"},
    )
    content = "candidate returns a string"
    evidence = EvidenceRecord(
        id=stable_evidence_id("scan", "type.json", content),
        kind="scan",
        source="type.json",
        sha256=sha256_text(content),
        content=content,
    )
    result = AuditResult(
        case_id=case.id,
        mode="verified",
        decision=Decision.REJECT,
        summary="A verified type regression blocks approval.",
        confidence=0.99,
        findings=[
            Finding(
                category=FindingCategory.BEHAVIORAL_REGRESSION,
                severity=Severity.HIGH,
                title="Return type changed",
                explanation="The candidate returns a string.",
                evidence_ids=[evidence.id],
                status=FindingStatus.VERIFIED,
            )
        ],
        evidence=[evidence],
        provider="deterministic",
        model="fixture",
    )
    return case, result


def test_bundle_round_trip_and_human_boundary(tmp_path: Path) -> None:
    case, result = sample_bundle()
    bundle = tmp_path / "bundle"
    manifest = write_review_bundle(case, result, bundle)
    verification = verify_review_bundle(bundle)

    assert manifest["decision"] == "reject"
    assert verification["verified"] is True
    assert verification["exit_code"] == 10
    markdown = (bundle / "report.md").read_text(encoding="utf-8")
    html = (bundle / "report.html").read_text(encoding="utf-8")
    assert "Human approval boundary" in markdown
    assert "MergeProof performed no merge, deployment, push, publication" in html


def test_bundle_tampering_fails_closed(tmp_path: Path) -> None:
    case, result = sample_bundle()
    bundle = tmp_path / "bundle"
    write_review_bundle(case, result, bundle)
    (bundle / "result.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(BundleVerificationError, match="mismatch"):
        verify_review_bundle(bundle)


def test_bundle_rejects_unexpected_entry(tmp_path: Path) -> None:
    case, result = sample_bundle()
    bundle = tmp_path / "bundle"
    write_review_bundle(case, result, bundle)
    (bundle / "ambiguous-extra.txt").write_text("not part of the bundle\n", encoding="utf-8")

    with pytest.raises(BundleVerificationError, match="entry set mismatch"):
        verify_review_bundle(bundle)


def test_bundle_recomputes_agent_trace_output_hash(tmp_path: Path) -> None:
    case, result = sample_bundle()
    evidence_id = result.evidence[0].id
    usage = ModelUsage(
        provider="fixture",
        model="fixture-model",
        agent="skeptical_reviewer",
        request_hash="a" * 64,
    )
    result = result.model_copy(
        update={
            "agent_traces": [
                AgentTrace(
                    agent="skeptical_reviewer",
                    provider="fixture",
                    model="fixture-model",
                    request_hash="a" * 64,
                    input_evidence_ids=[evidence_id],
                    output_sha256="0" * 64,
                    accepted_output={"decision": "reject"},
                    usage=usage,
                )
            ]
        }
    )
    bundle = tmp_path / "bundle"
    write_review_bundle(case, result, bundle)

    with pytest.raises(BundleVerificationError, match="agent output hash mismatch"):
        verify_review_bundle(bundle)


def test_bundle_recomputes_evidence_identity(tmp_path: Path) -> None:
    case, result = sample_bundle()
    evidence = result.evidence[0].model_copy(update={"id": "scan:forged:0000000000000000"})
    finding = result.findings[0].model_copy(update={"evidence_ids": [evidence.id]})
    result = result.model_copy(update={"evidence": [evidence], "findings": [finding]})
    bundle = tmp_path / "bundle"
    write_review_bundle(case, result, bundle)

    with pytest.raises(BundleVerificationError, match="evidence identity mismatch"):
        verify_review_bundle(bundle)


def test_exit_codes_are_stable() -> None:
    assert decision_exit_code(Decision.APPROVE) == 0
    assert decision_exit_code(Decision.REJECT) == 10
    assert decision_exit_code(Decision.HUMAN_REVIEW) == 20
