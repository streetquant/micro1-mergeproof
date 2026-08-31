from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal, cast

from mergeproof.sandbox import _bubblewrap_available
from mergeproof.utils import pretty_json, write_json

from .gate import GateExecutionError, review_project
from .models import DriftProofDemoCase, DriftProofDemoResponse, Verdict
from .reporting import verdict_exit_code, verify_gate_bundle
from .runner import find_dbt_executable, run_dbt_build

Isolation = Literal["auto", "disposable_copy", "bubblewrap"]
_CONTEXT = """# Finance release contract

The public contract must expose `sales`, `refunds`, and `net_revenue`.

`net_revenue = sales - refunds`.
"""
_TRAJECTORY = {
    "schema_version": 1,
    "producer": "synthetic installed-demo repair agent",
    "claim": "The candidate passes dbt build and is ready for independent semantic review.",
    "tool_calls": [{"tool": "dbt build", "result": "exit code 0"}],
    "human_checkpoint": (
        "No merge or deployment occurs before DriftProof and a qualified human approve."
    ),
}


class DemoExecutionError(GateExecutionError):
    """Raised when the transparent installed demonstration cannot complete safely."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_files(kind: Literal["safe", "unsafe"]) -> dict[str, str]:
    operation = "-" if kind == "safe" else "+"
    candidate = "SAFE" if kind == "safe" else "UNSAFE"
    project_name = f"driftproof_demo_{kind}"
    return {
        ".driftproof-candidate": f"DP-INSTALLED-DEMO-{candidate}\n",
        "BUSINESS_CONTEXT.md": _CONTEXT,
        "agent-trajectory.json": pretty_json(_TRAJECTORY) + "\n",
        "dbt_project.yml": (
            f"name: {project_name}\n"
            "version: 1.0.0\n"
            f"profile: {project_name}\n"
            "model-paths: [models]\n"
            "target-path: target\n"
            "clean-targets: [target, logs]\n"
        ),
        "models/revenue.sql": (
            "select\n"
            "    cast(100 as decimal(12, 2)) as sales,\n"
            "    cast(20 as decimal(12, 2)) as refunds,\n"
            f"    cast(100 {operation} 20 as decimal(12, 2)) as net_revenue\n"
        ),
        "profiles.yml": (
            f"{project_name}:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            "      path: installed-demo.duckdb\n"
            "      threads: 1\n"
        ),
    }


def _materialize_project(root: Path, kind: Literal["safe", "unsafe"]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for relative, content in sorted(_fixture_files(kind).items()):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")


def _select_output(output: Path | None) -> Path:
    if output is None:
        parent = Path(tempfile.gettempdir()) / "driftproof" / "demos"
        parent.mkdir(parents=True, exist_ok=True)
        return parent / f"demo-{uuid.uuid4().hex[:12]}"

    lexical = output.expanduser()
    if lexical.is_symlink() or lexical.exists():
        raise DemoExecutionError(
            f"demo output already exists or is unsafe: {lexical}; choose an absent directory"
        )
    for parent in lexical.parents:
        if parent.is_symlink():
            raise DemoExecutionError(f"demo output parent may not be a symlink: {parent}")
    resolved = lexical.resolve(strict=False)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists() or resolved.is_symlink():
        raise DemoExecutionError(
            f"demo output already exists or is unsafe: {resolved}; choose an absent directory"
        )
    return resolved


def _require_runtime(isolation: Isolation, allow_unconfined: bool) -> None:
    if find_dbt_executable() is None:
        raise DemoExecutionError(
            "the installed demo requires dbt; install `driftproof[dbt]` or run "
            "`uv sync --locked --extra dbt` in the source repository"
        )
    if isolation in {"auto", "bubblewrap"} and not _bubblewrap_available():
        raise DemoExecutionError(
            "the installed demo requires a working bubblewrap namespace for its default safe "
            "execution path"
        )
    if isolation == "disposable_copy" and not allow_unconfined:
        raise DemoExecutionError(
            "disposable_copy is not a security sandbox; pass --allow-unconfined only for this "
            "transparent trusted demo"
        )


def run_demo(
    *,
    output: Path | None = None,
    timeout_seconds: int = 120,
    isolation: Isolation = "auto",
    allow_unconfined: bool = False,
) -> DriftProofDemoResponse:
    """Run the transparent paired demo and atomically publish its verified evidence tree."""

    _require_runtime(isolation, allow_unconfined)
    destination = _select_output(output)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    published = False
    try:
        cases: dict[str, DriftProofDemoCase] = {}
        for kind in ("safe", "unsafe"):
            typed_kind: Literal["safe", "unsafe"] = kind
            project = staging / "projects" / typed_kind
            _materialize_project(project, typed_kind)

            baseline = run_dbt_build(
                project,
                work_root=staging / "work" / f"baseline-{typed_kind}",
                candidate_id=f"installed-demo-baseline-{typed_kind}",
                timeout_seconds=timeout_seconds,
                isolation=isolation,
                allow_unconfined=allow_unconfined,
            )
            baseline_path = staging / "baseline" / f"{typed_kind}.json"
            write_json(baseline_path, baseline.model_dump(mode="json"))
            baseline_verdict = Verdict.APPROVE if baseline.passed else Verdict.REJECT

            bundle = staging / "reviews" / typed_kind
            report, _certificate = review_project(
                project,
                context_path=project / "BUSINESS_CONTEXT.md",
                work_root=staging / "work" / f"review-{typed_kind}",
                output_dir=bundle,
                timeout_seconds=timeout_seconds,
                isolation=isolation,
                allow_unconfined=allow_unconfined,
            )
            verification = verify_gate_bundle(bundle)
            expected = Verdict.APPROVE if typed_kind == "safe" else Verdict.REJECT
            if baseline_verdict != Verdict.APPROVE:
                raise DemoExecutionError(
                    f"build-only baseline did not approve the {typed_kind} green-build fixture"
                )
            if report.verdict != expected:
                raise DemoExecutionError(
                    f"DriftProof returned {report.verdict.value} for the {typed_kind} fixture; "
                    f"expected {expected.value}"
                )

            final_project = destination / "projects" / typed_kind
            final_baseline = destination / "baseline" / f"{typed_kind}.json"
            final_bundle = destination / "reviews" / typed_kind
            cases[typed_kind] = DriftProofDemoCase(
                kind=typed_kind,
                expected_safe_to_approve=typed_kind == "safe",
                project=str(final_project),
                baseline_result=str(final_baseline),
                baseline_result_sha256=_sha256(baseline_path),
                baseline_verdict=baseline_verdict,
                driftproof_verdict=report.verdict,
                driftproof_exit_code=cast(Literal[0, 10], verdict_exit_code(report.verdict)),
                bundle=str(final_bundle),
                human_report=str(final_bundle / "report.html"),
                verify_argv=["driftproof", "verify-report", str(final_bundle)],
                bundle_manifest_sha256=str(verification["bundle_manifest_sha256"]),
                failed_check_ids=report.failed_check_ids,
            )

        shutil.rmtree(staging / "work")
        receipt = destination / "demo-receipt.json"
        response = DriftProofDemoResponse(
            output=str(destination),
            receipt=str(receipt),
            safe=cases["safe"],
            unsafe=cases["unsafe"],
        )
        write_json(staging / "demo-receipt.json", response.model_dump(mode="json"))
        DriftProofDemoResponse.model_validate_json(
            (staging / "demo-receipt.json").read_text(encoding="utf-8")
        )
        if destination.exists() or destination.is_symlink():
            raise DemoExecutionError(f"demo output appeared during publication: {destination}")
        staging.rename(destination)
        published = True
        return response
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)


__all__ = ["DemoExecutionError", "run_demo"]
