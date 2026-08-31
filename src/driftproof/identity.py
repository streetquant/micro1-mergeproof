from __future__ import annotations

from pathlib import Path

from mergeproof.utils import canonical_json, sha256_text

from .models import DriftProofFingerprintResponse, DriftProofReviewRequest
from .project import snapshot_project

_CONTROL_FIELDS = {"output", "work_root", "response_file", "replace_output", "run_id"}


def request_identity(request: DriftProofReviewRequest) -> str:
    """Hash semantic review configuration while excluding control destinations."""

    payload = request.model_dump(mode="json")
    for field in _CONTROL_FIELDS:
        payload.pop(field, None)
    return sha256_text(canonical_json(payload))


def fingerprint_request(
    request: DriftProofReviewRequest,
    *,
    tool_version: str,
) -> DriftProofFingerprintResponse:
    """Bind candidate, visible context and review configuration without executing code."""

    project = Path(request.project).expanduser().resolve(strict=False)
    context = (
        Path(request.context).expanduser().resolve(strict=False)
        if request.context is not None
        else project / "BUSINESS_CONTEXT.md"
    )
    if not project.is_dir() or project.is_symlink():
        raise ValueError(f"project must be a regular directory: {project}")
    if not context.is_file() or context.is_symlink():
        raise ValueError(f"business context must be a regular UTF-8 file: {context}")

    resolved = request.model_copy(update={"project": str(project), "context": str(context)})
    snapshot = snapshot_project(project)
    context_text = context.read_text(encoding="utf-8", errors="replace")
    context_sha256 = sha256_text(context_text)
    configuration_sha256 = request_identity(resolved)
    content_sha256 = sha256_text(
        canonical_json(
            {
                "protocol": "driftproof.content-fingerprint.v1",
                "tool_version": tool_version,
                "configuration_request_sha256": configuration_sha256,
                "project_sha256": snapshot.tree_sha256,
                "context_sha256": context_sha256,
            }
        )
    )
    return DriftProofFingerprintResponse(
        tool_version=tool_version,
        configuration_request_sha256=configuration_sha256,
        content_fingerprint_sha256=content_sha256,
        project=str(project),
        context=str(context),
        project_sha256=snapshot.tree_sha256,
        context_sha256=context_sha256,
        request=resolved,
    )
