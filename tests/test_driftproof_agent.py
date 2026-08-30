from __future__ import annotations

import json
from pathlib import Path

from driftproof.agent import ContractClarifier
from driftproof.models import ContractSpec, RuleKind
from driftproof.project import ProjectSnapshot, SelectItem
from mergeproof.providers import LLMProvider


class StaticProvider(LLMProvider):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(model="static-clarifier")
        self.payload = payload

    @property
    def name(self) -> str:
        return "static"

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        return json.dumps(self.payload), {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }


class FailingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(model="failing-clarifier")

    @property
    def name(self) -> str:
        return "failing"

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        raise ValueError("deliberate failure")


def snapshot() -> ProjectSnapshot:
    return ProjectSnapshot(
        root=Path("."),
        tree_sha256="a" * 64,
        sql_files={"models/revenue.sql": "select sales, refunds, sales + refunds as net_revenue"},
        yaml_files={},
        csv_headers={"sales", "refunds"},
        model_names={"revenue"},
        refs=set(),
        select_items=[
            SelectItem("models/revenue.sql", "sales", "sales"),
            SelectItem("models/revenue.sql", "refunds", "refunds"),
            SelectItem("models/revenue.sql", "sales + refunds", "net_revenue"),
        ],
    )


def contract(sentence: str) -> ContractSpec:
    return ContractSpec(
        context_sha256="b" * 64,
        rules=[],
        unknown_sentences=[sentence],
    )


def valid_payload(sentence: str) -> dict[str, object]:
    return {
        "rules": [
            {
                "kind": "subtraction_formula",
                "source_text": sentence,
                "output": "net_revenue",
                "fields": ["sales", "refunds"],
                "parameters": {"operator": "subtract"},
                "rationale": "Refunded cash is a deduction from booked sales.",
            }
        ],
        "unresolved_sentences": [],
    }


def test_agent_admits_exact_typed_rule() -> None:
    sentence = (
        "Finance policy says refunded cash is a deduction from booked sales; the exposed metric "
        "`net_revenue` uses `sales` and `refunds`."
    )
    enriched, trace = ContractClarifier(StaticProvider(valid_payload(sentence))).clarify(
        contract(sentence), snapshot()
    )
    assert len(enriched.rules) == 1
    assert enriched.rules[0].kind == RuleKind.SUBTRACTION_FORMULA
    assert enriched.unknown_sentences == []
    assert trace.accepted_rule_ids == [enriched.rules[0].id]
    assert trace.rejected_proposals == []
    assert trace.total_tokens == 15


def test_agent_rejects_invented_identifier() -> None:
    sentence = "Refunded cash reduces `net_revenue` computed from `sales` and `refunds`."
    payload = valid_payload(sentence)
    rule = dict(payload["rules"][0])  # type: ignore[index]
    rule["output"] = "invented_profit"
    payload["rules"] = [rule]
    enriched, trace = ContractClarifier(StaticProvider(payload)).clarify(
        contract(sentence), snapshot()
    )
    assert enriched.rules == []
    assert enriched.unknown_sentences == [sentence]
    assert "not observed or stated" in trace.rejected_proposals[0]


def test_agent_rejects_non_exact_source_sentence() -> None:
    sentence = "Refunded cash reduces `net_revenue` computed from `sales` and `refunds`."
    payload = valid_payload(sentence)
    rule = dict(payload["rules"][0])  # type: ignore[index]
    rule["source_text"] = sentence + " invented"
    payload["rules"] = [rule]
    enriched, trace = ContractClarifier(StaticProvider(payload)).clarify(
        contract(sentence), snapshot()
    )
    assert enriched.rules == []
    assert "exact unresolved sentence" in trace.rejected_proposals[0]


def test_agent_failure_fails_closed() -> None:
    sentence = "Refunded cash reduces `net_revenue` computed from `sales` and `refunds`."
    original = contract(sentence)
    enriched, trace = ContractClarifier(FailingProvider()).clarify(original, snapshot())
    assert enriched == original
    assert trace.accepted_rule_ids == []
    assert trace.unresolved_sentences == [sentence]
    assert "clarifier failure" in trace.rejected_proposals[0]
