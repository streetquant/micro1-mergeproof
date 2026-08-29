from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from .models import AuditResult, CaseInput, Decision, GoldCase
from .pipeline import run_baseline
from .providers import LLMProvider
from .utils import canonical_json, write_json


def load_cases(path: Path) -> list[CaseInput]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [CaseInput.model_validate(item) for item in raw]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate benchmark case IDs")
    return sorted(cases, key=lambda case: case.id)


def load_gold(path: Path) -> dict[str, GoldCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    gold = [GoldCase.model_validate(item) for item in raw]
    result = {item.id: item for item in gold}
    if len(result) != len(gold):
        raise ValueError("duplicate gold case IDs")
    return result


def compute_metrics(results: list[AuditResult], gold: dict[str, GoldCase]) -> dict[str, Any]:
    if not results:
        raise ValueError("cannot score an empty result set")
    tp = fp = tn = fn = 0
    category_tp = category_fp = category_fn = 0
    for result in results:
        expected = gold[result.case_id]
        actual_block = result.decision != Decision.APPROVE
        expected_block = not expected.safe_to_merge
        if actual_block and expected_block:
            tp += 1
        elif actual_block and not expected_block:
            fp += 1
        elif not actual_block and not expected_block:
            tn += 1
        else:
            fn += 1
        expected_categories = {category.value for category in expected.categories}
        predicted_categories = {
            finding.category.value
            for finding in result.findings
            if finding.status.value == "verified"
        }
        category_tp += len(expected_categories & predicted_categories)
        category_fp += len(predicted_categories - expected_categories)
        category_fn += len(expected_categories - predicted_categories)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(results)
    category_precision = (
        category_tp / (category_tp + category_fp) if category_tp + category_fp else 0.0
    )
    category_recall = (
        category_tp / (category_tp + category_fn) if category_tp + category_fn else 0.0
    )
    category_f1 = (
        2 * category_precision * category_recall / (category_precision + category_recall)
        if category_precision + category_recall
        else 0.0
    )
    durations = [result.duration_ms for result in results]
    usages = [usage for result in results for usage in result.usage]
    evidence_rates = [result.valid_evidence_rate for result in results]
    approvals = [result for result in results if result.decision == Decision.APPROVE]
    safe_approvals = sum(gold[result.case_id].safe_to_merge for result in approvals)
    return {
        "schema_version": 1,
        "cases": len(results),
        "primary_metric": "unsafe_change_decision_f1",
        "unsafe_change_decision": {
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "accuracy": accuracy,
        },
        "safe_approval_precision": safe_approvals / len(approvals) if approvals else 0.0,
        "issue_category_micro": {
            "tp": category_tp,
            "fp": category_fp,
            "fn": category_fn,
            "precision": category_precision,
            "recall": category_recall,
            "f1": category_f1,
        },
        "evidence_reference_validity": sum(evidence_rates) / len(evidence_rates),
        "runtime_ms": {
            "median": statistics.median(durations),
            "p95": sorted(durations)[max(0, round(0.95 * len(durations)) - 1)],
            "total": sum(durations),
        },
        "model_usage": {
            "calls": len(usages),
            "input_tokens": sum(item.input_tokens for item in usages),
            "output_tokens": sum(item.output_tokens for item in usages),
            "total_tokens": sum(item.total_tokens for item in usages),
            "estimated_cost_usd": sum(item.estimated_cost_usd or 0 for item in usages),
        },
    }


def run_benchmark(
    *,
    mode: str,
    provider: LLMProvider,
    cases_path: Path,
    gold_path: Path,
    output_dir: Path,
    only_case: str | None = None,
    limit: int | None = None,
) -> tuple[list[AuditResult], dict[str, Any]]:
    if mode != "baseline":
        raise ValueError(f"mode is not implemented yet: {mode}")
    cases = load_cases(cases_path)
    if only_case is not None:
        cases = [case for case in cases if case.id == only_case]
        if not cases:
            raise ValueError(f"unknown case: {only_case}")
    if limit is not None:
        cases = cases[:limit]
    gold = load_gold(gold_path)
    missing_gold = sorted({case.id for case in cases} - set(gold))
    if missing_gold:
        raise ValueError(f"missing gold labels: {missing_gold}")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[AuditResult] = []
    raw_path = output_dir / "raw-results.jsonl"
    with raw_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            result = run_baseline(case, provider)
            results.append(result)
            handle.write(canonical_json(result.model_dump(mode="json")) + "\n")
    metrics = compute_metrics(results, gold)
    metrics.update({"mode": mode, "provider": provider.name, "model": provider.model})
    write_json(output_dir / "metrics.json", metrics)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": 1,
            "mode": mode,
            "provider": provider.name,
            "model": provider.model,
            "case_ids": [case.id for case in cases],
            "raw_results": str(raw_path),
        },
    )
    return results, metrics
