from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import AuditResult, CaseInput, Decision, GoldCase
from .pipeline import run_advanced, run_baseline, run_verified
from .providers import LLMProvider
from .utils import canonical_json, write_json

_SUPPORTED_MODES = {"baseline", "verified", "advanced"}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_tree_sha256(root: Path = Path(".")) -> str:
    candidates = [
        *sorted((root / "src" / "mergeproof").glob("**/*.py")),
        root / "pyproject.toml",
        root / "uv.lock",
    ]
    records: list[bytes] = []
    for path in candidates:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode()
        records.extend((relative, b"\0", hashlib.sha256(path.read_bytes()).digest(), b"\n"))
    return hashlib.sha256(b"".join(records)).hexdigest()


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
    exact_issue_sets = 0
    human_reviews = 0
    for result in results:
        expected = gold[result.case_id]
        actual_block = result.decision != Decision.APPROVE
        expected_block = not expected.safe_to_merge
        human_reviews += result.decision == Decision.HUMAN_REVIEW
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
        exact_issue_sets += predicted_categories == expected_categories
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
    decision_metrics = {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
    }
    diagnosis_metrics = {
        "tp": category_tp,
        "fp": category_fp,
        "fn": category_fn,
        "precision": category_precision,
        "recall": category_recall,
        "f1": category_f1,
        "exact_issue_set_rate": exact_issue_sets / len(results),
    }
    operational_metrics = {
        "runtime_ms": {
            "median": statistics.median(durations),
            "p95": sorted(durations)[max(0, round(0.95 * len(durations)) - 1)],
            "total": sum(durations),
        },
        "model_usage": {
            "calls": len(usages),
            "http_attempts": sum(item.http_attempts for item in usages),
            "rate_limit_wait_ms": sum(item.rate_limit_wait_ms for item in usages),
            "input_tokens": sum(item.input_tokens for item in usages),
            "output_tokens": sum(item.output_tokens for item in usages),
            "total_tokens": sum(item.total_tokens for item in usages),
            "estimated_cost_usd": sum(item.estimated_cost_usd or 0 for item in usages),
        },
    }
    return {
        "schema_version": 2,
        "cases": len(results),
        "primary_metric": "unsafe_change_decision_f1",
        "unsafe_change_decision": decision_metrics,
        "safe_approval_precision": safe_approvals / len(approvals) if approvals else 0.0,
        "issue_category_micro": diagnosis_metrics,
        "evidence_reference_validity": sum(evidence_rates) / len(evidence_rates),
        "human_review_rate": human_reviews / len(results),
        "runtime_ms": operational_metrics["runtime_ms"],
        "model_usage": operational_metrics["model_usage"],
        "metric_groups": {
            "safety": {
                "unsafe_change_decision": decision_metrics,
                "safe_approval_precision": safe_approvals / len(approvals) if approvals else 0.0,
                "human_review_rate": human_reviews / len(results),
            },
            "diagnosis": diagnosis_metrics,
            "evidence": {
                "reference_validity": sum(evidence_rates) / len(evidence_rates),
            },
            "operational": operational_metrics,
        },
    }


def _runner_for_mode(
    mode: str,
    provider: LLMProvider | None,
) -> tuple[Callable[[CaseInput], AuditResult], str, str]:
    if mode == "verified":
        return run_verified, "deterministic", "collector+bubblewrap-v1"
    if provider is None:
        raise ValueError(f"mode {mode!r} requires a model provider")
    if mode == "baseline":
        return lambda case: run_baseline(case, provider), provider.name, provider.model
    if mode == "advanced":
        return lambda case: run_advanced(case, provider), provider.name, provider.model
    raise ValueError(f"unsupported mode: {mode}")


def run_benchmark(
    *,
    mode: str,
    provider: LLMProvider | None,
    cases_path: Path,
    gold_path: Path,
    output_dir: Path,
    only_case: str | None = None,
    limit: int | None = None,
) -> tuple[list[AuditResult], dict[str, Any]]:
    if mode not in _SUPPORTED_MODES:
        raise ValueError(f"unsupported mode: {mode}; choose one of {sorted(_SUPPORTED_MODES)}")
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

    runner, provider_name, model_name = _runner_for_mode(mode, provider)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[AuditResult] = []
    raw_path = output_dir / "raw-results.jsonl"
    with raw_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            result = runner(case)
            results.append(result)
            handle.write(canonical_json(result.model_dump(mode="json")) + "\n")

    metrics = compute_metrics(results, gold)
    metrics.update({"mode": mode, "provider": provider_name, "model": model_name})
    metrics_path = output_dir / "metrics.json"
    write_json(metrics_path, metrics)
    manifest_path = output_dir / "manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": 2,
            "mode": mode,
            "provider": provider_name,
            "model": model_name,
            "case_ids": [case.id for case in cases],
            "cases_sha256": _sha256_file(cases_path),
            "gold_sha256": _sha256_file(gold_path),
            "source_tree_sha256": _source_tree_sha256(),
            "raw_results": raw_path.name,
            "raw_results_sha256": _sha256_file(raw_path),
            "metrics_sha256": _sha256_file(metrics_path),
        },
    )
    return results, metrics
