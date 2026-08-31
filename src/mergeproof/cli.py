from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, cast

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .benchmark import load_cases, run_benchmark
from .intake import IntakeError, load_case, prepare_case_from_git, save_case
from .models import (
    AgentProtocolResponse,
    AgentTrace,
    AuditResult,
    CaseInput,
    CommandSpec,
    Decision,
    EvidenceRecord,
    Finding,
    ReviewErrorResponse,
    ReviewNavigationResponse,
    ReviewPreparationResponse,
)
from .pipeline import run_advanced, run_baseline, run_verified
from .providers import LLMProvider, ProviderError, build_provider
from .reporting import (
    BundleVerificationError,
    decision_exit_code,
    prepare_review_output,
    verify_review_bundle,
    write_review_bundle,
)
from .sandbox import SandboxUnavailable, _bubblewrap_available
from .utils import pretty_json, redact_secrets, write_json

Mode = Literal["verified", "advanced", "baseline"]
SchemaName = Literal[
    "request",
    "result",
    "command",
    "evidence_record",
    "finding",
    "agent_trace",
    "preparation_response",
    "navigation_response",
    "error_response",
    "agent_response",
]
_MAX_TASK_FILE_BYTES = 100_000
_EXTERNAL_PROVIDERS = {"gemini", "groq", "openai-compatible", "openrouter"}
_SCHEMA_ALIASES: dict[str, SchemaName] = {
    "request": "request",
    "review-request": "request",
    "result": "result",
    "review-result": "result",
    "command": "command",
    "command-spec": "command",
    "evidence-record": "evidence_record",
    "evidence_record": "evidence_record",
    "finding": "finding",
    "agent-trace": "agent_trace",
    "agent_trace": "agent_trace",
    "preparation-response": "preparation_response",
    "preparation_response": "preparation_response",
    "navigation-response": "navigation_response",
    "navigation_response": "navigation_response",
    "error-response": "error_response",
    "error_response": "error_response",
    "agent-response": "agent_response",
    "agent_response": "agent_response",
}
_RECOMMENDED_ACTION: dict[Decision, str] = {
    Decision.APPROVE: "human_approval",
    Decision.REJECT: "repair_required",
    Decision.HUMAN_REVIEW: "evidence_or_human_escalation",
}


class ExternalProviderConsentRequired(ValueError):
    """Raised when source evidence would leave the local machine without consent."""


app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Evidence-grounded release gate for agent-authored code changes. "
        "The default verified mode is deterministic, credential-free, and never merges or deploys."
    ),
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
    """Review changes without transferring merge or deployment authority to an agent."""


def _normalize_mode(value: str) -> Mode:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in {"verified", "advanced", "baseline"}:
        raise IntakeError(
            f"unsupported review mode {value!r}; choose verified, advanced, or baseline"
        )
    return cast(Mode, normalized)


def _normalize_schema_name(value: str) -> SchemaName:
    normalized = value.strip().lower()
    try:
        return _SCHEMA_ALIASES[normalized]
    except KeyError as exc:
        choices = ", ".join(sorted(_SCHEMA_ALIASES))
        raise IntakeError(f"unsupported schema {value!r}; choose one of: {choices}") from exc


def _provider_for_mode(
    *,
    mode: Mode,
    provider: str,
    model: str,
    record_dir: Path | None,
    replay_dir: Path | None,
    allow_external_provider: bool,
) -> LLMProvider | None:
    if mode == "verified":
        return None
    if provider in _EXTERNAL_PROVIDERS and not allow_external_provider:
        raise ExternalProviderConsentRequired(
            f"mode {mode!r} with provider {provider!r} sends a redacted, bounded evidence "
            "projection to an external service; pass --allow-external-provider only after "
            "the repository owner authorizes that disclosure"
        )
    return build_provider(
        provider=provider,
        model=model,
        record_dir=record_dir,
        replay_dir=replay_dir,
    )


def _run_case(case: CaseInput, mode: Mode, provider: LLMProvider | None) -> AuditResult:
    if mode == "verified":
        return run_verified(case)
    if provider is None:
        raise ValueError(f"mode {mode!r} requires a model provider")
    if mode == "baseline":
        return run_baseline(case, provider)
    return run_advanced(case, provider)


def _resolve_task(task: str | None, task_file: Path | None) -> str:
    if task is not None and task_file is not None:
        raise IntakeError("choose exactly one of --task or --task-file")
    if task_file is not None:
        if not task_file.is_file() or task_file.is_symlink():
            raise IntakeError(f"task file must be a regular UTF-8 file: {task_file}")
        payload = task_file.read_bytes()
        if len(payload) > _MAX_TASK_FILE_BYTES:
            raise IntakeError(f"task file exceeds {_MAX_TASK_FILE_BYTES} bytes: {task_file}")
        try:
            value = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntakeError(f"task file must be UTF-8: {task_file}") from exc
        if not value.strip():
            raise IntakeError("--task-file may not be blank")
        return value
    if task is None or not task.strip():
        raise IntakeError("--task or --task-file is required")
    return task


def _error_descriptor(exc: Exception) -> tuple[str, str, bool, bool]:
    if isinstance(exc, IntakeError):
        return (
            "input_invalid",
            "Correct the request, task, Git root, path, or command input and rerun.",
            False,
            True,
        )
    if isinstance(exc, ExternalProviderConsentRequired):
        return (
            "external_provider_consent_required",
            "Obtain repository-owner authorization, then rerun with --allow-external-provider, "
            "or use verified/replay mode so no evidence leaves the machine.",
            False,
            True,
        )
    if isinstance(exc, ProviderError):
        return (
            "provider_unavailable",
            "Check provider readiness, then retry the same immutable request with bounded retries.",
            True,
            True,
        )
    if isinstance(exc, SandboxUnavailable):
        return (
            "sandbox_unavailable",
            "Run `mergeproof doctor --json` and restore a working bubblewrap namespace.",
            False,
            True,
        )
    if isinstance(exc, BundleVerificationError):
        return (
            "bundle_invalid",
            "Do not trust the bundle. Use a dedicated output path or explicitly replace a prior bundle.",
            False,
            True,
        )
    if isinstance(exc, OSError):
        return (
            "filesystem_error",
            "Check that the requested paths are writable regular files or directories.",
            False,
            True,
        )
    if isinstance(exc, ValueError):
        return (
            "validation_failed",
            "Correct the invalid value and rerun without changing the review contract.",
            False,
            True,
        )
    return (
        "internal_error",
        "Inspect local diagnostics and treat this run as invalid before retrying.",
        False,
        False,
    )


def _validate_response_file(path: Path | None) -> None:
    if path is None:
        return
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise IntakeError(f"response file must be a regular file path: {path}")


def _prepare_request_output(path: Path, *, replace: bool) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise IntakeError(f"request output must be a regular file path: {path}")
    if path.exists() and not replace:
        raise IntakeError(
            f"request output already exists: {path}; choose a new path or pass --replace-output"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


def _require_distinct_paths(paths: list[tuple[str, Path | None]]) -> None:
    resolved: dict[Path, str] = {}
    for label, path in paths:
        if path is None:
            continue
        value = path.resolve(strict=False)
        if value in resolved:
            raise IntakeError(f"{label} must differ from {resolved[value]}: {path}")
        resolved[value] = label


def _validate_output_relationships(
    output: Path,
    protected: list[tuple[str, Path | None]],
) -> None:
    resolved_output = output.resolve(strict=False)
    for label, path in protected:
        if path is None:
            continue
        resolved = path.resolve(strict=False)
        if resolved == resolved_output or resolved.is_relative_to(resolved_output):
            raise IntakeError(f"{label} must not be inside the review bundle output: {path}")


def _write_response_file(path: Path, payload: dict[str, Any]) -> None:
    _validate_response_file(path)
    write_json(path, payload)


def _emit_machine_payload(
    payload: dict[str, Any],
    *,
    json_output: bool,
    response_file: Path | None,
) -> None:
    if response_file is not None:
        _write_response_file(response_file, payload)
    if json_output:
        typer.echo(pretty_json(payload))


def _error_payload(
    exc: Exception,
    *,
    context: str,
    response_file: Path | None,
) -> dict[str, Any]:
    error_code, hint, retryable, known = _error_descriptor(exc)
    return ReviewErrorResponse(
        schema_version=1,
        status="invalid_review",
        decision="human_review",
        exit_code=30,
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
        response_file=str(response_file) if response_file is not None else None,
        human_approval_required=True,
        consequential_action_taken=False,
    ).model_dump(mode="json")


def _fail(
    exc: Exception,
    *,
    json_output: bool,
    context: str,
    response_file: Path | None = None,
) -> NoReturn:
    payload = _error_payload(exc, context=context, response_file=response_file)
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
        console.print("No review bundle or consequential action should be trusted from this run.")
    if response_error is not None:
        console.print(
            f"[bold red]Response file could not be written:[/bold red] "
            f"{redact_secrets(str(response_error))[:1_000]}"
        )
    raise typer.Exit(code=30) from exc


def _schema_catalog() -> dict[str, dict[str, Any]]:
    return {
        "request": CaseInput.model_json_schema(),
        "result": AuditResult.model_json_schema(),
        "command": CommandSpec.model_json_schema(),
        "evidence_record": EvidenceRecord.model_json_schema(),
        "finding": Finding.model_json_schema(),
        "agent_trace": AgentTrace.model_json_schema(),
        "preparation_response": ReviewPreparationResponse.model_json_schema(),
        "navigation_response": ReviewNavigationResponse.model_json_schema(),
        "error_response": ReviewErrorResponse.model_json_schema(),
        "agent_response": AgentProtocolResponse.model_json_schema(),
    }


def _emit_bundle_result(
    *,
    case: CaseInput,
    result: AuditResult,
    output: Path,
    json_output: bool,
    response_file: Path | None,
) -> None:
    manifest = write_review_bundle(case, result, output)
    verification = verify_review_bundle(output)
    if manifest["decision"] != result.decision.value:
        raise BundleVerificationError("bundle manifest decision drifted from the result")
    payload = ReviewNavigationResponse(
        schema_version=1,
        case_id=case.id,
        decision=result.decision,
        confidence=result.confidence,
        exit_code=decision_exit_code(result.decision),
        recommended_action=cast(Any, _RECOMMENDED_ACTION[result.decision]),
        bundle=str(output),
        request=str(output / "request.json"),
        manifest=str(output / "manifest.json"),
        machine_result=str(output / "result.json"),
        evidence_ledger=str(output / "evidence.jsonl"),
        agent_traces=str(output / "agent-traces.json"),
        human_report=str(output / "report.html"),
        human_report_markdown=str(output / "report.md"),
        verified_findings=sum(item.status.value == "verified" for item in result.findings),
        hypotheses=sum(item.status.value == "hypothesis" for item in result.findings),
        bundle_verified=True,
        bundle_manifest_sha256=str(verification["bundle_manifest_sha256"]),
        response_file=str(response_file) if response_file is not None else None,
        human_approval_required=True,
        consequential_action_taken=False,
    ).model_dump(mode="json")
    try:
        _emit_machine_payload(
            payload,
            json_output=json_output,
            response_file=response_file,
        )
    except Exception as response_exc:
        try:
            prepare_review_output(output, replace=True)
        except Exception as cleanup_exc:
            raise BundleVerificationError(
                "machine response publication failed and the recognized review bundle "
                f"could not be removed: {cleanup_exc}"
            ) from response_exc
        raise
    if not json_output:
        console.print(f"[bold]Decision:[/bold] {result.decision.value}")
        console.print(result.summary)
        console.print(f"Human report: {output / 'report.html'}")
        console.print(f"Machine result: {output / 'result.json'}")
        console.print(f"Bundle manifest: {output / 'manifest.json'}")
        if response_file is not None:
            console.print(f"Machine navigation: {response_file}")
        console.print("No merge, deployment, push, or publication was performed.")


@app.command("review")
def review_request(
    request: Annotated[
        str,
        typer.Argument(help="Path to a versioned request JSON, or '-' to read it from stdin."),
    ],
    mode: Annotated[str, typer.Option(help="verified, advanced, or baseline.")] = "verified",
    output: Annotated[Path, typer.Option(help="Directory for the review bundle.")] = Path(
        "mergeproof-review"
    ),
    provider: Annotated[
        str,
        typer.Option(
            help="Provider for advanced/baseline mode: groq, openrouter, gemini, or replay."
        ),
    ] = "groq",
    model: Annotated[str, typer.Option(help="Provider model identifier.")] = "openai/gpt-oss-20b",
    record_dir: Annotated[
        Path | None, typer.Option(help="Record live model responses here.")
    ] = None,
    replay_dir: Annotated[
        Path | None, typer.Option(help="Read immutable model fixtures here.")
    ] = None,
    allow_external_provider: Annotated[
        bool,
        typer.Option(
            "--allow-external-provider",
            help="Acknowledge that advanced/baseline mode sends a redacted, bounded evidence projection to the selected external provider.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write one machine-readable JSON object to stdout."),
    ] = False,
    response_file: Annotated[
        Path | None,
        typer.Option(help="Atomically write the same machine navigation or error object here."),
    ] = None,
    replace_output: Annotated[
        bool,
        typer.Option(
            "--replace-output",
            help="Remove only a recognized prior/partial MergeProof bundle before this run.",
        ),
    ] = False,
) -> None:
    """Review one explicit request and emit a self-verifying human/agent bundle."""
    try:
        request_path = None if request == "-" else Path(request)
        _validate_response_file(response_file)
        _require_distinct_paths(
            [
                ("request file", request_path),
                ("response file", response_file),
                ("record directory", record_dir),
                ("replay directory", replay_dir),
            ]
        )
        _validate_output_relationships(
            output,
            [
                ("request file", request_path),
                ("response file", response_file),
                ("record directory", record_dir),
                ("replay directory", replay_dir),
            ],
        )
        prepare_review_output(output, replace=replace_output)
        case = load_case(request)
        resolved_mode = _normalize_mode(mode)
        llm = _provider_for_mode(
            mode=resolved_mode,
            provider=provider,
            model=model,
            record_dir=record_dir,
            replay_dir=replay_dir,
            allow_external_provider=allow_external_provider,
        )
        result = _run_case(case, resolved_mode, llm)
        _emit_bundle_result(
            case=case,
            result=result,
            output=output,
            json_output=json_output,
            response_file=response_file,
        )
    except Exception as exc:
        _fail(
            exc,
            json_output=json_output,
            context="Review",
            response_file=response_file,
        )
    raise typer.Exit(code=decision_exit_code(result.decision))


@app.command("agent")
def agent_request(
    request: Annotated[
        str,
        typer.Argument(help="Versioned request JSON; defaults to '-' for stdin."),
    ] = "-",
    mode: Annotated[str, typer.Option(help="verified, advanced, or baseline.")] = "verified",
    output: Annotated[Path, typer.Option(help="Directory for the verified review bundle.")] = Path(
        "mergeproof-review"
    ),
    provider: Annotated[
        str,
        typer.Option(help="groq, openrouter, gemini, openai-compatible, or replay."),
    ] = "groq",
    model: Annotated[str, typer.Option(help="Provider model identifier.")] = "openai/gpt-oss-20b",
    record_dir: Annotated[
        Path | None, typer.Option(help="Record sanitized live provider responses here.")
    ] = None,
    replay_dir: Annotated[
        Path | None, typer.Option(help="Read immutable replay fixtures here.")
    ] = None,
    allow_external_provider: Annotated[
        bool,
        typer.Option(
            "--allow-external-provider",
            help="Acknowledge disclosure of a redacted, bounded evidence projection to the selected external provider.",
        ),
    ] = False,
    response_file: Annotated[
        Path | None,
        typer.Option(help="Atomically write the same one-object protocol response here."),
    ] = None,
    replace_output: Annotated[
        bool,
        typer.Option(
            "--replace-output",
            help="Remove only a recognized prior/partial MergeProof bundle before this run.",
        ),
    ] = False,
) -> None:
    """Machine-first review: exactly one protocol JSON object on stdout."""

    review_request(
        request=request,
        mode=mode,
        output=output,
        provider=provider,
        model=model,
        record_dir=record_dir,
        replay_dir=replay_dir,
        allow_external_provider=allow_external_provider,
        json_output=True,
        response_file=response_file,
        replace_output=replace_output,
    )


@app.command("prepare")
def prepare_request(
    repo: Annotated[Path, typer.Argument(file_okay=False)] = Path("."),
    task: Annotated[
        str | None, typer.Option(help="Exact requested behavior and preservation constraints.")
    ] = None,
    task_file: Annotated[
        Path | None,
        typer.Option(
            help="Read the exact requested behavior and preservation constraints from UTF-8."
        ),
    ] = None,
    output: Annotated[Path, typer.Option(help="Request JSON to create.")] = Path(
        "mergeproof-request.json"
    ),
    base: Annotated[str, typer.Option(help="Git base ref for the before snapshot.")] = "HEAD",
    title: Annotated[str | None, typer.Option(help="Human-readable review title.")] = None,
    command: Annotated[
        list[str] | None,
        typer.Option(
            "--command",
            help="Bounded verification command; repeat this option for multiple commands.",
        ),
    ] = None,
    allow: Annotated[
        list[str] | None,
        typer.Option(
            "--allow",
            help="Allowed changed-path glob; defaults to the observed changed paths.",
        ),
    ] = None,
    trajectory: Annotated[
        Path | None,
        typer.Option(dir_okay=False, help="Optional sanitized agent trajectory JSON."),
    ] = None,
    timeout_seconds: Annotated[float, typer.Option(min=0.1, max=120)] = 15.0,
    repeat: Annotated[int, typer.Option(min=1, max=10)] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    response_file: Annotated[
        Path | None,
        typer.Option(help="Atomically write the machine preparation response here."),
    ] = None,
    replace_output: Annotated[
        bool,
        typer.Option("--replace-output", help="Replace an existing request JSON explicitly."),
    ] = False,
) -> None:
    """Convert a Git working tree into the versioned request protocol."""
    try:
        _validate_response_file(response_file)
        _require_distinct_paths(
            [
                ("request output", output),
                ("task file", task_file),
                ("trajectory file", trajectory),
                ("response file", response_file),
            ]
        )
        _prepare_request_output(output, replace=replace_output)
        case = prepare_case_from_git(
            repo=repo,
            base_ref=base,
            task=_resolve_task(task, task_file),
            title=title,
            commands=command,
            allowed_changed_globs=allow,
            trajectory_path=trajectory,
            exclude_paths=[
                output,
                *([task_file] if task_file is not None else []),
                *([trajectory] if trajectory is not None else []),
                *([response_file] if response_file is not None else []),
            ],
            timeout_seconds=timeout_seconds,
            repeat=repeat,
        )
        save_case(case, output)
    except Exception as exc:
        _fail(
            exc,
            json_output=json_output,
            context="Request preparation",
            response_file=response_file,
        )
    payload = ReviewPreparationResponse(
        schema_version=1,
        request=str(output),
        case_id=case.id,
        changed_paths=[str(path) for path in case.metadata.get("changed_paths", [])],
        base_commit=str(case.metadata.get("base_commit", "")),
        verification_commands=len(case.verification_commands),
        response_file=str(response_file) if response_file is not None else None,
        human_approval_required=True,
        consequential_action_taken=False,
    ).model_dump(mode="json")
    _emit_machine_payload(
        payload,
        json_output=json_output,
        response_file=response_file,
    )
    if not json_output:
        console.print(pretty_json(payload))


@app.command("review-git")
def review_git(
    repo: Annotated[Path, typer.Argument(file_okay=False)] = Path("."),
    task: Annotated[
        str | None, typer.Option(help="Exact requested behavior and preservation constraints.")
    ] = None,
    task_file: Annotated[
        Path | None,
        typer.Option(
            help="Read the exact requested behavior and preservation constraints from UTF-8."
        ),
    ] = None,
    mode: Annotated[str, typer.Option(help="verified, advanced, or baseline.")] = "verified",
    output: Annotated[Path, typer.Option(help="Directory for the review bundle.")] = Path(
        "mergeproof-review"
    ),
    base: Annotated[str, typer.Option(help="Git base ref for the before snapshot.")] = "HEAD",
    title: Annotated[str | None, typer.Option(help="Human-readable review title.")] = None,
    command: Annotated[
        list[str] | None,
        typer.Option(
            "--command",
            help="Bounded verification command; repeat this option for multiple commands.",
        ),
    ] = None,
    allow: Annotated[
        list[str] | None,
        typer.Option(
            "--allow",
            help="Allowed changed-path glob; defaults to the observed changed paths.",
        ),
    ] = None,
    trajectory: Annotated[
        Path | None,
        typer.Option(dir_okay=False, help="Optional sanitized agent trajectory JSON."),
    ] = None,
    timeout_seconds: Annotated[float, typer.Option(min=0.1, max=120)] = 15.0,
    repeat: Annotated[int, typer.Option(min=1, max=10)] = 1,
    provider: Annotated[
        str,
        typer.Option(
            help="Provider for advanced/baseline mode: groq, openrouter, gemini, or replay."
        ),
    ] = "groq",
    model: Annotated[str, typer.Option(help="Provider model identifier.")] = "openai/gpt-oss-20b",
    record_dir: Annotated[
        Path | None, typer.Option(help="Record live model responses here.")
    ] = None,
    replay_dir: Annotated[
        Path | None, typer.Option(help="Read immutable model fixtures here.")
    ] = None,
    allow_external_provider: Annotated[
        bool,
        typer.Option(
            "--allow-external-provider",
            help="Acknowledge that advanced/baseline mode sends a redacted, bounded evidence projection to the selected external provider.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write one machine-readable JSON object to stdout."),
    ] = False,
    response_file: Annotated[
        Path | None,
        typer.Option(help="Atomically write the same machine navigation or error object here."),
    ] = None,
    replace_output: Annotated[
        bool,
        typer.Option(
            "--replace-output",
            help="Remove only a recognized prior/partial MergeProof bundle before this run.",
        ),
    ] = False,
) -> None:
    """Prepare and review a Git working tree in one command."""
    try:
        _validate_response_file(response_file)
        _require_distinct_paths(
            [
                ("task file", task_file),
                ("trajectory file", trajectory),
                ("response file", response_file),
                ("record directory", record_dir),
                ("replay directory", replay_dir),
            ]
        )
        _validate_output_relationships(
            output,
            [
                ("repository", repo),
                ("task file", task_file),
                ("trajectory file", trajectory),
                ("response file", response_file),
                ("record directory", record_dir),
                ("replay directory", replay_dir),
            ],
        )
        prepare_review_output(output, replace=replace_output)
        case = prepare_case_from_git(
            repo=repo,
            base_ref=base,
            task=_resolve_task(task, task_file),
            title=title,
            commands=command,
            allowed_changed_globs=allow,
            trajectory_path=trajectory,
            exclude_paths=[
                output,
                *([task_file] if task_file is not None else []),
                *([record_dir] if record_dir is not None else []),
                *([replay_dir] if replay_dir is not None else []),
                *([trajectory] if trajectory is not None else []),
                *([response_file] if response_file is not None else []),
            ],
            timeout_seconds=timeout_seconds,
            repeat=repeat,
        )
        resolved_mode = _normalize_mode(mode)
        llm = _provider_for_mode(
            mode=resolved_mode,
            provider=provider,
            model=model,
            record_dir=record_dir,
            replay_dir=replay_dir,
            allow_external_provider=allow_external_provider,
        )
        result = _run_case(case, resolved_mode, llm)
        _emit_bundle_result(
            case=case,
            result=result,
            output=output,
            json_output=json_output,
            response_file=response_file,
        )
    except Exception as exc:
        _fail(
            exc,
            json_output=json_output,
            context="Review",
            response_file=response_file,
        )
    raise typer.Exit(code=decision_exit_code(result.decision))


@app.command("verify-bundle")
def verify_bundle(
    bundle: Annotated[Path, typer.Argument(file_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recompute the integrity and evidence-reference checks for a review bundle."""
    try:
        result = verify_review_bundle(bundle)
    except Exception as exc:
        _fail(exc, json_output=json_output, context="Bundle verification")
    typer.echo(pretty_json(result)) if json_output else console.print(pretty_json(result))


@app.command("verify-report")
def verify_report(
    bundle: Annotated[
        Path,
        typer.Argument(help="Review bundle directory; defaults to mergeproof-review."),
    ] = Path("mergeproof-review"),
) -> None:
    """Machine-first alias that verifies a review bundle and emits one JSON object."""

    verify_bundle(bundle=bundle, json_output=True)


@app.command("inspect")
def inspect_bundle(
    bundle: Annotated[
        Path,
        typer.Argument(help="Review bundle directory; defaults to mergeproof-review."),
    ] = Path("mergeproof-review"),
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify and summarize a review bundle for a human or agent."""

    try:
        verification = verify_review_bundle(bundle)
        result = AuditResult.model_validate_json(
            (bundle / "result.json").read_text(encoding="utf-8")
        )
        payload = {
            "schema_version": 1,
            "protocol": "mergeproof.inspect.v1",
            "verified": True,
            "case_id": result.case_id,
            "decision": result.decision.value,
            "confidence": result.confidence,
            "recommended_action": _RECOMMENDED_ACTION[result.decision],
            "summary": result.summary,
            "verified_findings": sum(item.status.value == "verified" for item in result.findings),
            "hypotheses": sum(item.status.value == "hypothesis" for item in result.findings),
            "human_report": str(bundle / "report.html"),
            "machine_result": str(bundle / "result.json"),
            "evidence_ledger": str(bundle / "evidence.jsonl"),
            "bundle_manifest_sha256": verification["bundle_manifest_sha256"],
            "human_approval_required": True,
            "consequential_action_taken": False,
        }
    except Exception as exc:
        _fail(exc, json_output=json_output, context="Bundle inspection")
    if json_output:
        typer.echo(pretty_json(payload))
    else:
        console.print(f"[bold]Decision:[/bold] {payload['decision']}")
        console.print(str(payload["summary"]))
        console.print(f"Next state: {payload['recommended_action']}")
        console.print(f"Human report: {payload['human_report']}")
        console.print(f"Evidence ledger: {payload['evidence_ledger']}")
        console.print("The bundle was verified; a qualified human still owns the final action.")


@app.command("capabilities")
def capabilities() -> None:
    """Emit the installed machine protocol, modes, providers, schemas, and exits."""

    typer.echo(
        pretty_json(
            {
                "schema_version": 1,
                "protocol": "mergeproof.capabilities.v1",
                "version": __version__,
                "commands": {
                    "human_git_review": "mergeproof review-git",
                    "machine_review": "mergeproof agent",
                    "prepare_request": "mergeproof prepare",
                    "verify_bundle": "mergeproof verify-report",
                    "inspect_bundle": "mergeproof inspect",
                    "readiness": "mergeproof doctor --json",
                    "schema": "mergeproof schema <name>",
                },
                "modes": ["verified", "advanced", "baseline"],
                "providers": [
                    "gemini",
                    "groq",
                    "openai-compatible",
                    "openrouter",
                    "replay",
                ],
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
def schema(
    name: Annotated[
        str,
        typer.Argument(help="Protocol schema name; hyphen and underscore aliases are accepted."),
    ],
) -> None:
    """Print one runtime-derived JSON Schema for an AI-agent integration."""

    try:
        canonical = _normalize_schema_name(name)
        payload = _schema_catalog()[canonical]
    except Exception as exc:
        _fail(exc, json_output=True, context="Schema discovery")
    typer.echo(pretty_json(payload))


@app.command("schemas")
def schemas() -> None:
    """Print the complete versioned protocol catalog and stable process exit codes."""
    typer.echo(
        pretty_json(
            {
                "schema_version": 4,
                "protocol": "mergeproof.schemas.v1",
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


@app.command("doctor")
def doctor(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Report readiness without exposing credential values."""
    bubblewrap_path = shutil.which("bwrap")
    bubblewrap_ready = _bubblewrap_available()
    checks: dict[str, Any] = {
        "python": {
            "ready": sys.version_info >= (3, 11),
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "bubblewrap": {
            "installed": bubblewrap_path is not None,
            "ready": bubblewrap_ready,
            "path": bubblewrap_path,
            "check": "namespace smoke test",
        },
        "git": {"ready": shutil.which("git") is not None, "path": shutil.which("git")},
        "providers": {
            "groq_configured": bool(os.getenv("GROQ_API_KEY")),
            "openrouter_configured": bool(os.getenv("OPENROUTER_API_KEY")),
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
            "note": "Provider credentials are optional; verified mode requires none.",
        },
    }
    ready = bool(checks["python"]["ready"] and checks["bubblewrap"]["ready"])
    git_ready = bool(ready and checks["git"]["ready"])
    payload = {
        "ready_for_verified_mode": ready,
        "ready_for_git_review": git_ready,
        "checks": checks,
    }
    typer.echo(pretty_json(payload)) if json_output else console.print(pretty_json(payload))
    if not ready:
        raise typer.Exit(code=30)


@app.command("list-cases")
def list_cases(
    cases: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "benchmark/cases.json"
    ),
) -> None:
    """List the frozen generic evaluation cases."""
    table = Table("ID", "Title", "Commands")
    for case in load_cases(cases):
        table.add_row(case.id, case.title, str(len(case.verification_commands)))
    console.print(table)


@app.command("evaluate")
def evaluate(
    mode: Annotated[str, typer.Option(help="Workflow stage.")] = "verified",
    provider: Annotated[str, typer.Option(help="groq, openrouter, gemini, or replay")] = "groq",
    model: Annotated[str, typer.Option(help="Provider model ID.")] = "openai/gpt-oss-20b",
    cases: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "benchmark/cases.json"
    ),
    gold: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("benchmark/gold.json"),
    output: Annotated[Path, typer.Option()] = Path("results/verified-current"),
    record_dir: Annotated[Path | None, typer.Option()] = None,
    replay_dir: Annotated[Path | None, typer.Option()] = None,
    allow_external_provider: Annotated[
        bool,
        typer.Option(
            "--allow-external-provider",
            help="Acknowledge disclosure of a redacted, bounded evidence projection to the selected external provider.",
        ),
    ] = False,
    case: Annotated[str | None, typer.Option(help="Run one case ID.")] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Run a frozen benchmark mode and write raw, metric, and manifest artifacts."""
    try:
        resolved_mode = _normalize_mode(mode)
        llm = _provider_for_mode(
            mode=resolved_mode,
            provider=provider,
            model=model,
            record_dir=record_dir,
            replay_dir=replay_dir,
            allow_external_provider=allow_external_provider,
        )
        _, metrics = run_benchmark(
            mode=resolved_mode,
            provider=llm,
            cases_path=cases,
            gold_path=gold,
            output_dir=output,
            only_case=case,
            limit=limit,
        )
    except Exception as exc:
        _fail(exc, json_output=True, context="Evaluation")
    typer.echo(pretty_json(metrics))


if __name__ == "__main__":
    app()
