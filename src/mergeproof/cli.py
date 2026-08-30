from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .benchmark import load_cases, run_benchmark
from .intake import IntakeError, load_case, prepare_case_from_git, save_case
from .models import AuditResult, CaseInput
from .pipeline import run_advanced, run_baseline, run_verified
from .providers import LLMProvider, ProviderError, build_provider
from .reporting import (
    BundleVerificationError,
    decision_exit_code,
    verify_review_bundle,
    write_review_bundle,
)
from .sandbox import SandboxUnavailable
from .utils import pretty_json

Mode = Literal["verified", "advanced", "baseline"]

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


def _provider_for_mode(
    *,
    mode: Mode,
    provider: str,
    model: str,
    record_dir: Path | None,
    replay_dir: Path | None,
) -> LLMProvider | None:
    if mode == "verified":
        return None
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


def _required_task(task: str | None) -> str:
    if task is None or not task.strip():
        raise IntakeError("--task is required and may not be blank")
    return task


def _emit_bundle_result(
    *,
    case: CaseInput,
    result: AuditResult,
    output: Path,
    json_output: bool,
) -> None:
    manifest = write_review_bundle(case, result, output)
    payload = {
        "schema_version": 1,
        "case_id": case.id,
        "decision": result.decision.value,
        "confidence": result.confidence,
        "exit_code": decision_exit_code(result.decision),
        "bundle": str(output),
        "manifest": str(output / "manifest.json"),
        "machine_result": str(output / "result.json"),
        "human_report": str(output / "report.html"),
        "verified_findings": sum(item.status.value == "verified" for item in result.findings),
        "hypotheses": sum(item.status.value == "hypothesis" for item in result.findings),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }
    if json_output:
        typer.echo(pretty_json(payload))
    else:
        console.print(f"[bold]Decision:[/bold] {result.decision.value}")
        console.print(result.summary)
        console.print(f"Human report: {output / 'report.html'}")
        console.print(f"Machine result: {output / 'result.json'}")
        console.print(f"Bundle manifest: {output / 'manifest.json'}")
        console.print("No merge, deployment, push, or publication was performed.")
    if manifest["decision"] != result.decision.value:
        raise RuntimeError("bundle manifest decision drifted from the result")


@app.command("review")
def review_request(
    request: Annotated[
        str,
        typer.Argument(help="Path to a versioned request JSON, or '-' to read it from stdin."),
    ],
    mode: Annotated[Mode, typer.Option(help="verified, advanced, or baseline.")] = "verified",
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write one machine-readable JSON object to stdout."),
    ] = False,
) -> None:
    """Review one explicit request and emit a self-verifying human/agent bundle."""
    try:
        case = load_case(request)
        llm = _provider_for_mode(
            mode=mode,
            provider=provider,
            model=model,
            record_dir=record_dir,
            replay_dir=replay_dir,
        )
        result = _run_case(case, mode, llm)
        _emit_bundle_result(
            case=case,
            result=result,
            output=output,
            json_output=json_output,
        )
    except (IntakeError, ProviderError, SandboxUnavailable, ValueError) as exc:
        if json_output:
            typer.echo(pretty_json({"error": type(exc).__name__, "detail": str(exc)}))
        else:
            console.print(f"[bold red]Review could not complete:[/bold red] {exc}")
        raise typer.Exit(code=30) from exc
    raise typer.Exit(code=decision_exit_code(result.decision))


@app.command("prepare")
def prepare_request(
    repo: Annotated[Path, typer.Argument(exists=True, file_okay=False)] = Path("."),
    task: Annotated[
        str | None, typer.Option(help="Exact requested behavior and preservation constraints.")
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
        typer.Option(exists=True, dir_okay=False, help="Optional sanitized agent trajectory JSON."),
    ] = None,
    timeout_seconds: Annotated[float, typer.Option(min=0.1, max=120)] = 15.0,
    repeat: Annotated[int, typer.Option(min=1, max=10)] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Convert a Git working tree into the versioned request protocol."""
    try:
        case = prepare_case_from_git(
            repo=repo,
            base_ref=base,
            task=_required_task(task),
            title=title,
            commands=command,
            allowed_changed_globs=allow,
            trajectory_path=trajectory,
            timeout_seconds=timeout_seconds,
            repeat=repeat,
        )
        save_case(case, output)
    except IntakeError as exc:
        if json_output:
            typer.echo(pretty_json({"error": type(exc).__name__, "detail": str(exc)}))
        else:
            console.print(f"[bold red]Request could not be prepared:[/bold red] {exc}")
        raise typer.Exit(code=30) from exc
    payload = {
        "schema_version": 1,
        "request": str(output),
        "case_id": case.id,
        "changed_paths": case.metadata.get("changed_paths", []),
        "base_commit": case.metadata.get("base_commit"),
        "verification_commands": len(case.verification_commands),
    }
    typer.echo(pretty_json(payload)) if json_output else console.print(pretty_json(payload))


@app.command("review-git")
def review_git(
    repo: Annotated[Path, typer.Argument(exists=True, file_okay=False)] = Path("."),
    task: Annotated[
        str | None, typer.Option(help="Exact requested behavior and preservation constraints.")
    ] = None,
    mode: Annotated[Mode, typer.Option()] = "verified",
    output: Annotated[Path, typer.Option(help="Directory for the review bundle.")] = Path(
        "mergeproof-review"
    ),
    base: Annotated[str, typer.Option(help="Git base ref for the before snapshot.")] = "HEAD",
    title: Annotated[str | None, typer.Option()] = None,
    command: Annotated[list[str] | None, typer.Option("--command")] = None,
    allow: Annotated[list[str] | None, typer.Option("--allow")] = None,
    trajectory: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    timeout_seconds: Annotated[float, typer.Option(min=0.1, max=120)] = 15.0,
    repeat: Annotated[int, typer.Option(min=1, max=10)] = 1,
    provider: Annotated[str, typer.Option()] = "groq",
    model: Annotated[str, typer.Option()] = "openai/gpt-oss-20b",
    record_dir: Annotated[Path | None, typer.Option()] = None,
    replay_dir: Annotated[Path | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare and review a Git working tree in one command."""
    try:
        case = prepare_case_from_git(
            repo=repo,
            base_ref=base,
            task=_required_task(task),
            title=title,
            commands=command,
            allowed_changed_globs=allow,
            trajectory_path=trajectory,
            timeout_seconds=timeout_seconds,
            repeat=repeat,
        )
        llm = _provider_for_mode(
            mode=mode,
            provider=provider,
            model=model,
            record_dir=record_dir,
            replay_dir=replay_dir,
        )
        result = _run_case(case, mode, llm)
        _emit_bundle_result(
            case=case,
            result=result,
            output=output,
            json_output=json_output,
        )
    except (IntakeError, ProviderError, SandboxUnavailable, ValueError) as exc:
        if json_output:
            typer.echo(pretty_json({"error": type(exc).__name__, "detail": str(exc)}))
        else:
            console.print(f"[bold red]Review could not complete:[/bold red] {exc}")
        raise typer.Exit(code=30) from exc
    raise typer.Exit(code=decision_exit_code(result.decision))


@app.command("verify-bundle")
def verify_bundle(
    bundle: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recompute the integrity and evidence-reference checks for a review bundle."""
    try:
        result = verify_review_bundle(bundle)
    except BundleVerificationError as exc:
        payload = {"verified": False, "error": type(exc).__name__, "detail": str(exc)}
        typer.echo(pretty_json(payload)) if json_output else console.print(pretty_json(payload))
        raise typer.Exit(code=30) from exc
    typer.echo(pretty_json(result)) if json_output else console.print(pretty_json(result))


@app.command("schemas")
def schemas() -> None:
    """Print the versioned input/output protocol and stable process exit codes."""
    typer.echo(
        pretty_json(
            {
                "schema_version": 1,
                "request": CaseInput.model_json_schema(),
                "result": AuditResult.model_json_schema(),
                "exit_codes": {
                    "0": "approve",
                    "10": "reject",
                    "20": "human_review",
                    "30": "tool_or_input_error",
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
    checks: dict[str, Any] = {
        "python": {
            "ready": sys.version_info >= (3, 11),
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "bubblewrap": {
            "ready": shutil.which("bwrap") is not None,
            "path": shutil.which("bwrap"),
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
    payload = {"ready_for_verified_mode": ready, "checks": checks}
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
    mode: Annotated[Mode, typer.Option(help="Workflow stage.")] = "verified",
    provider: Annotated[str, typer.Option(help="groq, openrouter, gemini, or replay")] = "groq",
    model: Annotated[str, typer.Option(help="Provider model ID.")] = "openai/gpt-oss-20b",
    cases: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "benchmark/cases.json"
    ),
    gold: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("benchmark/gold.json"),
    output: Annotated[Path, typer.Option()] = Path("results/verified-current"),
    record_dir: Annotated[Path | None, typer.Option()] = None,
    replay_dir: Annotated[Path | None, typer.Option()] = None,
    case: Annotated[str | None, typer.Option(help="Run one case ID.")] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Run a frozen benchmark mode and write raw, metric, and manifest artifacts."""
    try:
        llm = _provider_for_mode(
            mode=mode,
            provider=provider,
            model=model,
            record_dir=record_dir,
            replay_dir=replay_dir,
        )
        _, metrics = run_benchmark(
            mode=mode,
            provider=llm,
            cases_path=cases,
            gold_path=gold,
            output_dir=output,
            only_case=case,
            limit=limit,
        )
    except (ProviderError, ValueError) as exc:
        console.print(f"[bold red]Evaluation failed:[/bold red] {exc}")
        raise typer.Exit(code=30) from exc
    typer.echo(pretty_json(metrics))


if __name__ == "__main__":
    app()
