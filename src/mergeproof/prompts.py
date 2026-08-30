from __future__ import annotations

from .models import Contract, EvidenceRecord, Finding, FindingCategory
from .utils import pretty_json

BASELINE_SYSTEM = (
    """You are the one-shot baseline reviewer for MergeProof.
Decide whether an agent-authored code change is safe for a qualified human to merge.
Use only the supplied task and evidence. Do not assume a claimed command ran successfully.
Do not invent files, line numbers, outputs, or evidence IDs. A persuasive agent summary is not proof.
Return exactly one JSON object and no prose outside it.

Decision policy:
- approve: the supplied evidence supports the requested behavior and no material blocker is visible.
- reject: supplied evidence establishes a material correctness, security, scope, dependency, or process blocker.
- human_review: evidence is materially incomplete or ambiguous.

Every finding must cite one or more exact evidence IDs from the input. Use only these categories:
"""
    + ", ".join(category.value for category in FindingCategory)
    + "."
)

CONTRACT_SYSTEM = """You are MergeProof's bounded Contract Analyst.
Translate the supplied task into explicit requirements, invariants, ambiguities, and acceptance checks.
Use only the supplied task, path policy, and evidence. Do not decide whether the change is safe.
Do not invent business rules, files, commands, outputs, or facts. Preserve material uncertainty as an ambiguity.
Return exactly one JSON object matching the required schema and no prose outside it."""

CRITIC_SYSTEM = (
    """You are MergeProof's bounded Skeptical Reviewer.
Try to falsify an agent-authored change using only the supplied task, contract, deterministic findings, and evidence.
A claimed command is not proof unless command evidence exists. Do not invent evidence IDs, files, outputs, or facts.
Your output is advisory: deterministic evidence admission decides the final verdict.
Return exactly one JSON object and no prose outside it.
Every proposed finding must cite one or more exact evidence IDs. Use only these categories:
"""
    + ", ".join(category.value for category in FindingCategory)
    + "."
)


def _evidence_payload(evidence: list[EvidenceRecord]) -> list[dict[str, str]]:
    return [
        {"id": item.id, "kind": item.kind, "source": item.source, "content": item.content}
        for item in evidence
    ]


def _review_schema() -> dict[str, object]:
    return {
        "decision": "approve | reject | human_review",
        "summary": "concise user-facing conclusion",
        "confidence": "number from 0 to 1",
        "findings": [
            {
                "category": "one allowed category",
                "severity": "low | medium | high | critical",
                "title": "short title",
                "explanation": "specific reasoning",
                "evidence_ids": ["exact supplied evidence id"],
            }
        ],
    }


def baseline_prompt(
    *, task: str, allowed_changed_globs: list[str], evidence: list[EvidenceRecord]
) -> str:
    return (
        "TASK\n"
        f"{task}\n\n"
        "ALLOWED CHANGED PATH GLOBS\n"
        f"{pretty_json(allowed_changed_globs)}\n\n"
        "EVIDENCE\n"
        f"{pretty_json(_evidence_payload(evidence))}\n\n"
        "REQUIRED OUTPUT SCHEMA\n"
        f"{pretty_json(_review_schema())}"
    )


def contract_prompt(
    *, task: str, allowed_changed_globs: list[str], evidence: list[EvidenceRecord]
) -> str:
    schema = {
        "requirements": ["explicit requested outcomes"],
        "invariants": ["behavior or properties that must remain true"],
        "ambiguities": ["material unresolved interpretations"],
        "acceptance_checks": ["machine-checkable or reviewable checks"],
    }
    return (
        "TASK\n"
        f"{task}\n\n"
        "ALLOWED CHANGED PATH GLOBS\n"
        f"{pretty_json(allowed_changed_globs)}\n\n"
        "EVIDENCE\n"
        f"{pretty_json(_evidence_payload(evidence))}\n\n"
        "REQUIRED OUTPUT SCHEMA\n"
        f"{pretty_json(schema)}"
    )


def critic_prompt(
    *,
    task: str,
    contract: Contract,
    deterministic_findings: list[Finding],
    evidence: list[EvidenceRecord],
) -> str:
    finding_payload = [finding.model_dump(mode="json") for finding in deterministic_findings]
    return (
        "TASK\n"
        f"{task}\n\n"
        "COMPILED CONTRACT\n"
        f"{pretty_json(contract.model_dump(mode='json'))}\n\n"
        "DETERMINISTIC FINDINGS\n"
        f"{pretty_json(finding_payload)}\n\n"
        "EVIDENCE\n"
        f"{pretty_json(_evidence_payload(evidence))}\n\n"
        "REQUIRED OUTPUT SCHEMA\n"
        f"{pretty_json(_review_schema())}"
    )
