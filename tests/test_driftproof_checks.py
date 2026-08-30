from __future__ import annotations

from pathlib import Path

import pytest

from driftproof.checks import verify_contract
from driftproof.contracts import compile_contract
from driftproof.models import CheckStatus, RuleKind
from driftproof.project import snapshot_project


def make_project(
    tmp_path: Path,
    *,
    sql: str,
    headers: str = "customer_id,full_name,revenue_amount\n1,Ada,10\n",
    schema: str = "version: 2\nmodels: []\n",
    filename: str = "mart_model.sql",
) -> Path:
    (tmp_path / "models").mkdir(parents=True)
    (tmp_path / "input").mkdir()
    (tmp_path / "dbt_project.yml").write_text(
        "name: fixture\nversion: 1.0.0\nprofile: fixture\nmodel-paths: [models]\n",
        encoding="utf-8",
    )
    (tmp_path / "profiles.yml").write_text(
        "fixture:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: fixture.duckdb\n",
        encoding="utf-8",
    )
    (tmp_path / "models" / filename).write_text(sql, encoding="utf-8")
    (tmp_path / "models" / "schema.yml").write_text(schema, encoding="utf-8")
    (tmp_path / "input" / "raw.csv").write_text(headers, encoding="utf-8")
    return tmp_path


def status_for(root: Path, context: str, kind: RuleKind) -> CheckStatus:
    contract = compile_contract(context)
    rule = next(rule for rule in contract.rules if rule.kind == kind)
    results = verify_contract(snapshot_project(root), contract)
    return next(check.status for check in results if check.rule_id == rule.id)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "select customer_id, full_name as customer_name, revenue_amount from raw_customers\n",
            CheckStatus.PASS,
        ),
        (
            "select customer_id, cast(customer_id as varchar) as customer_name, revenue_amount from raw_customers\n",
            CheckStatus.FAIL,
        ),
    ],
)
def test_source_alias_check(tmp_path: Path, sql: str, expected: CheckStatus) -> None:
    root = make_project(tmp_path, sql=sql)
    context = (
        "The public mart contract is `customer_id`, `customer_name`, `revenue_amount`. "
        "Upstream renamed the human-readable name field."
    )
    assert status_for(root, context, RuleKind.SOURCE_ALIAS) == expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "select trim(first_name || ' ' || last_name) as display_name from raw_customers\n",
            CheckStatus.PASS,
        ),
        (
            "select trim(first_name || last_name) as display_name from raw_customers\n",
            CheckStatus.FAIL,
        ),
    ],
)
def test_derived_concat_check(tmp_path: Path, sql: str, expected: CheckStatus) -> None:
    root = make_project(tmp_path, sql=sql, headers="first_name,last_name\nAda,Lovelace\n")
    context = "`display_name` is the trimmed concatenation of `first_name`, a single space, and `last_name`."
    assert status_for(root, context, RuleKind.DERIVED_CONCAT) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("try_cast(amount_text as decimal(12, 2))", CheckStatus.PASS),
        ("coalesce(try_cast(amount_text as decimal(12, 2)), 0)", CheckStatus.FAIL),
        ("amount_text * 1.0", CheckStatus.FAIL),
    ],
)
def test_numeric_null_policy_check(tmp_path: Path, expression: str, expected: CheckStatus) -> None:
    root = make_project(
        tmp_path,
        sql=f"select {expression} as amount from raw_orders\n",
        headers="amount_text\ninvalid\n",
    )
    context = (
        "`amount` is numeric downstream. Convert valid text to DECIMAL. "
        "Invalid numeric text must become NULL rather than zero."
    )
    assert status_for(root, context, RuleKind.NUMERIC_NULL_POLICY) == expected


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("desc", CheckStatus.PASS), ("asc", CheckStatus.FAIL)],
)
def test_latest_record_direction(tmp_path: Path, direction: str, expected: CheckStatus) -> None:
    root = make_project(
        tmp_path,
        sql=(
            "select customer_id, email, updated_at from raw_updates "
            f"qualify row_number() over (partition by customer_id order by updated_at {direction}) = 1\n"
        ),
        headers="customer_id,email,updated_at\n1,a,2026-01-01\n",
    )
    context = "The model contract is one current row per `customer_id`. Choose the row with the greatest `updated_at`."
    assert status_for(root, context, RuleKind.LATEST_RECORD) == expected


@pytest.mark.parametrize(
    ("where_clause", "expected"),
    [
        ("where nullif(trim(customer_id), '') is not null", CheckStatus.PASS),
        ("where customer_id is not null", CheckStatus.FAIL),
    ],
)
def test_required_identifier_filter(
    tmp_path: Path, where_clause: str, expected: CheckStatus
) -> None:
    root = make_project(
        tmp_path,
        sql=f"select customer_id from raw_customers {where_clause}\n",
        headers="customer_id\n \n",
    )
    context = "`customer_id` is required. NULL, empty, and whitespace-only values must be excluded."
    assert status_for(root, context, RuleKind.REQUIRED_IDENTIFIER) == expected


def test_mapping_requires_logic_and_validation(tmp_path: Path) -> None:
    context = "Mapping is `paid -> revenue`, `refunded -> refund`, `chargeback -> loss`."
    schema = (
        "version: 2\nmodels:\n  - name: status\n    columns:\n      - name: business_status\n"
        "        data_tests:\n          - accepted_values:\n              arguments:\n"
        "                values: [revenue, refund, loss]\n"
    )
    safe = make_project(
        tmp_path / "safe",
        sql=(
            "select case when status = 'paid' then 'revenue' "
            "when status = 'refunded' then 'refund' "
            "when status = 'chargeback' then 'loss' end as business_status from raw_orders\n"
        ),
        headers="status\npaid\n",
        schema=schema,
    )
    unsafe = make_project(
        tmp_path / "unsafe",
        sql=(
            "select case when status = 'paid' then 'revenue' "
            "when status = 'refunded' then 'refund' "
            "when status = 'chargeback' then 'refund' end as business_status from raw_orders\n"
        ),
        headers="status\npaid\n",
        schema=schema,
    )
    assert status_for(safe, context, RuleKind.CATEGORICAL_MAPPING) == CheckStatus.PASS
    assert status_for(unsafe, context, RuleKind.CATEGORICAL_MAPPING) == CheckStatus.FAIL


@pytest.mark.parametrize(
    ("sql", "kind", "context", "expected"),
    [
        (
            "select {{ normalize_currency('amount', scale=100) }} as dollars from raw\n",
            RuleKind.MACRO_KEYWORD,
            "The macro has keyword argument `scale`; use `scale=100`.",
            CheckStatus.PASS,
        ),
        (
            "select {{ normalize_currency('amount', scale=10) }} as dollars from raw\n",
            RuleKind.MACRO_KEYWORD,
            "The macro has keyword argument `scale`; use `scale=100`.",
            CheckStatus.FAIL,
        ),
        (
            "select cast(timezone('Asia/Kolkata', ts::timestamp at time zone 'UTC') as date) as reporting_date from raw\n",
            RuleKind.TIMEZONE_DATE,
            "Source timestamps are UTC. Convert to Asia/Kolkata before casting to DATE.",
            CheckStatus.PASS,
        ),
        (
            "select cast(ts as date) as reporting_date from raw\n",
            RuleKind.TIMEZONE_DATE,
            "Source timestamps are UTC. Convert to Asia/Kolkata before casting to DATE.",
            CheckStatus.FAIL,
        ),
        (
            "select sum(case when kind = 'refund' then -amount else amount end) as net_revenue from raw\n",
            RuleKind.SUBTRACTION_FORMULA,
            "`net_revenue = sales - refunds`.",
            CheckStatus.PASS,
        ),
        (
            "select sum(amount) as net_revenue from raw\n",
            RuleKind.SUBTRACTION_FORMULA,
            "`net_revenue = sales - refunds`.",
            CheckStatus.FAIL,
        ),
    ],
)
def test_other_rule_checks(
    tmp_path: Path,
    sql: str,
    kind: RuleKind,
    context: str,
    expected: CheckStatus,
) -> None:
    root = make_project(tmp_path, sql=sql, headers="amount,kind,ts\n10,sale,2026-01-01\n")
    assert status_for(root, context, kind) == expected


def test_preserve_field_rejects_semantic_corruption(tmp_path: Path) -> None:
    context = "The mart contract remains `order_id`, `customer_id`, `amount`."
    safe = make_project(
        tmp_path / "safe",
        sql="select order_id, customer_id, amount from stg_orders\n",
        headers="order_id,customer_id,amount\n1,2,3\n",
        filename="mart_orders.sql",
    )
    unsafe = make_project(
        tmp_path / "unsafe",
        sql="select order_id, customer_id, amount * 0 as amount from stg_orders\n",
        headers="order_id,customer_id,amount\n1,2,3\n",
        filename="mart_orders.sql",
    )
    assert status_for(safe, context, RuleKind.PRESERVE_FIELD) == CheckStatus.PASS
    amount_rule = next(
        rule
        for rule in compile_contract(context).rules
        if rule.kind == RuleKind.PRESERVE_FIELD and rule.output == "amount"
    )
    results = verify_contract(snapshot_project(unsafe), compile_contract(context))
    amount_check = next(check for check in results if check.rule_id == amount_rule.id)
    assert amount_check.status == CheckStatus.FAIL
