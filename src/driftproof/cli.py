from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from mergeproof.providers import ProviderError, build_provider

from .agent import ContractClarifier
from .certificate import verify_certificate
from .contracts import compile_contract
from .gate import GateExecutionError, review_project
from .models import ApprovalCertificate, GateReport

app = typer.Typer(
    no_args_is_help=True,
    help="Independent, evidence-grounded release gate for agent-authored dbt repairs.",
)
console = Console()


@app.command("compile-contract")
def compile_contract_command(
    context: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    spec = compile_contract(context.read_text(encoding="utf-8", errors="replace"))
    console.print_json(json.dumps(spec.model_dump(mode="json"), sort_keys=True))


@app.command()
def review(
    project: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    context: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    output: Annotated[Path, typer.Option()] = Path("results/driftproof-review"),
    work_root: Annotated[Path, typer.Option()] = Path(".work/driftproof-runs"),
    timeout_seconds: Annotated[int, typer.Option(min=1, max=900)] = 120,
    isolation: Annotated[Literal["auto", "disposable_copy", "bubblewrap"], typer.Option()] = "auto",
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
        typer.Option(help="Optional bounded clarifier provider: groq, openrouter, or replay."),
    ] = None,
    agent_model: Annotated[str, typer.Option()] = "openai/gpt-oss-20b",
    agent_record_dir: Annotated[Path | None, typer.Option()] = None,
    agent_replay_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        clarifier = None
        if agent_provider is not None:
            provider = build_provider(
                provider=agent_provider,
                model=agent_model,
                record_dir=agent_record_dir,
                replay_dir=agent_replay_dir,
            )
            clarifier = ContractClarifier(provider)
        report, certificate = review_project(
            project,
            context_path=context,
            output_dir=output,
            work_root=work_root,
            timeout_seconds=timeout_seconds,
            isolation=isolation,
            allow_unconfined=allow_unconfined,
            clarifier=clarifier,
        )
    except (GateExecutionError, ProviderError) as exc:
        console.print(f"DriftProof could not complete: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print_json(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    if report.verdict.value != "approve":
        raise typer.Exit(code=1)
    console.print(f"Certificate: {certificate.self_sha256}")


@app.command("verify-bundle")
def verify_bundle(
    report_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    certificate_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    report = GateReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    certificate = ApprovalCertificate.model_validate_json(
        certificate_path.read_text(encoding="utf-8")
    )
    errors = verify_certificate(report, certificate)
    if errors:
        console.print_json(json.dumps({"verified": False, "errors": errors}, sort_keys=True))
        raise typer.Exit(code=1)
    console.print_json(
        json.dumps(
            {
                "verified": True,
                "candidate_id": report.candidate_id,
                "verdict": report.verdict.value,
                "certificate_sha256": certificate.self_sha256,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    app()
