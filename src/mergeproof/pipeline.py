from __future__ import annotations

import difflib
import time
from typing import Any

from pydantic import ValidationError

from .models import (
    AuditResult,
    CaseInput,
    Decision,
    EvidenceRecord,
    Finding,
    FindingCategory,
    FindingStatus,
    Severity,
)
from .prompts import BASELINE_SYSTEM, baseline_prompt
from .providers import LLMProvider, ProviderError
from .utils import canonical_json, sha256_text, stable_evidence_id


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


def build_static_evidence(case: CaseInput) -> list[EvidenceRecord]:
    evidence = [
        _evidence("task", "task.md", case.task),
        _evidence("diff", "candidate.patch", _tree_diff(case.before, case.candidate)),
        _evidence("trajectory", "trajectory.json", canonical_json(case.trajectory)),
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
        evidence.append(_evidence("file", f"candidate/{path}", content))
    return evidence


def _coerce_decision(value: Any) -> Decision:
    try:
        return Decision(str(value))
    except ValueError:
        return Decision.HUMAN_REVIEW


def _admit_model_output(
    *, raw: dict[str, Any], evidence: list[EvidenceRecord]
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
        if invalid:
            violations.append(f"finding {index} referenced unknown evidence: {invalid}")
        status = (
            FindingStatus.VERIFIED if admitted_ids and not invalid else FindingStatus.HYPOTHESIS
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
    except ProviderError as exc:
        task_id = evidence[0].id
        decision = Decision.HUMAN_REVIEW
        summary = f"Provider failure prevented review: {exc}"
        confidence = 0.0
        findings = [
            Finding(
                category=FindingCategory.PROVIDER_FAILURE,
                severity=Severity.HIGH,
                title="Model provider failed",
                explanation=str(exc),
                evidence_ids=[task_id],
                status=FindingStatus.VERIFIED,
            )
        ]
        valid_rate = 1.0
        violations = [str(exc)]
        usage = []
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
        duration_ms=round((time.perf_counter() - started) * 1000),
        provider=provider.name,
        model=provider.model,
    )
