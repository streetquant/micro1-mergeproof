from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from driftproof.gate import baseline_green_gate, review_project
from driftproof.models import Verdict
from driftproof.project import snapshot_project

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOT = ROOT / ".work" / "driftproof-benchmark"
DEFAULT_OUTPUT = ROOT / "results" / "driftproof-comparison"


class BenchmarkError(RuntimeError):
    pass


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_visible_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = list(payload["cases"])
    if not cases:
        raise BenchmarkError("visible case manifest is empty")
    ids = [str(case["candidate_id"]) for case in cases]
    if len(ids) != len(set(ids)):
        raise BenchmarkError("visible case manifest has duplicate candidate IDs")
    prohibited = {"safe_to_approve", "expected_safe_to_approve", "variant", "upstream_case_id"}
    leakage = sorted(prohibited.intersection({key for case in cases for key in case}))
    if leakage:
        raise BenchmarkError(f"gold fields leaked into visible cases: {leakage}")
    return sorted(cases, key=lambda case: str(case["candidate_id"]))


def _load_gold(path: Path) -> dict[str, bool]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = list(payload["gold"])
    result = {str(record["candidate_id"]): bool(record["safe_to_approve"]) for record in records}
    if len(result) != len(records):
        raise BenchmarkError("gold manifest has duplicate candidate IDs")
    return result


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def compute_metrics(predictions: list[dict[str, Any]], gold: dict[str, bool]) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    human_review = 0
    for prediction in predictions:
        candidate_id = str(prediction["candidate_id"])
        expected_safe = gold[candidate_id]
        verdict = Verdict(str(prediction["verdict"]))
        predicted_safe = verdict == Verdict.APPROVE
        human_review += verdict == Verdict.HUMAN_REVIEW
        if predicted_safe and expected_safe:
            tp += 1
        elif predicted_safe and not expected_safe:
            fp += 1
        elif not predicted_safe and not expected_safe:
            tn += 1
        else:
            fn += 1

    safe_precision = _ratio(tp, tp + fp)
    safe_recall = _ratio(tp, tp + fn)
    unsafe_precision = _ratio(tn, tn + fn)
    unsafe_recall = _ratio(tn, tn + fp)
    safe_f1 = _f1(safe_precision, safe_recall)
    unsafe_f1 = _f1(unsafe_precision, unsafe_recall)
    runtimes = sorted(int(item["runtime_ms"]) for item in predictions)
    p95_index = max(0, round(0.95 * len(runtimes)) - 1)
    return {
        "schema_version": 1,
        "cases": len(predictions),
        "primary_metric": "safe_approval_macro_f1",
        "safe_approval_macro_f1": (safe_f1 + unsafe_f1) / 2,
        "accuracy": _ratio(tp + tn, len(predictions)),
        "safe_class": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": safe_precision,
            "recall": safe_recall,
            "f1": safe_f1,
        },
        "unsafe_class": {
            "tp": tn,
            "fp": fn,
            "fn": fp,
            "precision": unsafe_precision,
            "recall": unsafe_recall,
            "f1": unsafe_f1,
        },
        "unsafe_repair_escape_rate": _ratio(fp, fp + tn),
        "human_review_rate": _ratio(human_review, len(predictions)),
        "runtime_ms": {
            "total": sum(runtimes),
            "median": (runtimes[(len(runtimes) - 1) // 2] + runtimes[len(runtimes) // 2]) / 2,
            "p95": runtimes[p95_index],
        },
    }


def _verify_candidate(case: dict[str, Any], project: Path) -> None:
    if not project.is_dir():
        raise BenchmarkError(f"candidate project does not exist: {project}")
    observed = snapshot_project(project).tree_sha256
    expected = str(case["project_tree_sha256"])
    if observed != expected:
        raise BenchmarkError(
            f"candidate tree hash mismatch for {case['candidate_id']}: expected {expected}, observed {observed}"
        )
    context = project / "BUSINESS_CONTEXT.md"
    if (
        hashlib.sha256(context.read_bytes()).hexdigest()
        != hashlib.sha256(str(case["business_context"]).encode()).hexdigest()
    ):
        raise BenchmarkError(f"business context mismatch for {case['candidate_id']}")


def run_mode(
    mode: Literal["baseline", "advanced"],
    *,
    cases: list[dict[str, Any]],
    work_root: Path,
    output: Path,
    timeout_seconds: int,
    isolation: Literal["auto", "disposable_copy", "bubblewrap"],
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / f"{mode}-raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            candidate_id = str(case["candidate_id"])
            project = work_root / candidate_id
            _verify_candidate(case, project)
            started = time.perf_counter()
            if mode == "baseline":
                verdict = baseline_green_gate(
                    project,
                    work_root=output / "work" / "baseline",
                    timeout_seconds=timeout_seconds,
                    isolation=isolation,
                )
                record: dict[str, Any] = {
                    "schema_version": 1,
                    "candidate_id": candidate_id,
                    "mode": mode,
                    "verdict": verdict.value,
                    "basis": "approve if the candidate's own dbt build is green",
                }
            else:
                report, certificate = review_project(
                    project,
                    work_root=output / "work" / "advanced",
                    output_dir=output / "candidates" / candidate_id,
                    timeout_seconds=timeout_seconds,
                    isolation=isolation,
                )
                record = {
                    "schema_version": 1,
                    "candidate_id": candidate_id,
                    "mode": mode,
                    "verdict": report.verdict.value,
                    "failed_check_ids": report.failed_check_ids,
                    "inconclusive_check_ids": report.inconclusive_check_ids,
                    "compiled_rule_count": len(report.contract.rules),
                    "check_count": len(report.checks),
                    "certificate_sha256": certificate.self_sha256,
                    "build_isolation": report.build.isolation,
                }
            record["runtime_ms"] = round((time.perf_counter() - started) * 1000)
            predictions.append(record)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
    return predictions


def run(
    *,
    work_root: Path,
    output: Path,
    timeout_seconds: int,
    isolation: Literal["auto", "disposable_copy", "bubblewrap"],
) -> dict[str, Any]:
    cases_path = ROOT / "benchmark_dbt" / "cases.json"
    gold_path = ROOT / "benchmark_dbt" / "gold.json"
    manifest_path = ROOT / "benchmark_dbt" / "manifest.json"
    cases = _load_visible_cases(cases_path)

    baseline = run_mode(
        "baseline",
        cases=cases,
        work_root=work_root,
        output=output,
        timeout_seconds=timeout_seconds,
        isolation=isolation,
    )
    advanced = run_mode(
        "advanced",
        cases=cases,
        work_root=work_root,
        output=output,
        timeout_seconds=timeout_seconds,
        isolation=isolation,
    )

    # Gold is opened only after all verdicts have been finalized and written.
    gold = _load_gold(gold_path)
    visible_ids = {str(case["candidate_id"]) for case in cases}
    if set(gold) != visible_ids:
        raise BenchmarkError("visible/gold candidate identities differ")
    baseline_metrics = compute_metrics(baseline, gold)
    advanced_metrics = compute_metrics(advanced, gold)
    comparison = {
        "schema_version": 1,
        "benchmark": "DriftProof green-but-wrong dbt approval benchmark",
        "fairness": {
            "same_candidates": True,
            "same_context": True,
            "same_dbt_command": True,
            "baseline_resources": "candidate files plus candidate-owned dbt build",
            "advanced_resources": (
                "same inputs and dbt build plus deterministic context compilation, adversarial static "
                "checks, immutable worktree validation, and a hash-bound certificate"
            ),
            "gold_opened_after_predictions": True,
        },
        "baseline": baseline_metrics,
        "advanced": advanced_metrics,
        "change": {
            "safe_approval_macro_f1": (
                advanced_metrics["safe_approval_macro_f1"]
                - baseline_metrics["safe_approval_macro_f1"]
            ),
            "accuracy": advanced_metrics["accuracy"] - baseline_metrics["accuracy"],
            "unsafe_repair_escape_rate": (
                advanced_metrics["unsafe_repair_escape_rate"]
                - baseline_metrics["unsafe_repair_escape_rate"]
            ),
        },
        "provenance": {
            "cases_sha256": _sha256_file(cases_path),
            "gold_sha256": _sha256_file(gold_path),
            "benchmark_manifest_sha256": _sha256_file(manifest_path),
            "runner_sha256": _sha256_file(Path(__file__)),
        },
    }
    _write_json(output / "baseline-metrics.json", baseline_metrics)
    _write_json(output / "advanced-metrics.json", advanced_metrics)
    _write_json(output / "comparison.json", comparison)
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen DriftProof baseline comparison.")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--isolation",
        choices=("auto", "disposable_copy", "bubblewrap"),
        default="disposable_copy",
    )
    args = parser.parse_args()
    comparison = run(
        work_root=args.work_root.resolve(),
        output=args.output.resolve(),
        timeout_seconds=args.timeout_seconds,
        isolation=args.isolation,
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
