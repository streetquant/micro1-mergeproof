from __future__ import annotations

import difflib
import re
import time
from typing import Any

from pydantic import ValidationError

from .collector import StaticAnalysis, collect_static_analysis, finding_catalog
from .models import (
    AgentTrace,
    AuditResult,
    CaseInput,
    Contract,
    Decision,
    EvidenceRecord,
    Finding,
    FindingCategory,
    FindingStatus,
    ModelUsage,
    Severity,
)
from .prompts import (
    BASELINE_SYSTEM,
    CONTRACT_SYSTEM,
    CRITIC_SYSTEM,
    baseline_prompt,
    contract_prompt,
    critic_prompt,
)
from .providers import LLMProvider, ProviderError
from .sandbox import SandboxUnavailable, VerificationAnalysis, verify_case
from .utils import canonical_json, redact_secrets, sha256_text, stable_evidence_id

_PRESERVATION_CONTRACT = re.compile(
    r"(?i)\b(do not change|preserv\w*|must remain|remain unchanged|idempotent|only when)\b"
)
_CLOSED_DOMAIN_CONTRACT = re.compile(
    r"(?i)\b(reject unknown|reject every other|every other value|only explicitly allowed)\b"
)


def _tree_diff(before: dict[str, str], candidate: dict[str, str]) -> str:
    lines: list[str] = []
    for path in sorted(set(before) | set(candidate)):
        old = before.get(path, "").splitlines(keepends=True)
        new = candidate.get(path, "").splitlines(keepends=True)
        lines.extend(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
    return "\n".join(line.rstrip("\n") for line in lines)


def _evidence(kind: str, source: str, content: str, **metadata: Any) -> EvidenceRecord:
    return EvidenceRecord(
        id=stable_evidence_id(kind, source, content),
        kind=kind,
        source=source,
        sha256=sha256_text(content),
        content=content,
        metadata=metadata,
    )


def _redacted_evidence(kind: str, source: str, content: str, **metadata: Any) -> EvidenceRecord:
    redacted = redact_secrets(content)
    projection = dict(metadata)
    if redacted != content:
        projection.update(
            {
                "content_redacted": True,
                "original_sha256": sha256_text(content),
                "original_chars": len(content),
            }
        )
    return _evidence(kind, source, redacted, **projection)


def build_static_evidence(case: CaseInput) -> list[EvidenceRecord]:
    evidence = [
        _redacted_evidence("task", "task.md", case.task),
        _redacted_evidence("diff", "candidate.patch", _tree_diff(case.before, case.candidate)),
        _redacted_evidence("trajectory", "trajectory.json", canonical_json(case.trajectory)),
        _evidence(
            "policy",
            "allowed-changed-globs.json",
            canonical_json(case.allowed_changed_globs),
        ),
        _evidence(
            "commands",
            "verification-commands.json",
            canonical_json(
                [command.model_dump(mode="json") for command in case.verification_commands]
            ),
        ),
    ]
    for path, content in sorted(case.candidate.items()):
        evidence.append(_redacted_evidence("file", f"candidate/{path}", content))
    return evidence


def _coerce_decision(value: Any) -> Decision:
    try:
        return Decision(str(value))
    except ValueError:
        return Decision.HUMAN_REVIEW


def _admit_model_output(
    *,
    raw: dict[str, Any],
    evidence: list[EvidenceRecord],
    verified_if_admitted: bool = True,
) -> tuple[Decision, str, float, list[Finding], float, list[str]]:
    valid_ids = {item.id for item in evidence}
    violations: list[str] = []
    findings: list[Finding] = []
    referenced = 0
    valid_referenced = 0
    raw_findings = raw.get("findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []
        violations.append("findings was not a list")
    for index, item in enumerate(raw_findings):
        if not isinstance(item, dict):
            violations.append(f"finding {index} was not an object")
            continue
        requested_ids = item.get("evidence_ids", [])
        if not isinstance(requested_ids, list):
            requested_ids = []
        normalized_ids = [str(value) for value in requested_ids]
        referenced += len(normalized_ids)
        admitted_ids = [value for value in normalized_ids if value in valid_ids]
        valid_referenced += len(admitted_ids)
        invalid = sorted(set(normalized_ids) - valid_ids)
        if not normalized_ids:
            violations.append(f"finding {index} supplied no evidence IDs")
        if invalid:
            violations.append(f"finding {index} referenced unknown evidence: {invalid}")
        status = (
            FindingStatus.VERIFIED
            if verified_if_admitted and admitted_ids and not invalid
            else FindingStatus.HYPOTHESIS
        )
        try:
            finding = Finding(
                category=FindingCategory(str(item.get("category", "other"))),
                severity=Severity(str(item.get("severity", "medium"))),
                title=str(item.get("title", "Untitled finding"))[:160],
                explanation=str(item.get("explanation", "No explanation supplied"))[:4000],
                evidence_ids=admitted_ids,
                status=status,
            )
        except (ValueError, ValidationError):
            violations.append(f"finding {index} failed schema validation")
            continue
        findings.append(finding)
    decision = _coerce_decision(raw.get("decision"))
    if violations and decision == Decision.APPROVE:
        decision = Decision.HUMAN_REVIEW
    try:
        confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
        violations.append("confidence was not numeric")
    summary = str(raw.get("summary", "No summary supplied"))[:4000]
    valid_rate = 1.0 if referenced == 0 else valid_referenced / referenced
    return decision, summary, confidence, findings, valid_rate, violations


def _agent_trace(
    *,
    agent: str,
    response: Any,
    evidence: list[EvidenceRecord],
    accepted_output: dict[str, Any],
    gate_violations: list[str] | None = None,
) -> AgentTrace:
    payload = canonical_json(accepted_output)
    return AgentTrace(
        agent=agent,
        provider=response.usage.provider,
        model=response.usage.model,
        request_hash=response.usage.request_hash,
        input_evidence_ids=[item.id for item in evidence],
        output_sha256=sha256_text(payload),
        accepted_output=accepted_output,
        gate_violations=list(gate_violations or []),
        usage=response.usage,
    )


def _verified_finding(
    *,
    category: FindingCategory,
    title: str,
    explanation: str,
    evidence_ids: list[str],
    severity: Severity = Severity.HIGH,
) -> Finding:
    return Finding(
        category=category,
        severity=severity,
        title=title,
        explanation=explanation,
        evidence_ids=sorted(set(evidence_ids)),
        status=FindingStatus.VERIFIED,
    )


def _derived_contract_findings(
    *,
    case: CaseInput,
    base_evidence: list[EvidenceRecord],
    static: StaticAnalysis,
    verification: VerificationAnalysis,
) -> list[Finding]:
    findings: list[Finding] = []
    task_ids = [item.id for item in base_evidence if item.kind == "task"]
    command_ids = [item.id for item in verification.evidence if item.kind == "command"]
    claim_ids = [
        item.id for item in static.evidence if item.metadata.get("detector") == "trajectory_claim"
    ]
    verification_problem = verification.failed or verification.skipped or verification.denied

    if static.edge_sensitive and (verification.failed or verification.skipped):
        findings.append(
            _verified_finding(
                category=FindingCategory.EDGE_CASE_FAILURE,
                title="Declared boundary behavior is not verified",
                explanation=(
                    "The task explicitly names boundary-sensitive behavior, and bounded verification "
                    "either failed or completed with skipped tests."
                ),
                evidence_ids=[*task_ids, *command_ids],
            )
        )

    preservation_required = bool(
        _PRESERVATION_CONTRACT.search(case.task) or _CLOSED_DOMAIN_CONTRACT.search(case.task)
    )
    if preservation_required and not static.edge_sensitive and verification_problem:
        findings.append(
            _verified_finding(
                category=FindingCategory.BEHAVIORAL_REGRESSION,
                title="Candidate violates an explicit preservation contract",
                explanation=(
                    "The task requires existing, idempotent, or closed-domain behavior to be "
                    "preserved, while bounded verification establishes a mismatch."
                ),
                evidence_ids=[*task_ids, *command_ids],
            )
        )

    if static.specific_success_claim and verification_problem:
        findings.append(
            _verified_finding(
                category=FindingCategory.UNVERIFIED_CLAIM,
                title="Agent success claim conflicts with executable evidence",
                explanation=(
                    "The supplied trajectory makes a specific success or type-preservation claim, "
                    "but bounded verification failed, was denied, or skipped required checks."
                ),
                evidence_ids=[*task_ids, *claim_ids, *command_ids],
            )
        )
    return findings


def _deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    result: list[Finding] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for finding in findings:
        key = (
            finding.category.value,
            finding.status.value,
            finding.title,
            tuple(sorted(finding.evidence_ids)),
        )
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def _deterministic_review(
    case: CaseInput,
) -> tuple[list[EvidenceRecord], list[Finding], StaticAnalysis, VerificationAnalysis]:
    base_evidence = build_static_evidence(case)
    static = collect_static_analysis(case, base_evidence)
    verification = verify_case(case)
    evidence = [*base_evidence, *static.evidence, *verification.evidence]
    findings = _deduplicate_findings(
        [
            *static.findings,
            *verification.findings,
            *_derived_contract_findings(
                case=case,
                base_evidence=base_evidence,
                static=static,
                verification=verification,
            ),
        ]
    )
    return evidence, findings, static, verification


def _deterministic_decision(
    case: CaseInput,
    findings: list[Finding],
    verification: VerificationAnalysis,
) -> tuple[Decision, str, float]:
    if findings:
        categories = ", ".join(sorted({finding.category.value for finding in findings}))
        return (
            Decision.REJECT,
            f"Rejected: verified evidence establishes material blocker categories: {categories}.",
            0.99,
        )
    if not case.verification_commands:
        return (
            Decision.HUMAN_REVIEW,
            "Human review required: the candidate declares no bounded verification command.",
            0.0,
        )
    if verification.denied or verification.failed or verification.skipped:
        return (
            Decision.REJECT,
            "Rejected: bounded verification did not complete successfully.",
            0.99,
        )
    return (
        Decision.APPROVE,
        "Evidence supports approval for a qualified human merge decision; no action was executed.",
        0.95,
    )


def _provider_failure_finding(task_id: str, error: Exception) -> Finding:
    return Finding(
        category=FindingCategory.PROVIDER_FAILURE,
        severity=Severity.HIGH,
        title="Model provider or structured output failed",
        explanation=str(error),
        evidence_ids=[task_id],
        status=FindingStatus.VERIFIED,
    )


def run_baseline(case: CaseInput, provider: LLMProvider) -> AuditResult:
    started = time.perf_counter()
    evidence = build_static_evidence(case)
    try:
        response = provider.complete_json(
            agent="baseline_reviewer",
            system=BASELINE_SYSTEM,
            user=baseline_prompt(
                task=case.task,
                allowed_changed_globs=case.allowed_changed_globs,
                evidence=evidence,
            ),
        )
        decision, summary, confidence, findings, valid_rate, violations = _admit_model_output(
            raw=response.data, evidence=evidence
        )
        usage = [response.usage]
        traces = [
            _agent_trace(
                agent="baseline_reviewer",
                response=response,
                evidence=evidence,
                accepted_output=response.data,
                gate_violations=violations,
            )
        ]
    except ProviderError as exc:
        task_id = evidence[0].id
        decision = Decision.HUMAN_REVIEW
        summary = f"Provider failure prevented review: {exc}"
        confidence = 0.0
        findings = [_provider_failure_finding(task_id, exc)]
        valid_rate = 1.0
        violations = [str(exc)]
        usage = []
        traces = []
    return AuditResult(
        case_id=case.id,
        mode="baseline",
        decision=decision,
        summary=summary,
        confidence=confidence,
        findings=findings,
        evidence=evidence,
        valid_evidence_rate=valid_rate,
        gate_violations=violations,
        usage=usage,
        agent_traces=traces,
        duration_ms=round((time.perf_counter() - started) * 1000),
        provider=provider.name,
        model=provider.model,
    )


def run_verified(case: CaseInput) -> AuditResult:
    started = time.perf_counter()
    try:
        evidence, findings, _static, verification = _deterministic_review(case)
        decision, summary, confidence = _deterministic_decision(case, findings, verification)
        violations: list[str] = []
    except SandboxUnavailable as exc:
        evidence = build_static_evidence(case)
        task_id = evidence[0].id
        findings = [
            _verified_finding(
                category=FindingCategory.INSUFFICIENT_EVIDENCE,
                title="Required isolation boundary is unavailable",
                explanation=str(exc),
                evidence_ids=[task_id],
            )
        ]
        decision = Decision.HUMAN_REVIEW
        summary = f"Human review required: {exc}"
        confidence = 0.0
        violations = [str(exc)]
    return AuditResult(
        case_id=case.id,
        mode="verified",
        decision=decision,
        summary=summary,
        confidence=confidence,
        findings=findings,
        evidence=evidence,
        valid_evidence_rate=1.0,
        gate_violations=violations,
        usage=[],
        duration_ms=round((time.perf_counter() - started) * 1000),
        provider="deterministic",
        model="collector+bubblewrap-v1",
    )


def run_advanced(case: CaseInput, provider: LLMProvider) -> AuditResult:
    started = time.perf_counter()
    usage: list[ModelUsage] = []
    gate_violations: list[str] = []
    critic_findings: list[Finding] = []
    critic_valid_rate = 1.0
    contract: Contract | None = None
    traces: list[AgentTrace] = []

    try:
        evidence, deterministic_findings, _static, verification = _deterministic_review(case)
    except SandboxUnavailable as exc:
        evidence = build_static_evidence(case)
        task_id = evidence[0].id
        deterministic_findings = [
            _verified_finding(
                category=FindingCategory.INSUFFICIENT_EVIDENCE,
                title="Required isolation boundary is unavailable",
                explanation=str(exc),
                evidence_ids=[task_id],
            )
        ]
        return AuditResult(
            case_id=case.id,
            mode="advanced",
            decision=Decision.HUMAN_REVIEW,
            summary=f"Human review required: {exc}",
            confidence=0.0,
            findings=deterministic_findings,
            evidence=evidence,
            valid_evidence_rate=1.0,
            gate_violations=[str(exc)],
            usage=[],
            duration_ms=round((time.perf_counter() - started) * 1000),
            provider=provider.name,
            model=provider.model,
        )

    task_id = next(item.id for item in evidence if item.kind == "task")
    try:
        contract_response = provider.complete_json(
            agent="contract_analyst",
            system=CONTRACT_SYSTEM,
            user=contract_prompt(
                task=case.task,
                allowed_changed_globs=case.allowed_changed_globs,
                evidence=evidence,
            ),
        )
        usage.append(contract_response.usage)
        contract = Contract.model_validate(contract_response.data)
        if not any(
            (
                contract.requirements,
                contract.invariants,
                contract.ambiguities,
                contract.acceptance_checks,
            )
        ):
            raise ProviderError("contract analyst returned an empty contract")
        contract_record = _evidence(
            "agent",
            "contract-analysis.json",
            canonical_json(contract.model_dump(mode="json")),
            agent="contract_analyst",
            request_hash=contract_response.usage.request_hash,
        )
        evidence.append(contract_record)
        traces.append(
            _agent_trace(
                agent="contract_analyst",
                response=contract_response,
                evidence=[item for item in evidence if item.id != contract_record.id],
                accepted_output=contract.model_dump(mode="json"),
            )
        )

        critic_response = provider.complete_json(
            agent="skeptical_reviewer",
            system=CRITIC_SYSTEM,
            user=critic_prompt(
                task=case.task,
                contract=contract,
                deterministic_findings=deterministic_findings,
                evidence=evidence,
            ),
        )
        usage.append(critic_response.usage)
        (
            critic_decision,
            critic_summary,
            critic_confidence,
            critic_findings,
            critic_valid_rate,
            critic_violations,
        ) = _admit_model_output(
            raw=critic_response.data,
            evidence=evidence,
            verified_if_admitted=False,
        )
        gate_violations.extend(critic_violations)
        critic_record = _evidence(
            "agent",
            "skeptical-review.json",
            canonical_json(
                {
                    "decision": critic_decision.value,
                    "summary": critic_summary,
                    "confidence": critic_confidence,
                    "findings": [finding.model_dump(mode="json") for finding in critic_findings],
                    "gate_violations": critic_violations,
                }
            ),
            agent="skeptical_reviewer",
            request_hash=critic_response.usage.request_hash,
        )
        evidence.append(critic_record)
        traces.append(
            _agent_trace(
                agent="skeptical_reviewer",
                response=critic_response,
                evidence=[item for item in evidence if item.id != critic_record.id],
                accepted_output={
                    "decision": critic_decision.value,
                    "summary": critic_summary,
                    "confidence": critic_confidence,
                    "findings": [finding.model_dump(mode="json") for finding in critic_findings],
                },
                gate_violations=critic_violations,
            )
        )
    except (ProviderError, ValidationError) as exc:
        critic_findings.append(_provider_failure_finding(task_id, exc))
        gate_violations.append(str(exc))

    findings = _deduplicate_findings([*deterministic_findings, *critic_findings])
    decision, summary, confidence = _deterministic_decision(
        case, deterministic_findings, verification
    )
    if gate_violations and decision == Decision.APPROVE:
        decision = Decision.HUMAN_REVIEW
        confidence = 0.0
        summary = "Human review required because an agent response failed evidence admission."

    material_hypotheses = [
        finding
        for finding in critic_findings
        if finding.status == FindingStatus.HYPOTHESIS
        and finding.severity in {Severity.HIGH, Severity.CRITICAL}
        and finding.evidence_ids
    ]
    if material_hypotheses and decision == Decision.APPROVE:
        decision = Decision.HUMAN_REVIEW
        confidence = min(confidence, 0.5)
        summary = (
            "Human review required: the skeptical agent found material evidence-bound "
            "hypotheses that deterministic checks could not resolve."
        )

    return AuditResult(
        case_id=case.id,
        mode="advanced",
        decision=decision,
        summary=summary,
        confidence=confidence,
        findings=findings,
        evidence=evidence,
        valid_evidence_rate=critic_valid_rate,
        gate_violations=gate_violations,
        usage=usage,
        contract=contract,
        agent_traces=traces,
        duration_ms=round((time.perf_counter() - started) * 1000),
        provider=provider.name,
        model=provider.model,
    )


def verified_finding_catalog(result: AuditResult) -> str:
    return finding_catalog(
        [finding for finding in result.findings if finding.status == FindingStatus.VERIFIED]
    )
