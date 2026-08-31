from __future__ import annotations

import os
import subprocess
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mergeproof.utils import bounded_text, redact_secrets

from .identity import fingerprint_request
from .models import (
    DriftProofAgentProtocolResponse,
    DriftProofErrorResponse,
    DriftProofFingerprintResponse,
    DriftProofNavigationResponse,
    DriftProofReviewRequest,
)

ReviewRequest = DriftProofReviewRequest
ReviewResponse = DriftProofNavigationResponse | DriftProofErrorResponse

_PATH_FIELDS = (
    "project",
    "context",
    "output",
    "work_root",
    "agent_record_dir",
    "agent_replay_dir",
    "response_file",
)


class SDKProtocolError(RuntimeError):
    """Raised when the installed machine protocol is absent or inconsistent."""


def _tool_version() -> str:
    try:
        return version("driftproof")
    except PackageNotFoundError:
        return "0+unknown"


def _resolve_request(request: ReviewRequest, base_dir: Path) -> ReviewRequest:
    updates: dict[str, str | None] = {}
    for field in _PATH_FIELDS:
        value = getattr(request, field)
        if value is None:
            updates[field] = None
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        updates[field] = str(path.resolve(strict=False))
    return request.model_copy(update=updates)


def fingerprint_for_agent(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
) -> DriftProofFingerprintResponse:
    """Compute stable candidate/context identity without executing candidate code."""

    base = (base_dir or Path.cwd()).expanduser().resolve(strict=False)
    resolved = _resolve_request(request, base)
    return fingerprint_request(resolved, tool_version=_tool_version())


def review_for_agent(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
    process_timeout_seconds: float | None = None,
    unique_default_run: bool = True,
) -> ReviewResponse:
    """Run the strict one-object agent protocol without shell or prose parsing.

    When callers have not selected an output or run ID, the SDK assigns a
    process-unique run ID so concurrent agents cannot target the same default
    bundle directory. Semantic request identity still excludes that control ID.
    """

    base = (base_dir or Path.cwd()).expanduser().resolve(strict=False)
    resolved = _resolve_request(request, base)
    if unique_default_run and resolved.output is None and resolved.run_id is None:
        resolved = resolved.model_copy(
            update={"run_id": f"sdk-{os.getpid()}-{uuid.uuid4().hex[:12]}"}
        )

    command = [sys.executable, "-m", "driftproof.cli", "agent", "-"]
    timeout = process_timeout_seconds
    if timeout is None:
        timeout = max(float(resolved.timeout_seconds) + 60.0, 90.0)
    try:
        completed = subprocess.run(
            command,
            cwd=base,
            input=resolved.model_dump_json(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SDKProtocolError(f"DriftProof agent process exceeded {timeout:.3g} seconds") from exc
    except OSError as exc:
        raise SDKProtocolError(f"could not start DriftProof agent process: {exc}") from exc

    try:
        response = DriftProofAgentProtocolResponse.model_validate_json(completed.stdout).root
    except ValueError as exc:
        detail = redact_secrets(bounded_text(completed.stderr, 2_000)).strip()
        raise SDKProtocolError(
            "DriftProof did not emit one valid protocol object" + (f": {detail}" if detail else "")
        ) from exc
    if completed.returncode != response.exit_code:
        raise SDKProtocolError(
            "process exit code does not match the validated DriftProof response: "
            f"{completed.returncode} != {response.exit_code}"
        )
    return response


__all__ = [
    "ReviewRequest",
    "ReviewResponse",
    "SDKProtocolError",
    "fingerprint_for_agent",
    "review_for_agent",
]
