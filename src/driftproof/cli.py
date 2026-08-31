from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, cast

import typer
from rich.console import Console

from mergeproof.providers import ProviderError, build_provider
from mergeproof.sandbox import _bubblewrap_available
from mergeproof.utils import (
    atomic_write_text,
    canonical_json,
    exclusive_atomic_write_text,
    pretty_json,
    redact_secrets,
    sha256_text,
    write_json,
)

from . import __version__
from .agent import ContractClarifier
from .certificate import verify_certificate
from .contracts import compile_contract
from .gate import GateExecutionError, review_project
from .models import (
    ApprovalCertificate,
    DriftProofAgentProtocolResponse,
    DriftProofContextTemplateResponse,
    DriftProofErrorResponse,
    DriftProofNavigationResponse,
    DriftProofOnboardingResponse,
    DriftProofPreflightResponse,
    DriftProofReviewRequest,
    GateReport,
    RuleKind,
    Verdict,
)
from .project import snapshot_project
from .reporting import (
    GateBundleError,
    prepare_gate_output,
    verify_gate_bundle,
)
from .templates import CONTEXT_TEMPLATE

Isolation = Literal["auto", "disposable_copy", "bubblewrap"]
_EXTERNAL_PROVIDERS = {"gemini", "groq", "openai-compatible", "openrouter"}
_MAX_REQUEST_BYTES = 1_000_000
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RECOMMENDED_ACTION: dict[Verdict, str] = {
    Verdict.APPROVE: "human_approval",
    Verdict.REJECT: "repair_required",
    Verdict.HUMAN_REVIEW: "evidence_or_human_escalation",
}
_SCHEMA_ALIASES = {
    "request": "request",
    "review-request": "request",
    "review_request": "request",
    "preflight-response": "preflight_response",
    "preflight_response": "preflight_response",
    "context-template-response": "context_template_response",
    "context_template_response": "context_template_response",
    "onboard-response": "onboarding_response",
    "onboard_response": "onboarding_response",
    "onboarding-response": "onboarding_response",
    "onboarding_response": "onboarding_response",
    "report": "report",
    "gate-report": "report",
    "gate_report": "report",
    "certificate": "certificate",
    "approval-certificate": "certificate",
    "approval_certificate": "certificate",
    "navigation-response": "navigation_response",
    "navigation_response": "navigation_response",
    "error-response": "error_response",
    "error_response": "error_response",
    "agent-response": "agent_response",
    "agent_response": "agent_response",
}


class ExternalProviderConsentRequired(ValueError):
    """Raised when business context would leave the local machine without consent."""


app = typer.Typer(
    no_args_is_help=True,
    help="Independent, evidence-grounded release gate for agent-authored dbt repairs.",
)
console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    """Review dbt repairs without granting merge or deployment authority."""


def _verdict_exit_code(verdict: Verdict) -> Literal[0, 10, 20]:
    if verdict == Verdict.APPROVE:
        return 0
    if verdict == Verdict.REJECT:
        return 10
    return 20


def _normalize_isolation(value: str) -> Isolation:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in {"auto", "disposable_copy", "bubblewrap"}:
        raise ValueError(
            f"unsupported isolation {value!r}; choose auto, bubblewrap, or disposable_copy"
        )
    return cast(Isolation, normalized)


def _validate_timeout(value: int) -> int:
    if value < 1 or value > 900:
        raise ValueError("timeout-seconds must be between 1 and 900")
    return value


def _validate_run_id(value: str | None) -> str | None:
    if value is None:
        return None
    if _RUN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "run-id must be 1-64 characters and contain only letters, digits, '.', '_', or '-'"
        )
    return value


def _absolute_path(path: Path | None) -> Path | None:
    return path.expanduser().resolve(strict=False) if path is not None else None


def _resolve_control_paths(
    project: Path,
    output: Path | None,
    work_root: Path | None,
    run_id: str | None,
) -> tuple[Path, Path]:
    resolved_project = project.expanduser().resolve(strict=False)
    project_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", resolved_project.name).strip("-._")
    project_name = project_name[:48] or "candidate"
    project_identity = sha256_text(str(resolved_project))[:12]
    run_suffix = f"-{run_id}" if run_id is not None else ""
    identity = f"{project_name}-{project_identity}{run_suffix}"
    temporary_root = Path(tempfile.gettempdir()) / "driftproof"
    selected_output = output or temporary_root / "reviews" / identity
    selected_work_root = work_root or temporary_root / "runs" / identity
    return (
        selected_output.expanduser().resolve(strict=False),
        selected_work_root.expanduser().resolve(strict=False),
    )


def _load_review_request(source: str) -> tuple[DriftProofReviewRequest, Path]:
    if source == "-":
        payload = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        base = Path.cwd()
    else:
        path = Path(source).expanduser()
        if path.is_symlink() or not path.is_file():
            raise GateExecutionError(f"request must be a regular JSON file or '-': {path}")
        payload = path.read_bytes()
        base = path.parent.resolve(strict=False)
    if len(payload) > _MAX_REQUEST_BYTES:
        raise GateExecutionError(f"request exceeds {_MAX_REQUEST_BYTES} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateExecutionError("request must be UTF-8 JSON") from exc
    try:
        request = DriftProofReviewRequest.model_validate_json(text)
    except ValueError as exc:
        raise ValueError(f"invalid DriftProof request: {exc}") from exc
    return request, base


def _resolve_request_path(value: str | None, base: Path) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve(strict=False))


def _resolve_review_request(
    request: DriftProofReviewRequest,
    base: Path,
) -> DriftProofReviewRequest:
    return request.model_copy(
        update={
            "project": _resolve_request_path(request.project, base),
            "context": _resolve_request_path(request.context, base),
            "output": _resolve_request_path(request.output, base),
            "work_root": _resolve_request_path(request.work_root, base),
            "agent_record_dir": _resolve_request_path(request.agent_record_dir, base),
            "agent_replay_dir": _resolve_request_path(request.agent_replay_dir, base),
            "response_file": _resolve_request_path(request.response_file, base),
            "run_id": _validate_run_id(request.run_id),
        }
    )


def _request_identity(request: DriftProofReviewRequest) -> str:
    payload = request.model_dump(mode="json")
    for control_field in ("output", "work_root", "response_file", "replace_output", "run_id"):
        payload.pop(control_field, None)
    return sha256_text(canonical_json(payload))


def _paths_overlap(left: Path, right: Path) -> bool:
    resolved_left = left.resolve(strict=False)
    resolved_right = right.resolve(strict=False)
    return (
        resolved_left == resolved_right
        or resolved_left.is_relative_to(resolved_right)
        or resolved_right.is_relative_to(resolved_left)
    )


def _validate_project_paths(
    *,
    project: Path,
    context: Path | None,
    output: Path,
    work_root: Path,
    response_file: Path | None,
    agent_record_dir: Path | None,
    agent_replay_dir: Path | None,
) -> None:
    if not project.is_dir() or project.is_symlink():
        raise GateExecutionError(f"project must be a regular directory: {project}")
    project_root = project.resolve()
    if context is not None and (not context.is_file() or context.is_symlink()):
        raise GateExecutionError(f"business context must be a regular UTF-8 file: {context}")

    controls = [
        ("bundle output", output),
        ("work root", work_root),
        ("response file", response_file),
        ("provider record directory", agent_record_dir),
        ("provider replay directory", agent_replay_dir),
    ]
    present_controls = [(label, path) for label, path in controls if path is not None]
    for label, path in present_controls:
        resolved = path.resolve(strict=False)
        if resolved == project_root or resolved.is_relative_to(project_root):
            raise GateExecutionError(
                f"{label} must be outside the candidate project so review cannot mutate source: {path}"
            )

    for index, (left_label, left) in enumerate(present_controls):
        for right_label, right in present_controls[index + 1 :]:
            if _paths_overlap(left, right):
                raise GateExecutionError(
                    f"{left_label} and {right_label} must be disjoint: {left} vs {right}"
                )

    if context is not None:
        for label, path in present_controls:
            if _paths_overlap(context, path):
                raise GateExecutionError(
                    f"business context and {label} must be disjoint: {context} vs {path}"
                )

    if response_file is not None and (
        response_file.is_symlink() or (response_file.exists() and not response_file.is_file())
    ):
        raise GateExecutionError(f"response file must be a regular file path: {response_file}")


def _safe_error_response_file(
    response_file: Path | None,
    *forbidden: Path | None,
) -> Path | None:
    """Return a response destination only when an error write cannot alias protected state."""

    if response_file is None:
        return None
    try:
        if response_file.is_symlink() or (response_file.exists() and not response_file.is_file()):
            return None
        if any(path is not None and _paths_overlap(response_file, path) for path in forbidden):
            return None
    except OSError:
        return None
    return response_file


def _error_descriptor(exc: Exception) -> tuple[str, str, bool, bool]:
    if isinstance(exc, ExternalProviderConsentRequired):
        return (
            "external_provider_consent_required",
            "Obtain repository-owner authorization, then pass --allow-external-provider, or omit the clarifier/replay it locally.",
            False,
            True,
        )
    if isinstance(exc, ProviderError):
        return (
            "provider_unavailable",
            "Check provider readiness and retry the same immutable candidate with bounded retries.",
            True,
            True,
        )
    if isinstance(exc, GateBundleError):
        return (
            "bundle_invalid",
            "Do not trust the bundle. Choose a dedicated output path or explicitly replace a prior DriftProof bundle.",
            False,
            True,
        )
    if isinstance(exc, GateExecutionError):
        return (
            "review_execution_failed",
            "Correct the project, context, isolation, dbt, or path problem and rerun.",
            False,
            True,
        )
    if isinstance(exc, OSError):
        return (
            "filesystem_error",
            "Check that all requested paths are writable regular files or directories.",
            False,
            True,
        )
    if isinstance(exc, ValueError):
        return (
            "validation_failed",
            "Correct the invalid value without weakening the review contract.",
            False,
            True,
        )
    return (
        "internal_error",
        "Inspect local diagnostics and treat this run as invalid before retrying.",
        False,
        False,
    )


def _error_payload(
    exc: Exception,
    *,
    context: str,
    response_file: Path | None,
    request_sha256: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    error_code, hint, retryable, known = _error_descriptor(exc)
    return DriftProofErrorResponse(
        tool_version=__version__,
        context=context,
        error=type(exc).__name__,
        error_code=error_code,
        detail=(
            redact_secrets(str(exc))[:4_000]
            if known
            else "Unexpected internal error. The review is invalid; inspect local diagnostics before retrying."
        ),
        hint=hint,
        retryable=retryable,
        request_sha256=request_sha256,
        run_id=run_id,
        response_file=str(response_file) if response_file is not None else None,
        human_approval_required=True,
        consequential_action_taken=False,
    ).model_dump(mode="json")


def _write_response_file(path: Path, payload: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OSError(f"response file must be a regular file path: {path}")
    write_json(path, payload)


def _emit_payload(
    payload: dict[str, Any],
    *,
    json_output: bool,
    response_file: Path | None,
) -> None:
    if response_file is not None:
        _write_response_file(response_file, payload)
    if json_output:
        typer.echo(pretty_json(payload))


def _fail(
    exc: Exception,
    *,
    json_output: bool,
    context: str,
    response_file: Path | None = None,
    request_sha256: str | None = None,
    run_id: str | None = None,
) -> NoReturn:
    payload = _error_payload(
        exc,
        context=context,
        response_file=response_file,
        request_sha256=request_sha256,
        run_id=run_id,
    )
    response_error: Exception | None = None
    if response_file is not None:
        try:
            _write_response_file(response_file, payload)
        except Exception as write_exc:
            response_error = write_exc
    if json_output:
        typer.echo(pretty_json(payload))
    else:
        console.print(f"[bold red]{context} could not complete:[/bold red] {payload['detail']}")
        console.print(f"Next step: {payload['hint']}")
        console.print("No report, certificate, or consequential action should be trusted.")
    if response_error is not None:
        console.print(
            "[bold red]Response file could not be written:[/bold red] "
            f"{redact_secrets(str(response_error))[:1_000]}"
        )
    raise typer.Exit(code=30) from exc


def _navigation_payload(
    report: GateReport,
    certificate: ApprovalCertificate,
    output: Path,
    *,
    request: DriftProofReviewRequest,
    resolved_context: Path,
    response_file: Path | None,
) -> dict[str, Any]:
    verification = verify_gate_bundle(output)
    return DriftProofNavigationResponse(
        tool_version=__version__,
        request_sha256=_request_identity(request),
        run_id=request.run_id,
        candidate_id=report.candidate_id,
        verdict=report.verdict,
        exit_code=_verdict_exit_code(report.verdict),
        recommended_action=cast(Any, _RECOMMENDED_ACTION[report.verdict]),
        summary=report.summary,
        project=request.project,
        context=str(resolved_context),
        project_sha256=report.project_sha256,
        context_sha256=report.context_sha256,
        build_worktree_sha256=report.build.worktree_sha256,
        bundle=str(output),
        report=str(output / "gate-report.json"),
        certificate=str(output / "approval-certificate.json"),
        manifest=str(output / "manifest.json"),
        human_report=str(output / "report.html"),
        human_report_markdown=str(output / "report.md"),
        certificate_sha256=certificate.self_sha256,
        bundle_manifest_sha256=str(verification["bundle_manifest_sha256"]),
        bundle_verified=True,
        failed_checks=len(report.failed_check_ids),
        inconclusive_checks=len(report.inconclusive_check_ids),
        failed_check_ids=report.failed_check_ids,
        inconclusive_check_ids=report.inconclusive_check_ids,
        verify_argv=["driftproof", "verify-report", str(output)],
        response_file=str(response_file) if response_file is not None else None,
        human_approval_required=True,
        consequential_action_taken=False,
    ).model_dump(mode="json")


def _schema_catalog() -> dict[str, dict[str, Any]]:
    return {
        "request": DriftProofReviewRequest.model_json_schema(),
        "preflight_response": DriftProofPreflightResponse.model_json_schema(),
        "context_template_response": DriftProofContextTemplateResponse.model_json_schema(),
        "onboarding_response": DriftProofOnboardingResponse.model_json_schema(),
        "report": GateReport.model_json_schema(),
        "certificate": ApprovalCertificate.model_json_schema(),
        "navigation_response": DriftProofNavigationResponse.model_json_schema(),
        "error_response": DriftProofErrorResponse.model_json_schema(),
        "agent_response": DriftProofAgentProtocolResponse.model_json_schema(),
    }


@app.command("compile-contract")
def compile_contract_command(context: Annotated[Path, typer.Argument()]) -> None:
    """Compile visible business context into typed deterministic rules."""

    try:
        if not context.is_file() or context.is_symlink():
            raise GateExecutionError(f"context must be a regular UTF-8 file: {context}")
        spec = compile_contract(context.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        _fail(exc, json_output=True, context="Contract compilation")
    typer.echo(pretty_json(spec.model_dump(mode="json")))


@app.command("context-template")
def context_template(
    output: Annotated[
        Path | None,
        typer.Option(help="Optional BUSINESS_CONTEXT.md destination."),
    ] = None,
    replace_output: Annotated[
        bool,
        typer.Option(
            "--replace-output", help="Replace an existing regular template file explicitly."
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Print or safely write a compilable business-context template."""

    try:
        resolved_output = _absolute_path(output)
        if resolved_output is not None:
            if resolved_output.is_symlink() or (
                resolved_output.exists() and not resolved_output.is_file()
            ):
                raise GateExecutionError(
                    f"context template output must be a regular file path: {resolved_output}"
                )
            if resolved_output.exists() and not replace_output:
                raise GateExecutionError(
                    f"context template output already exists: {resolved_output}; "
                    "choose another path or pass --replace-output"
                )
            atomic_write_text(resolved_output, CONTEXT_TEMPLATE)
        payload = DriftProofContextTemplateResponse(
            content=CONTEXT_TEMPLATE,
            content_sha256=sha256_text(CONTEXT_TEMPLATE),
            supported_rule_kinds=list(RuleKind),
            output=str(resolved_output) if resolved_output is not None else None,
            consequential_action_taken=False,
        ).model_dump(mode="json")
    except Exception as exc:
        _fail(exc, json_output=json_output, context="Context template generation")
    if json_output:
        typer.echo(pretty_json(payload))
    elif resolved_output is not None:
        console.print(f"Business-context template: {resolved_output}")
    else:
        typer.echo(CONTEXT_TEMPLATE, nl=False)


@app.command("onboard")
def onboard(
    project: Annotated[Path, typer.Argument()] = Path("."),
    context: Annotated[
        Path | None,
        typer.Option(help="Business-context path; defaults to PROJECT/BUSINESS_CONTEXT.md."),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Create a missing context template atomically. Existing files are never replaced.",
        ),
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option(
            help="Optional collision-safe identifier included in the suggested review command."
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan first-time setup or safely create the one missing context template."""

    try:
        lexical_project = project.expanduser()
        if lexical_project.is_symlink() or not lexical_project.is_dir():
            raise GateExecutionError(f"project must be a regular directory: {lexical_project}")
        resolved_project = lexical_project.resolve()
        selected_context = (
            context.expanduser()
            if context is not None
            else (resolved_project / "BUSINESS_CONTEXT.md")
        )
        if selected_context.is_symlink():
            raise GateExecutionError(f"business context may not be a symlink: {selected_context}")
        resolved_context = selected_context.resolve(strict=False)
        if resolved_context.exists() and not resolved_context.is_file():
            raise GateExecutionError(
                f"business context must be a regular UTF-8 file path: {resolved_context}"
            )

        validated_run_id = _validate_run_id(run_id)
        context_created = False
        if not resolved_context.exists() and apply:
            exclusive_atomic_write_text(resolved_context, CONTEXT_TEMPLATE, mode=0o644)
            context_created = True

        context_exists = resolved_context.is_file() and not resolved_context.is_symlink()
        context_sha256 = (
            sha256_text(resolved_context.read_text(encoding="utf-8")) if context_exists else None
        )
        if context_created:
            status: Literal["planning", "context_created", "context_present"] = "context_created"
            recommended_action: Literal[
                "create_business_context", "edit_business_context", "run_preflight"
            ] = "edit_business_context"
        elif context_exists:
            status = "context_present"
            recommended_action = "run_preflight"
        else:
            status = "planning"
            recommended_action = "create_business_context"

        project_text = str(resolved_project)
        context_text = str(resolved_context)
        create_context_argv = (
            [
                "driftproof",
                "onboard",
                project_text,
                "--context",
                context_text,
                "--apply",
                "--json",
            ]
            if not context_exists
            else None
        )
        preflight_argv = [
            "driftproof",
            "preflight",
            project_text,
            "--context",
            context_text,
            "--json",
        ]
        review_argv = [
            "driftproof",
            "review",
            project_text,
            "--context",
            context_text,
        ]
        if validated_run_id is not None:
            review_argv.extend(("--run-id", validated_run_id))

        payload = DriftProofOnboardingResponse(
            status=status,
            project=project_text,
            context=context_text,
            context_exists=context_exists,
            context_created=context_created,
            context_sha256=context_sha256,
            created_files=[context_text] if context_created else [],
            recommended_action=recommended_action,
            create_context_argv=create_context_argv,
            preflight_argv=preflight_argv,
            review_argv=review_argv,
            doctor_argv=["driftproof", "doctor", "--json"],
        ).model_dump(mode="json")
    except Exception as exc:
        _fail(exc, json_output=json_output, context="DriftProof onboarding")

    if json_output:
        typer.echo(pretty_json(payload))
        return
    console.print("[bold]DriftProof onboarding[/bold]")
    console.print(f"Project: {payload['project']}")
    console.print(f"Context: {payload['context']} ({payload['status']})")
    console.print(f"Next state: {payload['recommended_action']}")
    if payload["create_context_argv"] is not None:
        console.print(f"Create template: {shlex.join(payload['create_context_argv'])}")
    if payload["context_created"]:
        console.print("Edit the generated context so it states the real visible business contract.")
    console.print(f"Preflight: {shlex.join(payload['preflight_argv'])}")
    console.print(f"Review: {shlex.join(payload['review_argv'])}")


@app.command("preflight")
def preflight(
    project: Annotated[Path, typer.Argument()] = Path("."),
    context: Annotated[Path | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate a candidate and compile its visible contract without executing dbt."""

    try:
        resolved_project = project.expanduser().resolve(strict=False)
        resolved_context = (
            context.expanduser().resolve(strict=False)
            if context is not None
            else resolved_project / "BUSINESS_CONTEXT.md"
        )
        if resolved_context.is_symlink() or not resolved_context.is_file():
            raise GateExecutionError(
                f"business context must be a regular UTF-8 file: {resolved_context}"
            )
        snapshot = snapshot_project(resolved_project)
        contract = compile_contract(resolved_context.read_text(encoding="utf-8", errors="replace"))
        complete = bool(contract.rules) and not contract.unknown_sentences
        payload = DriftProofPreflightResponse(
            project=str(resolved_project),
            context=str(resolved_context),
            project_sha256=snapshot.tree_sha256,
            context_sha256=contract.context_sha256,
            sql_files=len(snapshot.sql_files),
            yaml_files=len(snapshot.yaml_files),
            models=len(snapshot.model_names),
            references=len(snapshot.refs),
            compiled_rules=len(contract.rules),
            rule_kinds=list(dict.fromkeys(rule.kind for rule in contract.rules)),
            unresolved_sentences=contract.unknown_sentences,
            deterministic_contract_complete=complete,
            review_can_run=True,
            recommended_action="run_review" if complete else "clarify_business_context",
            human_approval_required=True,
            consequential_action_taken=False,
        ).model_dump(mode="json")
    except Exception as exc:
        _fail(exc, json_output=json_output, context="DriftProof preflight")
    if json_output:
        typer.echo(pretty_json(payload))
    else:
        console.print(
            f"[bold]Preflight:[/bold] {payload['compiled_rules']} rules; "
            f"{len(payload['unresolved_sentences'])} unresolved statements"
        )
        console.print(f"Next state: {payload['recommended_action']}")


def _execute_review_request(
    request: DriftProofReviewRequest,
    *,
    json_output: bool,
) -> Literal[0, 10, 20]:
    project = Path(request.project).expanduser().resolve(strict=False)
    resolved_context = (
        Path(request.context).expanduser().resolve(strict=False)
        if request.context is not None
        else project / "BUSINESS_CONTEXT.md"
    )
    output, work_root = _resolve_control_paths(
        project,
        Path(request.output) if request.output is not None else None,
        Path(request.work_root) if request.work_root is not None else None,
        request.run_id,
    )
    response_file = _absolute_path(
        Path(request.response_file) if request.response_file is not None else None
    )
    record_dir = _absolute_path(
        Path(request.agent_record_dir) if request.agent_record_dir is not None else None
    )
    replay_dir = _absolute_path(
        Path(request.agent_replay_dir) if request.agent_replay_dir is not None else None
    )
    effective_request = request.model_copy(
        update={
            "project": str(project),
            "context": str(resolved_context),
            "output": str(output),
            "work_root": str(work_root),
            "agent_record_dir": str(record_dir) if record_dir is not None else None,
            "agent_replay_dir": str(replay_dir) if replay_dir is not None else None,
            "response_file": str(response_file) if response_file is not None else None,
        }
    )
    request_sha256 = _request_identity(effective_request)

    try:
        _validate_project_paths(
            project=project,
            context=resolved_context,
            output=output,
            work_root=work_root,
            response_file=response_file,
            agent_record_dir=record_dir,
            agent_replay_dir=replay_dir,
        )
        clarifier = None
        if effective_request.agent_provider is not None:
            if (
                effective_request.agent_provider in _EXTERNAL_PROVIDERS
                and not effective_request.allow_external_provider
            ):
                raise ExternalProviderConsentRequired(
                    f"clarifier provider {effective_request.agent_provider!r} is external and "
                    "requires explicit consent"
                )
            provider = build_provider(
                provider=effective_request.agent_provider,
                model=effective_request.agent_model,
                record_dir=record_dir,
                replay_dir=replay_dir,
            )
            clarifier = ContractClarifier(provider)
        report, certificate = review_project(
            project,
            context_path=resolved_context,
            output_dir=output,
            work_root=work_root,
            timeout_seconds=effective_request.timeout_seconds,
            isolation=effective_request.isolation,
            allow_unconfined=effective_request.allow_unconfined,
            clarifier=clarifier,
            replace_output=effective_request.replace_output,
        )
        payload = _navigation_payload(
            report,
            certificate,
            output,
            request=effective_request,
            resolved_context=resolved_context,
            response_file=response_file,
        )
        try:
            _emit_payload(payload, json_output=json_output, response_file=response_file)
        except Exception:
            try:
                prepare_gate_output(output, replace=True)
            except Exception as cleanup_exc:
                raise GateBundleError(
                    f"response publication failed and bundle cleanup also failed: {cleanup_exc}"
                ) from cleanup_exc
            raise
        if not json_output:
            console.print(f"[bold]Verdict:[/bold] {report.verdict.value}")
            console.print(report.summary)
            console.print(f"Next state: {_RECOMMENDED_ACTION[report.verdict]}")
            console.print(f"Human report: {output / 'report.html'}")
            console.print(f"Machine report: {output / 'gate-report.json'}")
            console.print(f"Certificate: {certificate.self_sha256}")
            console.print(f"Request identity: {request_sha256}")
            if response_file is not None:
                console.print(f"Machine navigation: {response_file}")
            console.print("A qualified human must authorize any merge or deployment.")
        return _verdict_exit_code(report.verdict)
    except Exception as exc:
        error_response_file = _safe_error_response_file(
            response_file,
            project,
            resolved_context,
            output,
            work_root,
            record_dir,
            replay_dir,
        )
        _fail(
            exc,
            json_output=json_output,
            context="DriftProof review",
            response_file=error_response_file,
            request_sha256=request_sha256,
            run_id=effective_request.run_id,
        )


@app.command()
def review(
    project: Annotated[Path, typer.Argument()] = Path("."),
    context: Annotated[Path | None, typer.Option()] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            help="Bundle directory; defaults to a collision-resistant path under the system temp directory."
        ),
    ] = None,
    work_root: Annotated[
        Path | None,
        typer.Option(help="Disposable execution root; defaults outside the candidate project."),
    ] = None,
    timeout_seconds: Annotated[int, typer.Option()] = 120,
    isolation: Annotated[str, typer.Option()] = "auto",
    allow_unconfined: Annotated[
        bool,
        typer.Option(
            help=(
                "Explicitly permit the weaker disposable-copy runner for a trusted project. "
                "Never use this for untrusted candidate code."
            )
        ),
    ] = False,
    agent_provider: Annotated[
        str | None,
        typer.Option(
            help="Optional bounded clarifier provider: groq, openrouter, gemini, or replay."
        ),
    ] = None,
    agent_model: Annotated[str, typer.Option()] = "openai/gpt-oss-20b",
    agent_record_dir: Annotated[Path | None, typer.Option()] = None,
    agent_replay_dir: Annotated[Path | None, typer.Option()] = None,
    allow_external_provider: Annotated[
        bool,
        typer.Option(
            "--allow-external-provider",
            help="Acknowledge that unresolved business context may be sent to the selected external clarifier provider.",
        ),
    ] = False,
    response_file: Annotated[
        Path | None,
        typer.Option(help="Atomically write the same machine navigation or error object here."),
    ] = None,
    replace_output: Annotated[
        bool,
        typer.Option(
            "--replace-output",
            help="Remove only a recognized prior/partial DriftProof bundle before this run.",
        ),
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option(help="Optional collision-safe identifier for repeated or concurrent runs."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable navigation object."),
    ] = False,
) -> None:
    """Review one dbt candidate and emit a self-verifying human/agent bundle."""

    try:
        request = DriftProofReviewRequest(
            project=str(project),
            context=str(context) if context is not None else None,
            output=str(output) if output is not None else None,
            work_root=str(work_root) if work_root is not None else None,
            timeout_seconds=_validate_timeout(timeout_seconds),
            isolation=_normalize_isolation(isolation),
            allow_unconfined=allow_unconfined,
            agent_provider=agent_provider,
            agent_model=agent_model,
            agent_record_dir=(str(agent_record_dir) if agent_record_dir is not None else None),
            agent_replay_dir=(str(agent_replay_dir) if agent_replay_dir is not None else None),
            allow_external_provider=allow_external_provider,
            response_file=str(response_file) if response_file is not None else None,
            replace_output=replace_output,
            run_id=_validate_run_id(run_id),
        )
        request = _resolve_review_request(request, Path.cwd())
    except Exception as exc:
        _fail(exc, json_output=json_output, context="DriftProof request construction")
    raise typer.Exit(code=_execute_review_request(request, json_output=json_output))


@app.command("agent")
def agent(
    target: Annotated[
        str,
        typer.Argument(help="Candidate project, request JSON, or '-' for request JSON on stdin."),
    ] = ".",
    context: Annotated[Path | None, typer.Option()] = None,
    output: Annotated[Path | None, typer.Option()] = None,
    work_root: Annotated[Path | None, typer.Option()] = None,
    timeout_seconds: Annotated[int, typer.Option()] = 120,
    isolation: Annotated[str, typer.Option()] = "auto",
    allow_unconfined: Annotated[bool, typer.Option()] = False,
    agent_provider: Annotated[str | None, typer.Option()] = None,
    agent_model: Annotated[str, typer.Option()] = "openai/gpt-oss-20b",
    agent_record_dir: Annotated[Path | None, typer.Option()] = None,
    agent_replay_dir: Annotated[Path | None, typer.Option()] = None,
    allow_external_provider: Annotated[
        bool,
        typer.Option("--allow-external-provider"),
    ] = False,
    response_file: Annotated[Path | None, typer.Option()] = None,
    replace_output: Annotated[bool, typer.Option("--replace-output")] = False,
    run_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Machine-first review: exactly one DriftProof protocol JSON object on stdout."""

    try:
        target_path = Path(target).expanduser()
        request_mode = target == "-" or target_path.is_file()
        if request_mode:
            conflicts = {
                "context": context is not None,
                "output": output is not None,
                "work_root": work_root is not None,
                "timeout_seconds": timeout_seconds != 120,
                "isolation": isolation != "auto",
                "allow_unconfined": allow_unconfined,
                "agent_provider": agent_provider is not None,
                "agent_model": agent_model != "openai/gpt-oss-20b",
                "agent_record_dir": agent_record_dir is not None,
                "agent_replay_dir": agent_replay_dir is not None,
                "allow_external_provider": allow_external_provider,
            }
            supplied = sorted(name for name, present in conflicts.items() if present)
            if supplied:
                raise ValueError(
                    "request JSON may not be combined with review options: " + ", ".join(supplied)
                )
            request, base = _load_review_request(target)
            request = _resolve_review_request(request, base)
            updates: dict[str, Any] = {}
            if response_file is not None:
                updates["response_file"] = str(response_file.expanduser().resolve(strict=False))
            if replace_output:
                updates["replace_output"] = True
            if run_id is not None:
                updates["run_id"] = _validate_run_id(run_id)
            if updates:
                request = request.model_copy(update=updates)
        else:
            request = DriftProofReviewRequest(
                project=target,
                context=str(context) if context is not None else None,
                output=str(output) if output is not None else None,
                work_root=str(work_root) if work_root is not None else None,
                timeout_seconds=_validate_timeout(timeout_seconds),
                isolation=_normalize_isolation(isolation),
                allow_unconfined=allow_unconfined,
                agent_provider=agent_provider,
                agent_model=agent_model,
                agent_record_dir=(str(agent_record_dir) if agent_record_dir is not None else None),
                agent_replay_dir=(str(agent_replay_dir) if agent_replay_dir is not None else None),
                allow_external_provider=allow_external_provider,
                response_file=str(response_file) if response_file is not None else None,
                replace_output=replace_output,
                run_id=_validate_run_id(run_id),
            )
            request = _resolve_review_request(request, Path.cwd())
    except Exception as exc:
        safe_response = _safe_error_response_file(_absolute_path(response_file))
        _fail(
            exc,
            json_output=True,
            context="DriftProof request loading",
            response_file=safe_response,
            run_id=_validate_run_id(run_id) if run_id is not None else None,
        )
    raise typer.Exit(code=_execute_review_request(request, json_output=True))


@app.command("verify-report")
def verify_report(
    bundle: Annotated[
        Path,
        typer.Argument(help="DriftProof bundle directory; defaults to driftproof-review."),
    ] = Path("driftproof-review"),
) -> None:
    """Verify a complete DriftProof bundle and emit one JSON object."""

    try:
        payload = verify_gate_bundle(bundle)
    except Exception as exc:
        _fail(exc, json_output=True, context="DriftProof bundle verification")
    typer.echo(pretty_json(payload))


@app.command("inspect")
def inspect_report(
    bundle: Annotated[Path, typer.Argument()] = Path("driftproof-review"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify and summarize a DriftProof bundle."""

    try:
        verification = verify_gate_bundle(bundle)
        report = GateReport.model_validate_json(
            (bundle / "gate-report.json").read_text(encoding="utf-8")
        )
        payload = {
            "schema_version": 1,
            "protocol": "driftproof.inspect.v1",
            "verified": True,
            "candidate_id": report.candidate_id,
            "verdict": report.verdict.value,
            "recommended_action": _RECOMMENDED_ACTION[report.verdict],
            "summary": report.summary,
            "failed_checks": len(report.failed_check_ids),
            "inconclusive_checks": len(report.inconclusive_check_ids),
            "human_report": str(bundle / "report.html"),
            "machine_report": str(bundle / "gate-report.json"),
            "certificate": str(bundle / "approval-certificate.json"),
            "bundle_manifest_sha256": verification["bundle_manifest_sha256"],
            "human_approval_required": True,
            "consequential_action_taken": False,
        }
    except Exception as exc:
        _fail(exc, json_output=json_output, context="DriftProof bundle inspection")
    if json_output:
        typer.echo(pretty_json(payload))
    else:
        console.print(f"[bold]Verdict:[/bold] {payload['verdict']}")
        console.print(str(payload["summary"]))
        console.print(f"Next state: {payload['recommended_action']}")
        console.print(f"Human report: {payload['human_report']}")
        console.print("The verified result still ends at a qualified-human checkpoint.")


@app.command("verify-bundle")
def verify_bundle(
    report_path: Annotated[Path, typer.Argument()],
    certificate_path: Annotated[Path, typer.Argument()],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable verification object."),
    ] = False,
) -> None:
    """Legacy two-file certificate verification; prefer verify-report for full bundles."""

    try:
        if not report_path.is_file() or report_path.is_symlink():
            raise GateBundleError(f"gate report must be a regular file: {report_path}")
        if not certificate_path.is_file() or certificate_path.is_symlink():
            raise GateBundleError(f"certificate must be a regular file: {certificate_path}")
        report = GateReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        certificate = ApprovalCertificate.model_validate_json(
            certificate_path.read_text(encoding="utf-8")
        )
        errors = verify_certificate(report, certificate)
        if errors:
            raise GateBundleError(f"certificate verification failed: {errors}")
        payload = {
            "schema_version": 1,
            "verified": True,
            "candidate_id": report.candidate_id,
            "verdict": report.verdict.value,
            "certificate_sha256": certificate.self_sha256,
            "human_approval_required": True,
            "consequential_action_taken": False,
        }
        typer.echo(pretty_json(payload)) if json_output else console.print(pretty_json(payload))
    except Exception as exc:
        _fail(exc, json_output=json_output, context="DriftProof certificate verification")


@app.command("doctor")
def doctor(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Report local readiness without exposing credential values."""

    dbt_path = shutil.which("dbt")
    bwrap_path = shutil.which("bwrap")
    checks: dict[str, Any] = {
        "python": {
            "ready": sys.version_info >= (3, 11),
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "dbt": {"ready": dbt_path is not None, "path": dbt_path},
        "bubblewrap": {
            "installed": bwrap_path is not None,
            "ready": _bubblewrap_available(),
            "path": bwrap_path,
        },
        "providers": {
            "groq_configured": bool(os.getenv("GROQ_API_KEY")),
            "openrouter_configured": bool(os.getenv("OPENROUTER_API_KEY")),
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
            "note": "A provider is optional; deterministic DriftProof requires none.",
        },
    }
    missing_requirements: list[str] = []
    remediation: list[str] = []
    if not checks["python"]["ready"]:
        missing_requirements.append("python>=3.11")
        remediation.append("Install Python 3.11 or later, then rerun driftproof doctor --json.")
    if not checks["dbt"]["ready"]:
        missing_requirements.append("dbt")
        remediation.append(
            "Install the pinned dbt dependencies (dbt-core 1.11.14 and dbt-duckdb 1.11.0), "
            "or run `uv sync --locked --extra dbt` in this repository."
        )
    if not checks["bubblewrap"]["ready"]:
        missing_requirements.append("working_bubblewrap_namespace")
        remediation.append(
            "Install bubblewrap and ensure an unprivileged `bwrap --unshare-all` namespace works."
        )
    ready = not missing_requirements
    payload = {
        "schema_version": 1,
        "protocol": "driftproof.doctor.v1",
        "ready_for_review": ready,
        "missing_requirements": missing_requirements,
        "recommended_action": "run_onboard" if ready else "repair_environment",
        "next_argv": ["driftproof", "onboard", ".", "--json"] if ready else None,
        "remediation": remediation,
        "checks": checks,
    }
    typer.echo(pretty_json(payload)) if json_output else console.print(pretty_json(payload))
    if not ready:
        raise typer.Exit(code=30)


@app.command("capabilities")
def capabilities() -> None:
    """Emit the installed DriftProof machine protocol and safety boundary."""

    typer.echo(
        pretty_json(
            {
                "schema_version": 1,
                "protocol": "driftproof.capabilities.v1",
                "version": __version__,
                "commands": {
                    "human_review": "driftproof review",
                    "machine_review": "driftproof agent",
                    "onboard": "driftproof onboard",
                    "preflight": "driftproof preflight",
                    "context_template": "driftproof context-template",
                    "verify_bundle": "driftproof verify-report",
                    "inspect_bundle": "driftproof inspect",
                    "readiness": "driftproof doctor",
                    "schema": "driftproof schema",
                },
                "usage": {
                    "machine_review": "driftproof agent <project|request.json|->",
                    "onboard": "driftproof onboard <project> [--apply] --json",
                    "preflight": "driftproof preflight <project> --json",
                    "context_template": "driftproof context-template [--output BUSINESS_CONTEXT.md]",
                    "verify_bundle": "driftproof verify-report <bundle>",
                    "inspect_bundle": "driftproof inspect <bundle> --json",
                    "readiness": "driftproof doctor --json",
                    "schema": "driftproof schema <name>",
                },
                "request_protocol": "driftproof.request.v1",
                "request_paths_relative_to": "request_file_parent_or_cwd_for_stdin",
                "request_size_limit_bytes": _MAX_REQUEST_BYTES,
                "response_protocol": "driftproof.agent.v1",
                "stdout_objects_per_machine_invocation": 1,
                "default_paths": "collision_resistant_project_path_hash_with_optional_run_id",
                "isolation": ["auto", "bubblewrap", "disposable_copy"],
                "external_provider_consent_required": True,
                "canonical_schemas": sorted(_schema_catalog()),
                "schema_aliases": dict(sorted(_SCHEMA_ALIASES.items())),
                "exit_codes": {
                    "0": "approve_for_human_checkpoint",
                    "10": "reject",
                    "20": "human_review",
                    "30": "invalid_review_or_tool_error",
                },
                "safety_boundary": {
                    "human_approval_required": True,
                    "consequential_action_taken": False,
                },
            }
        )
    )


@app.command("schema")
def schema(name: Annotated[str, typer.Argument()]) -> None:
    """Print one runtime-derived DriftProof JSON Schema."""

    try:
        canonical = _SCHEMA_ALIASES[name.strip().lower()]
        payload = _schema_catalog()[canonical]
    except Exception:
        _fail(
            ValueError(f"unsupported schema {name!r}; choose one of {sorted(_SCHEMA_ALIASES)}"),
            json_output=True,
            context="DriftProof schema discovery",
        )
    typer.echo(pretty_json(payload))


@app.command("schemas")
def schemas() -> None:
    """Print the complete DriftProof protocol catalog."""

    typer.echo(
        pretty_json(
            {
                "schema_version": 1,
                "protocol": "driftproof.schemas.v1",
                **_schema_catalog(),
                "schema_aliases": dict(sorted(_SCHEMA_ALIASES.items())),
                "exit_codes": {
                    "0": "approve_for_human_checkpoint",
                    "10": "reject",
                    "20": "human_review",
                    "30": "invalid_review_or_tool_error",
                },
                "safety_boundary": {
                    "human_approval_required": True,
                    "consequential_action_taken": False,
                },
            }
        )
    )


if __name__ == "__main__":
    app()
