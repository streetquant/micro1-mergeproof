from __future__ import annotations

import re
from collections.abc import Iterable

from mergeproof.utils import canonical_json, sha256_text

from .models import CheckResult, CheckStatus, ContractRule, ContractSpec, RuleKind
from .project import ProjectSnapshot, SelectItem


def _check_id(rule: ContractRule | None, title: str) -> str:
    payload = canonical_json({"rule_id": rule.id if rule else None, "title": title})
    return f"C-{sha256_text(payload)[:12].upper()}"


def _result(
    rule: ContractRule | None,
    status: CheckStatus,
    title: str,
    detail: str,
    *,
    evidence: Iterable[str] = (),
    metadata: dict[str, object] | None = None,
) -> CheckResult:
    return CheckResult(
        id=_check_id(rule, title),
        rule_id=rule.id if rule else None,
        status=status,
        title=title,
        detail=detail,
        evidence=list(evidence),
        metadata=metadata or {},
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _expressions(snapshot: ProjectSnapshot, output: str | None) -> list[SelectItem]:
    return snapshot.expressions_for(output) if output else []


def _public_contract(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    outputs = {item.output.lower() for item in snapshot.select_items}
    missing = [field for field in rule.fields if field.lower() not in outputs]
    status = CheckStatus.PASS if not missing else CheckStatus.FAIL
    return _result(
        rule,
        status,
        "Public output contract is represented",
        "All documented fields are projected by the candidate."
        if not missing
        else f"Documented fields are not projected anywhere: {missing}",
        evidence=sorted(snapshot.sql_files),
        metadata={"missing": missing, "observed_outputs": sorted(outputs)},
    )


def _source_alias(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    output = rule.output
    if output is None:
        return _result(
            rule,
            CheckStatus.INCONCLUSIVE,
            "Renamed source field is preserved through an alias",
            "The contract parser did not identify the downstream output field.",
        )
    token = str(rule.parameters.get("semantic_token", "")).lower()
    candidates = sorted(
        header
        for header in snapshot.csv_headers
        if token and token in header.lower() and header.lower() != output.lower()
    )
    items = _expressions(snapshot, output)
    matching = [
        {"path": item.path, "expression": item.expression, "source": candidate}
        for item in items
        for candidate in candidates
        if re.search(rf"\b{re.escape(candidate)}\b", item.expression, flags=re.IGNORECASE)
    ]
    if len(candidates) != 1:
        return _result(
            rule,
            CheckStatus.INCONCLUSIVE,
            "Renamed source field is preserved through an alias",
            f"Expected one semantic source candidate for {output}; observed {candidates}.",
            evidence=sorted(snapshot.sql_files),
            metadata={"source_candidates": candidates},
        )
    status = CheckStatus.PASS if matching else CheckStatus.FAIL
    return _result(
        rule,
        status,
        "Renamed source field is preserved through an alias",
        f"{candidates[0]} is visibly aliased to {output}."
        if matching
        else f"The unique semantic source field {candidates[0]} is not used to produce {output}.",
        evidence=sorted({item.path for item in items}),
        metadata={"source_candidates": candidates, "matching_expressions": matching},
    )


def _derived_concat(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    output = rule.output
    items = _expressions(snapshot, output)
    separator = str(rule.parameters.get("separator", " "))
    first, last = [*rule.fields, "", ""][:2]
    valid: list[SelectItem] = []
    for item in items:
        expression = _compact(item.expression)
        has_separator = f"'{separator}'" in item.expression or f'"{separator}"' in item.expression
        if (
            "trim(" in expression
            and re.search(rf"\b{re.escape(first.lower())}\b", expression)
            and re.search(rf"\b{re.escape(last.lower())}\b", expression)
            and has_separator
        ):
            valid.append(item)
    status = CheckStatus.PASS if valid else CheckStatus.FAIL
    return _result(
        rule,
        status,
        "Documented derived text expression is exact",
        f"{output} uses trim, both source fields, and the required separator."
        if valid
        else f"No expression for {output} contains trim({first} + {separator!r} + {last}).",
        evidence=sorted({item.path for item in items}),
        metadata={"expressions": [item.expression for item in items]},
    )


def _numeric_null_policy(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    items = _expressions(snapshot, rule.output)
    valid: list[SelectItem] = []
    unsafe: list[SelectItem] = []
    for item in items:
        expression = _compact(item.expression)
        uses_conversion = "try_cast(" in expression and "decimal" in expression
        substitutes_invalid = "coalesce(" in expression or "ifnull(" in expression
        if uses_conversion and not substitutes_invalid:
            valid.append(item)
        elif uses_conversion or substitutes_invalid:
            unsafe.append(item)
    status = CheckStatus.PASS if valid and not unsafe else CheckStatus.FAIL
    detail = (
        "Invalid numeric text remains NULL after an explicit DECIMAL try_cast."
        if status == CheckStatus.PASS
        else "The numeric conversion is missing or replaces invalid values instead of preserving NULL."
    )
    return _result(
        rule,
        status,
        "Invalid numeric input follows the documented NULL policy",
        detail,
        evidence=sorted({item.path for item in items}),
        metadata={
            "valid_expressions": [item.expression for item in valid],
            "unsafe_expressions": [item.expression for item in unsafe],
            "all_expressions": [item.expression for item in items],
        },
    )


def _dependencies(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    missing = sorted(snapshot.refs - snapshot.model_names)
    status = CheckStatus.PASS if not missing else CheckStatus.FAIL
    return _result(
        rule,
        status,
        "All dbt model references resolve to observed models",
        "No stale dbt refs remain." if not missing else f"Missing referenced models: {missing}",
        evidence=sorted(snapshot.sql_files),
        metadata={"refs": sorted(snapshot.refs), "model_names": sorted(snapshot.model_names)},
    )


def _preserve_field(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    output = rule.output
    items = _expressions(snapshot, output)
    if output is None:
        return _result(
            rule,
            CheckStatus.INCONCLUSIVE,
            "Unchanged contract field remains a pass-through",
            "No output field was compiled for this preservation rule.",
        )
    preferred = [item for item in items if "mart" in item.path.lower()]
    reviewed = preferred or items
    plain = [
        item
        for item in reviewed
        if re.fullmatch(
            rf"(?:[A-Za-z_][A-Za-z0-9_]*\.)?{re.escape(output)}",
            item.expression.strip(),
            flags=re.IGNORECASE,
        )
    ]
    transformed = [item for item in reviewed if item not in plain]
    if not reviewed:
        status = CheckStatus.INCONCLUSIVE
        detail = f"No explicit projection for preserved field {output} was found."
    elif transformed:
        status = CheckStatus.FAIL
        detail = (
            f"Preserved field {output} is modified by: {[item.expression for item in transformed]}"
        )
    else:
        status = CheckStatus.PASS
        detail = f"Preserved field {output} remains a direct pass-through."
    return _result(
        rule,
        status,
        "Unchanged contract field remains a pass-through",
        detail,
        evidence=sorted({item.path for item in reviewed}),
        metadata={"expressions": [item.expression for item in reviewed]},
    )


def _latest_record(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    order_field = str(rule.parameters.get("order_field", ""))
    sql = _compact(snapshot.sql_text)
    has_operator = any(token in sql for token in ("row_number(", "arg_max(", "max_by("))
    has_descending_order = (
        re.search(rf"order\s+by\s+{re.escape(order_field.lower())}\s+desc\b", sql) is not None
    )
    has_ascending_order = (
        re.search(rf"order\s+by\s+{re.escape(order_field.lower())}\s+asc\b", sql) is not None
    )
    status = (
        CheckStatus.PASS
        if has_operator and has_descending_order and not has_ascending_order
        else CheckStatus.FAIL
    )
    return _result(
        rule,
        status,
        "Latest-record selection follows the greatest documented timestamp",
        f"A descending {order_field} window selection is visible."
        if status == CheckStatus.PASS
        else f"Expected a latest-record operator ordered by {order_field} DESC.",
        evidence=sorted(snapshot.sql_files),
        metadata={
            "has_latest_operator": has_operator,
            "has_descending_order": has_descending_order,
            "has_ascending_order": has_ascending_order,
        },
    )


def _required_identifier(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    identifier = rule.output or (rule.fields[0] if rule.fields else "")
    sql = _compact(snapshot.sql_text)
    has_where = " where " in f" {sql} "
    has_trim = re.search(rf"trim\s*\([^)]*\b{re.escape(identifier.lower())}\b", sql) is not None
    has_empty_rejection = "nullif(" in sql or "<> ''" in sql or "!= ''" in sql
    status = (
        CheckStatus.PASS if has_where and has_trim and has_empty_rejection else CheckStatus.FAIL
    )
    return _result(
        rule,
        status,
        "Required identifier rejects NULL, empty, and whitespace-only values",
        f"{identifier} has an explicit trimmed rejection filter."
        if status == CheckStatus.PASS
        else f"{identifier} is not protected by a trimmed empty-value filter.",
        evidence=sorted(snapshot.sql_files),
        metadata={
            "has_where": has_where,
            "has_trim": has_trim,
            "has_empty_rejection": has_empty_rejection,
        },
    )


def _mapping(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    sql = _compact(snapshot.sql_text)
    yaml = _compact(snapshot.yaml_text)
    pairs = list(rule.parameters.get("pairs", []))
    missing_logic: list[dict[str, str]] = []
    missing_validation: list[str] = []
    for raw_pair in pairs:
        pair = dict(raw_pair)
        source = str(pair.get("source", ""))
        target = str(pair.get("target", ""))
        mapping_pattern = re.compile(
            rf"when\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*['\"]{re.escape(source.lower())}['\"]\s+"
            rf"then\s+['\"]{re.escape(target.lower())}['\"]"
        )
        if mapping_pattern.search(sql) is None:
            missing_logic.append({"source": source, "target": target})
        if target.lower() not in yaml:
            missing_validation.append(target)
    has_explicit_validation = "accepted_values" in yaml
    status = (
        CheckStatus.PASS
        if not missing_logic and not missing_validation and has_explicit_validation
        else CheckStatus.FAIL
    )
    return _result(
        rule,
        status,
        "Categorical mapping and validation match the documented table",
        "Every mapping and accepted output remains explicit."
        if status == CheckStatus.PASS
        else "Mapping logic or accepted-values validation is incomplete or incorrect.",
        evidence=sorted([*snapshot.sql_files, *snapshot.yaml_files]),
        metadata={
            "missing_logic": missing_logic,
            "missing_validation": missing_validation,
            "has_explicit_validation": has_explicit_validation,
        },
    )


def _macro_keyword(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    keyword = str(rule.parameters.get("keyword", ""))
    value = str(rule.parameters.get("value", ""))
    model_sql = _compact(
        "\n".join(text for path, text in snapshot.sql_files.items() if path.startswith("models/"))
    )
    found = (
        re.search(
            rf"\b{re.escape(keyword.lower())}\s*=\s*{re.escape(value.lower())}\b",
            model_sql,
        )
        is not None
    )
    status = CheckStatus.PASS if found else CheckStatus.FAIL
    return _result(
        rule,
        status,
        "Macro call uses the current documented keyword and value",
        f"Observed {keyword}={value}." if found else f"Did not observe {keyword}={value}.",
        evidence=sorted(snapshot.sql_files),
    )


def _timezone(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    source_zone = str(rule.parameters.get("source_timezone", "UTC"))
    target_zone = str(rule.parameters.get("target_timezone", ""))
    sql = _compact(snapshot.sql_text)
    source_index = sql.find(source_zone.lower())
    target_index = sql.find(target_zone.lower())
    has_conversion = "timezone(" in sql or "at time zone" in sql
    correct_direction = target_index >= 0 and source_index >= 0 and target_index < source_index
    cast_after = re.search(r"cast\s*\([^)]*(?:timezone|at time zone).*?as\s+date", sql) is not None
    status = (
        CheckStatus.PASS
        if has_conversion and correct_direction and cast_after
        else CheckStatus.FAIL
    )
    return _result(
        rule,
        status,
        "Timezone conversion occurs before DATE truncation in the documented direction",
        f"Observed {source_zone} to {target_zone} before DATE casting."
        if status == CheckStatus.PASS
        else f"Expected {source_zone} to {target_zone} conversion before DATE casting.",
        evidence=sorted(snapshot.sql_files),
        metadata={
            "has_conversion": has_conversion,
            "correct_direction": correct_direction,
            "cast_after": cast_after,
        },
    )


def _subtraction(snapshot: ProjectSnapshot, rule: ContractRule) -> CheckResult:
    items = _expressions(snapshot, rule.output)
    subtracting = [item for item in items if "-" in item.expression]
    status = CheckStatus.PASS if subtracting else CheckStatus.FAIL
    return _result(
        rule,
        status,
        "Documented subtraction formula retains a negative refund term",
        f"{rule.output} contains an explicit subtraction/negative term."
        if subtracting
        else f"No subtractive expression produces {rule.output}.",
        evidence=sorted({item.path for item in items}),
        metadata={"expressions": [item.expression for item in items]},
    )


_RULE_CHECKERS = {
    RuleKind.PUBLIC_CONTRACT: _public_contract,
    RuleKind.SOURCE_ALIAS: _source_alias,
    RuleKind.DERIVED_CONCAT: _derived_concat,
    RuleKind.NUMERIC_NULL_POLICY: _numeric_null_policy,
    RuleKind.DEPENDENCY_EXISTS: _dependencies,
    RuleKind.PRESERVE_FIELD: _preserve_field,
    RuleKind.LATEST_RECORD: _latest_record,
    RuleKind.REQUIRED_IDENTIFIER: _required_identifier,
    RuleKind.CATEGORICAL_MAPPING: _mapping,
    RuleKind.MACRO_KEYWORD: _macro_keyword,
    RuleKind.TIMEZONE_DATE: _timezone,
    RuleKind.SUBTRACTION_FORMULA: _subtraction,
}


def verify_contract(snapshot: ProjectSnapshot, contract: ContractSpec) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rule in contract.rules:
        checker = _RULE_CHECKERS.get(rule.kind)
        if checker is None:
            results.append(
                _result(
                    rule,
                    CheckStatus.INCONCLUSIVE,
                    "Unsupported compiled contract rule",
                    f"No verifier is registered for {rule.kind.value}.",
                )
            )
        else:
            results.append(checker(snapshot, rule))

    yaml = _compact(snapshot.yaml_text)
    suppression_patterns = {
        "disabled resource": re.search(r"\benabled\s*:\s*false\b", yaml) is not None,
        "warning-only test": re.search(r"\bseverity\s*:\s*warn\b", yaml) is not None,
    }
    present = sorted(name for name, matched in suppression_patterns.items() if matched)
    results.append(
        _result(
            None,
            CheckStatus.FAIL if present else CheckStatus.PASS,
            "Verification controls were not weakened",
            "No disabled or warning-only validation controls were found."
            if not present
            else f"Potential test suppression detected: {present}",
            evidence=sorted(snapshot.yaml_files),
            metadata={"suppression_signals": present},
        )
    )
    return results
