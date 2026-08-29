from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mergeproof.utils import (
    canonical_json,
    extract_json_object,
    redact_secrets,
    stable_evidence_id,
)


def test_extract_json_object_accepts_fenced_json() -> None:
    assert extract_json_object('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_object_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        extract_json_object("[1, 2, 3]")


def test_secret_redaction_removes_credential_shaped_assignment() -> None:
    text = 'api_key = "DEMO_ONLY_NOT_A_REAL_CREDENTIAL_123456"'
    redacted = redact_secrets(text)
    assert "DEMO_ONLY_NOT_A_REAL_CREDENTIAL_123456" not in redacted
    assert "[REDACTED_SECRET]" in redacted


@given(st.dictionaries(st.text(max_size=20), st.integers(), max_size=8))
def test_canonical_json_is_round_trip_stable(value: dict[str, int]) -> None:
    serialized = canonical_json(value)
    assert canonical_json(json.loads(serialized)) == serialized


@given(st.text(max_size=100), st.text(max_size=100), st.text(max_size=100))
def test_evidence_id_is_deterministic(kind: str, source: str, content: str) -> None:
    assert stable_evidence_id(kind, source, content) == stable_evidence_id(kind, source, content)
