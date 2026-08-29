from __future__ import annotations

import json
from pathlib import Path

import pytest

from mergeproof.benchmark import compute_metrics, load_cases, load_gold
from mergeproof.models import (
    AuditResult,
    Decision,
    Finding,
    FindingCategory,
    FindingStatus,
    GoldCase,
    Severity,
)

ROOT = Path(__file__).resolve().parents[1]


def result(
    case_id: str,
    decision: Decision,
    categories: list[FindingCategory] | None = None,
) -> AuditResult:
    findings = [
        Finding(
            category=category,
            severity=Severity.HIGH,
            title=category.value,
            explanation="fixture",
            evidence_ids=["fixture:evidence"],
            status=FindingStatus.VERIFIED,
        )
        for category in categories or []
    ]
    return AuditResult(
        case_id=case_id,
        mode="test",
        decision=decision,
        summary="fixture",
        confidence=1.0,
        findings=findings,
        provider="test",
        model="test",
    )


def test_frozen_benchmark_is_balanced_and_gold_separated() -> None:
    cases = load_cases(ROOT / "benchmark/cases.json")
    gold = load_gold(ROOT / "benchmark/gold.json")
    assert len(cases) == 24
    assert sum(item.safe_to_merge for item in gold.values()) == 12
    assert sum(not item.safe_to_merge for item in gold.values()) == 12
    assert {case.id for case in cases} == set(gold)
    raw_cases = json.loads((ROOT / "benchmark/cases.json").read_text(encoding="utf-8"))
    assert all("safe_to_merge" not in item for item in raw_cases)
    assert all("categories" not in item for item in raw_cases)


def test_perfect_decisions_score_one() -> None:
    gold = {
        "unsafe": GoldCase(
            id="unsafe",
            safe_to_merge=False,
            categories=[FindingCategory.TEST_FAILURE],
            rationale="fixture",
        ),
        "safe": GoldCase(id="safe", safe_to_merge=True, rationale="fixture"),
    }
    metrics = compute_metrics(
        [
            result("unsafe", Decision.REJECT, [FindingCategory.TEST_FAILURE]),
            result("safe", Decision.APPROVE),
        ],
        gold,
    )
    assert metrics["unsafe_change_decision"]["f1"] == pytest.approx(1.0)
    assert metrics["issue_category_micro"]["f1"] == pytest.approx(1.0)
    assert metrics["safe_approval_precision"] == pytest.approx(1.0)


def test_categories_on_wrong_cases_receive_no_credit() -> None:
    gold = {
        "a": GoldCase(
            id="a",
            safe_to_merge=False,
            categories=[FindingCategory.TEST_FAILURE],
            rationale="fixture",
        ),
        "b": GoldCase(
            id="b",
            safe_to_merge=False,
            categories=[FindingCategory.SECRET_EXPOSURE],
            rationale="fixture",
        ),
    }
    metrics = compute_metrics(
        [
            result("a", Decision.REJECT, [FindingCategory.SECRET_EXPOSURE]),
            result("b", Decision.REJECT, [FindingCategory.TEST_FAILURE]),
        ],
        gold,
    )
    assert metrics["unsafe_change_decision"]["f1"] == pytest.approx(1.0)
    assert metrics["issue_category_micro"]["f1"] == pytest.approx(0.0)
