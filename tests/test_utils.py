from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mergeproof.utils import (
    atomic_write_text,
    canonical_json,
    exclusive_atomic_write_text,
    extract_json_object,
    redact_secrets,
    stable_evidence_id,
    write_json,
)


def test_extract_json_object_accepts_fenced_json() -> None:
    assert extract_json_object('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_object_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        extract_json_object("[1, 2, 3]")


def test_secret_redaction_preserves_assignment_structure() -> None:
    secret = "DEMO" + "_ONLY_NOT_A_REAL_CREDENTIAL_123456"

    assert redact_secrets(f'api_key = "{secret}"') == 'api_key = "[REDACTED_SECRET]"'
    assert redact_secrets(f'{{"api_key": "{secret}"}}') == ('{"api_key": "[REDACTED_SECRET]"}')
    assert redact_secrets(f"token: {secret}") == "token: [REDACTED_SECRET]"


def test_secret_redaction_masks_standalone_provider_tokens() -> None:
    token = "ghp" + "_" + "A" * 32
    assert redact_secrets(f"credential={token}") == "credential=[REDACTED_SECRET]"


def test_atomic_write_does_not_follow_a_predictable_temp_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    predictable = tmp_path / ".response.json.tmp"
    predictable.symlink_to(victim)
    response = tmp_path / "response.json"

    write_json(response, {"status": "complete"})

    assert json.loads(response.read_text(encoding="utf-8")) == {"status": "complete"}
    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert predictable.is_symlink()


def test_concurrent_atomic_writes_leave_one_complete_payload(tmp_path: Path) -> None:
    target = tmp_path / "shared.json"
    payloads = [{"writer": index, "content": "x" * 1_000} for index in range(24)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda payload: write_json(target, payload), payloads))

    assert json.loads(target.read_text(encoding="utf-8")) in payloads
    assert not list(tmp_path.glob(".shared.json.*.tmp"))


def test_atomic_write_rejects_a_symlink_destination(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    destination.symlink_to(victim)

    with pytest.raises(OSError, match="regular file path"):
        atomic_write_text(destination, "replace me\n")

    assert victim.read_text(encoding="utf-8") == "keep me\n"


def test_exclusive_atomic_write_never_replaces_existing_content(tmp_path: Path) -> None:
    destination = tmp_path / "context.md"
    destination.write_text("human-authored\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        exclusive_atomic_write_text(destination, "generated\n", mode=0o644)

    assert destination.read_text(encoding="utf-8") == "human-authored\n"


def test_concurrent_exclusive_writers_publish_exactly_one_payload(tmp_path: Path) -> None:
    destination = tmp_path / "context.md"
    payloads = [f"writer-{index}\n" for index in range(24)]

    def publish(payload: str) -> bool:
        try:
            exclusive_atomic_write_text(destination, payload, mode=0o644)
        except FileExistsError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(publish, payloads))

    assert outcomes.count(True) == 1
    assert destination.read_text(encoding="utf-8") in payloads
    assert not list(tmp_path.glob(".context.md.*.tmp"))


@given(st.dictionaries(st.text(max_size=20), st.integers(), max_size=8))
def test_canonical_json_is_round_trip_stable(value: dict[str, int]) -> None:
    serialized = canonical_json(value)
    assert canonical_json(json.loads(serialized)) == serialized


@given(st.text(max_size=100), st.text(max_size=100), st.text(max_size=100))
def test_evidence_id_is_deterministic(kind: str, source: str, content: str) -> None:
    assert stable_evidence_id(kind, source, content) == stable_evidence_id(kind, source, content)
