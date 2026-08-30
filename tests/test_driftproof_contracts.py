from __future__ import annotations

import pytest

from driftproof.contracts import compile_contract
from driftproof.models import RuleKind


def kinds(context: str) -> set[RuleKind]:
    return {rule.kind for rule in compile_contract(context).rules}


def test_public_rename_contract_compiles_alias_rule() -> None:
    context = (
        "The public mart contract is `customer_id`, `customer_name`, `revenue_amount`. "
        "Upstream renamed the human-readable customer field, but downstream names must remain stable."
    )
    spec = compile_contract(context)
    assert RuleKind.PUBLIC_CONTRACT in {rule.kind for rule in spec.rules}
    alias = next(rule for rule in spec.rules if rule.kind == RuleKind.SOURCE_ALIAS)
    assert alias.output == "customer_name"


def test_derived_concat_contract_is_structured() -> None:
    context = (
        "The public model must expose `customer_id`, `display_name`, and `revenue_amount`. "
        "`display_name` is the trimmed concatenation of `first_name`, a single space, and `last_name`."
    )
    spec = compile_contract(context)
    rule = next(rule for rule in spec.rules if rule.kind == RuleKind.DERIVED_CONCAT)
    assert rule.output == "display_name"
    assert rule.fields == ["first_name", "last_name"]
    assert rule.parameters["separator"] == " "


def test_numeric_invalid_to_null_policy_compiles() -> None:
    context = (
        "`amount` is numeric downstream. Source text that is valid numeric data should be converted "
        "to DECIMAL. Invalid numeric text must become NULL rather than be coerced to zero or dropped."
    )
    spec = compile_contract(context)
    rule = next(rule for rule in spec.rules if rule.kind == RuleKind.NUMERIC_NULL_POLICY)
    assert rule.output == "amount"
    assert rule.parameters["invalid_policy"] == "null"


@pytest.mark.parametrize(
    ("context", "kind"),
    [
        (
            "The staging model was renamed during the refactor; use the current staging model rather than recreating the removed name.",
            RuleKind.DEPENDENCY_EXISTS,
        ),
        (
            "The model contract is exactly one current row per `customer_id`. If multiple records exist, choose the row with the greatest `updated_at`.",
            RuleKind.LATEST_RECORD,
        ),
        (
            "`customer_id` is required. Source rows with NULL, empty, or whitespace-only customer IDs are invalid and must be excluded.",
            RuleKind.REQUIRED_IDENTIFIER,
        ),
        (
            "Business status mapping is: `paid -> revenue`, `refunded -> refund`, `chargeback -> loss`.",
            RuleKind.CATEGORICAL_MAPPING,
        ),
        (
            "The macro accepts a required expression plus keyword argument `scale`. Normalize with `scale=100`.",
            RuleKind.MACRO_KEYWORD,
        ),
        (
            "Reporting dates use Asia/Kolkata local dates. Source timestamps are UTC. Convert before casting to DATE.",
            RuleKind.TIMEZONE_DATE,
        ),
        (
            "`gross_sales` is sales, `refunds` is positive, and `net_revenue = sales - refunds`.",
            RuleKind.SUBTRACTION_FORMULA,
        ),
    ],
)
def test_specialized_contracts_compile(context: str, kind: RuleKind) -> None:
    assert kind in kinds(context)


def test_preserve_rules_exclude_explicit_transformations() -> None:
    context = (
        "The public mart contract remains `customer_id`, `customer_name`, `revenue_amount`. "
        "The upstream name field may have changed. Revenue now arrives as text: valid numeric text "
        "becomes DECIMAL and invalid text becomes NULL."
    )
    spec = compile_contract(context)
    preserve = {rule.output for rule in spec.rules if rule.kind == RuleKind.PRESERVE_FIELD}
    assert "customer_id" in preserve
    assert "customer_name" not in preserve
    assert "revenue_amount" not in preserve


def test_rule_ids_are_deterministic() -> None:
    context = "`customer_id` is required. Empty and whitespace-only identifiers are invalid."
    first = compile_contract(context)
    second = compile_contract(context)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
