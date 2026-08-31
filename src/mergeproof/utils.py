from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<name>api[_-]?key|access[_-]?token|auth[_-]?token|token|secret)\b"
    r"(?P<name_quote>['\"]?)(?P<separator>\s*[=:]\s*)"
    r"(?P<value_quote>['\"]?)(?P<value>[^\s'\"\n]{16,})(?P=value_quote)"
)
_SECRET_TOKEN_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-(?:live|prod|test)_[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def stable_evidence_id(kind: str, source: str, content: str) -> str:
    digest = sha256_text(f"{kind}\0{source}\0{content}")[:16]
    safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "_", source).strip("_")[:48] or "root"
    return f"{kind}:{safe_source}:{digest}"


def stable_request_hash(agent: str, model: str, system: str, user: str) -> str:
    return sha256_text(
        canonical_json({"agent": agent, "model": model, "system": system, "user": user})
    )


def extract_json_object(raw: str) -> dict[str, Any]:
    text = _JSON_FENCE.sub("", raw.strip()).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response did not contain a JSON object") from None
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response JSON must be an object")
    return value


def _redact_assignment(match: re.Match[str]) -> str:
    return (
        f"{match.group('name')}{match.group('name_quote')}"
        f"{match.group('separator')}{match.group('value_quote')}"
        f"[REDACTED_SECRET]{match.group('value_quote')}"
    )


def redact_secrets(text: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(_redact_assignment, text)
    for pattern in _SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def bounded_text(text: str, limit: int = 120_000) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n...[TRUNCATED {len(text) - limit} CHARS]...\n\n{tail}"


def atomic_write_text(path: Path, payload: str) -> None:
    """Atomically replace one regular text file without a predictable temp path.

    A unique same-directory temporary file avoids collisions between concurrent
    agents and prevents a pre-created ``.<name>.tmp`` symlink from redirecting a
    write. ``os.replace`` publishes the complete file in one operation.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"output must be a regular file path: {path}")

    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, pretty_json(value) + "\n")


def model_dump_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(k): model_dump_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [model_dump_jsonable(v) for v in value]
    return value
