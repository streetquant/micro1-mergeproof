from __future__ import annotations

from .models import EvidenceRecord, FindingCategory
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


def baseline_prompt(
    *, task: str, allowed_changed_globs: list[str], evidence: list[EvidenceRecord]
) -> str:
    schema = {
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
    evidence_payload = [
        {"id": item.id, "kind": item.kind, "source": item.source, "content": item.content}
        for item in evidence
    ]
    return (
        "TASK\n"
        f"{task}\n\n"
        "ALLOWED CHANGED PATH GLOBS\n"
        f"{pretty_json(allowed_changed_globs)}\n\n"
        "EVIDENCE\n"
        f"{pretty_json(evidence_payload)}\n\n"
        "REQUIRED OUTPUT SCHEMA\n"
        f"{pretty_json(schema)}"
    )
