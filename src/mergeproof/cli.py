from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .benchmark import load_cases, run_benchmark
from .providers import ProviderError, build_provider
from .utils import pretty_json

app = typer.Typer(
    no_args_is_help=True,
    help="Evidence-grounded release gate for agent-authored code changes.",
)
console = Console()


@app.command("list-cases")
def list_cases(
    cases: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "benchmark/cases.json"
    ),
) -> None:
    table = Table("ID", "Title", "Commands")
    for case in load_cases(cases):
        table.add_row(case.id, case.title, str(len(case.verification_commands)))
    console.print(table)


@app.command()
def evaluate(
    mode: Annotated[str, typer.Option(help="Workflow stage.")] = "baseline",
    provider: Annotated[str, typer.Option(help="gemini, groq, openrouter, or replay")] = "gemini",
    model: Annotated[str, typer.Option(help="Provider model ID.")] = "gemini-3.1-flash-lite",
    cases: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path(
        "benchmark/cases.json"
    ),
    gold: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("benchmark/gold.json"),
    output: Annotated[Path, typer.Option()] = Path("results/baseline-live"),
    record_dir: Annotated[Path | None, typer.Option()] = None,
    replay_dir: Annotated[Path | None, typer.Option()] = None,
    case: Annotated[str | None, typer.Option(help="Run one case ID.")] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    try:
        llm = build_provider(
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
        raise typer.Exit(code=2) from exc
    console.print(pretty_json(metrics))


if __name__ == "__main__":
    app()
