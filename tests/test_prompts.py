from __future__ import annotations

import json

from mergeproof.models import EvidenceRecord
from mergeproof.prompts import (
    _MAX_EVIDENCE_ITEM_CHARS,
    _MAX_EVIDENCE_RECORDS,
    _MAX_EVIDENCE_TOTAL_CHARS,
    _evidence_payload,
)
from mergeproof.utils import sha256_text, stable_evidence_id


def evidence(kind: str, source: str, content: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=stable_evidence_id(kind, source, content),
        kind=kind,
        source=source,
        sha256=sha256_text(content),
        content=content,
    )


def test_clean_small_evidence_preserves_historical_list_shape() -> None:
    item = evidence("task", "task.md", "Return two.")

    payload = _evidence_payload([item])

    assert payload == [
        {
            "id": item.id,
            "kind": "task",
            "source": "task.md",
            "content": "Return two.",
        }
    ]


def test_provider_evidence_projection_is_redacted_bounded_and_deterministic() -> None:
    secret = "sk-" + "live_" + "ABCDEFGHIJKLMNOPQRSTUV"
    records = [
        evidence("file", f"candidate/file-{index:03d}.py", "x" * 10_000)
        for index in range(_MAX_EVIDENCE_RECORDS + 2)
    ]
    task = evidence("task", "task.md", f"Do not expose {secret}.")
    records.append(task)

    first = _evidence_payload(records)
    second = _evidence_payload(records)

    assert first == second
    assert isinstance(first, dict)
    projected = first["records"]
    projection = first["projection"]
    assert isinstance(projected, list)
    assert isinstance(projection, dict)
    assert projected[0]["id"] == task.id
    assert len(projected) == _MAX_EVIDENCE_RECORDS
    assert projection["omitted_records"] == 3
    assert projection["redacted_records"] >= 1
    assert projection["truncated_records"] >= 1
    assert all(len(str(item["content"])) <= _MAX_EVIDENCE_ITEM_CHARS for item in projected)
    assert sum(len(str(item["content"])) for item in projected) <= _MAX_EVIDENCE_TOTAL_CHARS
    serialized = json.dumps(first, sort_keys=True)
    assert secret not in serialized
    assert "[REDACTED_SECRET]" in serialized
