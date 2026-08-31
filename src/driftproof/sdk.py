from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mergeproof.utils import bounded_text, redact_secrets

from .identity import fingerprint_request, request_identity
from .models import (
    DriftProofAgentProtocolResponse,
    DriftProofErrorResponse,
    DriftProofFingerprintResponse,
    DriftProofNavigationResponse,
    DriftProofResponseVerification,
    DriftProofReviewRequest,
)
from .response import verify_response_file, verify_response_object

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
_RUN_ID_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,46}$")


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


def configuration_request_sha256_for_agent(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
) -> str:
    """Compute the exact CLI semantic request identity without executing candidate code."""

    base = (base_dir or Path.cwd()).expanduser().resolve(strict=False)
    resolved = _resolve_request(request, base)
    project = Path(resolved.project).expanduser().resolve(strict=False)
    context = (
        Path(resolved.context).expanduser().resolve(strict=False)
        if resolved.context is not None
        else project / "BUSINESS_CONTEXT.md"
    )
    effective = resolved.model_copy(
        update={
            "project": str(project),
            "context": str(context),
        }
    )
    return request_identity(effective)


def fingerprint_for_agent(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
) -> DriftProofFingerprintResponse:
    """Compute stable candidate/context identity without executing candidate code."""

    base = (base_dir or Path.cwd()).expanduser().resolve(strict=False)
    resolved = _resolve_request(request, base)
    return fingerprint_request(resolved, tool_version=_tool_version())


def stable_run_id_for_agent(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
    prefix: str = "retry",
) -> str:
    """Derive a stable control run ID from the current content fingerprint."""

    if _RUN_ID_PREFIX.fullmatch(prefix) is None:
        raise ValueError(
            "run ID prefix must be 1-47 characters containing letters, digits, '.', '_', or '-'"
        )
    fingerprint = fingerprint_for_agent(request, base_dir=base_dir)
    return f"{prefix}-{fingerprint.content_fingerprint_sha256[:16]}"


def request_with_stable_run_id(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
    prefix: str = "retry",
) -> ReviewRequest:
    """Return a copy whose run ID is stable for the current immutable review input."""

    return request.model_copy(
        update={
            "run_id": stable_run_id_for_agent(
                request,
                base_dir=base_dir,
                prefix=prefix,
            )
        }
    )


def verify_response_for_agent(
    response: ReviewResponse | Path,
    *,
    expected_request_sha256: str | None = None,
) -> DriftProofResponseVerification:
    """Authenticate a response object/file and every claim bound by its referenced bundle."""

    if isinstance(response, Path):
        return verify_response_file(
            response,
            expected_request_sha256=expected_request_sha256,
        )
    return verify_response_object(
        response,
        expected_request_sha256=expected_request_sha256,
    )


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


def review_and_verify_for_agent(
    request: ReviewRequest,
    *,
    base_dir: Path | None = None,
    process_timeout_seconds: float | None = None,
    unique_default_run: bool = True,
) -> tuple[ReviewResponse, DriftProofResponseVerification]:
    """Run the machine review and independently bind its response to the result bundle."""

    base = (base_dir or Path.cwd()).expanduser().resolve(strict=False)
    expected_request_sha256 = configuration_request_sha256_for_agent(
        request,
        base_dir=base,
    )
    response = review_for_agent(
        request,
        base_dir=base,
        process_timeout_seconds=process_timeout_seconds,
        unique_default_run=unique_default_run,
    )
    if response.response_file is not None:
        response_path = Path(response.response_file)
        if response_path.is_symlink() or not response_path.is_file():
            raise SDKProtocolError(
                f"DriftProof response file is missing or unsafe: {response_path}"
            )
        try:
            recorded = DriftProofAgentProtocolResponse.model_validate_json(
                response_path.read_text(encoding="utf-8")
            ).root
        except (OSError, ValueError) as exc:
            raise SDKProtocolError(
                f"DriftProof response file is not one valid protocol object: {response_path}"
            ) from exc
        if recorded != response:
            raise SDKProtocolError(
                "atomically written response file does not match the validated process response"
            )
        verification = verify_response_for_agent(
            response_path,
            expected_request_sha256=expected_request_sha256,
        )
    else:
        verification = verify_response_for_agent(
            response,
            expected_request_sha256=expected_request_sha256,
        )
    return response, verification


__all__ = [
    "ReviewRequest",
    "ReviewResponse",
    "SDKProtocolError",
    "configuration_request_sha256_for_agent",
    "fingerprint_for_agent",
    "request_with_stable_run_id",
    "review_and_verify_for_agent",
    "review_for_agent",
    "stable_run_id_for_agent",
    "verify_response_for_agent",
]
