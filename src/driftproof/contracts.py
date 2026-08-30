from __future__ import annotations

import re
from collections.abc import Iterable

from mergeproof.utils import canonical_json, sha256_text

from .models import ContractRule, ContractSpec, RuleKind

IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_QUOTED_IDENTIFIER = re.compile(rf"`({IDENTIFIER})`")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(context: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(context) if part.strip()]


def _quoted(sentence: str) -> list[str]:
    return list(dict.fromkeys(_QUOTED_IDENTIFIER.findall(sentence)))


def _rule_id(
    kind: RuleKind,
    source_text: str,
    *,
    output: str | None = None,
    fields: Iterable[str] = (),
    parameters: dict[str, object] | None = None,
) -> str:
    payload = canonical_json(
        {
            "kind": kind.value,
            "source_text": source_text,
            "output": output,
            "fields": list(fields),
            "parameters": parameters or {},
        }
    )
    return f"R-{sha256_text(payload)[:12].upper()}"


def build_contract_rule(
    kind: RuleKind,
    source_text: str,
    *,
    output: str | None = None,
    fields: Iterable[str] = (),
    parameters: dict[str, object] | None = None,
) -> ContractRule:
    field_list = list(fields)
    parameter_map = parameters or {}
    return ContractRule(
        id=_rule_id(
            kind,
            source_text,
            output=output,
            fields=field_list,
            parameters=parameter_map,
        ),
        kind=kind,
        source_text=source_text,
        output=output,
        fields=field_list,
        parameters=parameter_map,
    )


def _public_fields(sentences: list[str]) -> tuple[list[str], list[str]]:
    fields: list[str] = []
    sources: list[str] = []
    for sentence in sentences:
        lower = sentence.lower()
        is_public_contract = (
            ("public" in lower and "contract" in lower)
            or "must expose" in lower
            or "mart contract remains" in lower
        )
        if not is_public_contract:
            continue
        quoted = _quoted(sentence)
        if quoted:
            fields.extend(quoted)
            sources.append(sentence)
    return list(dict.fromkeys(fields)), sources


def compile_contract(context: str) -> ContractSpec:
    sentences = _sentences(context)
    public_fields, public_sources = _public_fields(sentences)
    rules: list[ContractRule] = []
    matched_sentences: set[str] = set()

    for source in public_sources:
        matched_sentences.add(source)
    if public_fields:
        source_text = " ".join(public_sources)
        rules.append(
            build_contract_rule(
                RuleKind.PUBLIC_CONTRACT,
                source_text,
                fields=public_fields,
            )
        )

    rename_source = next(
        (
            sentence
            for sentence in sentences
            if "upstream" in sentence.lower()
            and ("renam" in sentence.lower() or "changed" in sentence.lower())
            and ("field" in sentence.lower() or "column" in sentence.lower())
        ),
        None,
    )
    if rename_source and public_fields:
        rename_targets = [field for field in public_fields if "name" in field.lower()]
        if len(rename_targets) == 1:
            matched_sentences.add(rename_source)
            rules.append(
                build_contract_rule(
                    RuleKind.SOURCE_ALIAS,
                    rename_source,
                    output=rename_targets[0],
                    parameters={"semantic_token": "name", "require_unique_source_candidate": True},
                )
            )

    derived_pattern = re.compile(
        rf"`(?P<output>{IDENTIFIER})`\s+is\s+the\s+trimmed\s+concatenation\s+of\s+"
        rf"`(?P<first>{IDENTIFIER})`.*?`(?P<last>{IDENTIFIER})`",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in derived_pattern.finditer(context):
        source = match.group(0).strip()
        matched_sentences.update(
            sentence for sentence in sentences if match.group("output") in sentence
        )
        rules.append(
            build_contract_rule(
                RuleKind.DERIVED_CONCAT,
                source,
                output=match.group("output"),
                fields=[match.group("first"), match.group("last")],
                parameters={"separator": " ", "trim": True},
            )
        )

    numeric_sentences = [
        sentence
        for sentence in sentences
        if "numeric" in sentence.lower()
        or ("decimal" in sentence.lower() and "invalid" in sentence.lower())
    ]
    numeric_policy_present = (
        "invalid" in context.lower()
        and "null" in context.lower()
        and ("numeric" in context.lower() or "decimal" in context.lower())
    )
    if numeric_policy_present:
        output: str | None = None
        for sentence in numeric_sentences:
            quoted = _quoted(sentence)
            if quoted:
                output = quoted[0]
                break
        if output is None:
            amount_fields = [field for field in public_fields if field.lower().endswith("amount")]
            if len(amount_fields) == 1:
                output = amount_fields[0]
        source = " ".join(numeric_sentences) or context.strip()
        matched_sentences.update(numeric_sentences)
        rules.append(
            build_contract_rule(
                RuleKind.NUMERIC_NULL_POLICY,
                source,
                output=output,
                parameters={
                    "invalid_policy": "null",
                    "required_conversion": "try_cast",
                    "target_type": "decimal",
                },
            )
        )

    dependency_sentence = next(
        (
            sentence
            for sentence in sentences
            if "model" in sentence.lower()
            and "renam" in sentence.lower()
            and ("staging" in sentence.lower() or "refactor" in sentence.lower())
        ),
        None,
    )
    if dependency_sentence:
        matched_sentences.add(dependency_sentence)
        rules.append(build_contract_rule(RuleKind.DEPENDENCY_EXISTS, dependency_sentence))

    transformed_outputs = {
        rule.output
        for rule in rules
        if rule.output is not None
        and rule.kind
        in {RuleKind.SOURCE_ALIAS, RuleKind.DERIVED_CONCAT, RuleKind.NUMERIC_NULL_POLICY}
    }
    preserve_source = next(
        (sentence for sentence in sentences if "contract remains" in sentence.lower()),
        None,
    )
    if preserve_source:
        matched_sentences.add(preserve_source)
        for field in public_fields:
            if field not in transformed_outputs:
                rules.append(
                    build_contract_rule(
                        RuleKind.PRESERVE_FIELD,
                        preserve_source,
                        output=field,
                        fields=[field],
                    )
                )

    greatest_match = re.search(rf"greatest\s+`({IDENTIFIER})`", context, flags=re.IGNORECASE)
    if greatest_match:
        order_field = greatest_match.group(1)
        key_match = re.search(
            rf"(?:one\s+(?:current\s+)?row|grain\s+is\s+one\s+row)\s+per\s+`({IDENTIFIER})`",
            context,
            flags=re.IGNORECASE,
        )
        source = next(
            (
                sentence
                for sentence in sentences
                if order_field in sentence and "greatest" in sentence.lower()
            ),
            context.strip(),
        )
        matched_sentences.add(source)
        rules.append(
            build_contract_rule(
                RuleKind.LATEST_RECORD,
                source,
                fields=[
                    value
                    for value in [key_match.group(1) if key_match else None, order_field]
                    if value
                ],
                parameters={"order_field": order_field, "direction": "desc"},
            )
        )

    required_match = re.search(rf"`({IDENTIFIER})`\s+is\s+required", context, flags=re.IGNORECASE)
    if required_match:
        identifier = required_match.group(1)
        source = next(
            (
                sentence
                for sentence in sentences
                if identifier in sentence and "required" in sentence.lower()
            ),
            required_match.group(0),
        )
        matched_sentences.add(source)
        rules.append(
            build_contract_rule(
                RuleKind.REQUIRED_IDENTIFIER,
                source,
                output=identifier,
                fields=[identifier],
                parameters={"reject_null": True, "reject_empty": True, "reject_whitespace": True},
            )
        )

    mappings = re.findall(rf"`?({IDENTIFIER})\s*->\s*({IDENTIFIER})`?", context)
    if mappings:
        source = next(
            (sentence for sentence in sentences if "->" in sentence),
            context.strip(),
        )
        matched_sentences.add(source)
        rules.append(
            build_contract_rule(
                RuleKind.CATEGORICAL_MAPPING,
                source,
                fields=list(dict.fromkeys(value for pair in mappings for value in pair)),
                parameters={
                    "pairs": [
                        {"source": source_value, "target": target}
                        for source_value, target in mappings
                    ]
                },
            )
        )

    keyword_match = re.search(
        rf"keyword\s+argument\s+`({IDENTIFIER})`", context, flags=re.IGNORECASE
    )
    if keyword_match:
        keyword = keyword_match.group(1)
        value_match = re.search(
            rf"\b{re.escape(keyword)}\s*=\s*([0-9]+(?:\.[0-9]+)?)",
            context,
            flags=re.IGNORECASE,
        )
        source = next(
            (
                sentence
                for sentence in sentences
                if keyword in sentence and "keyword" in sentence.lower()
            ),
            keyword_match.group(0),
        )
        matched_sentences.add(source)
        rules.append(
            build_contract_rule(
                RuleKind.MACRO_KEYWORD,
                source,
                output=keyword,
                parameters={
                    "keyword": keyword,
                    "value": value_match.group(1) if value_match else None,
                },
            )
        )

    zone_match = re.search(r"\b([A-Z][A-Za-z_]+/[A-Z][A-Za-z_]+)\b", context)
    if zone_match and "utc" in context.lower():
        zone = zone_match.group(1)
        source = next(
            (sentence for sentence in sentences if zone in sentence),
            context.strip(),
        )
        matched_sentences.add(source)
        rules.append(
            build_contract_rule(
                RuleKind.TIMEZONE_DATE,
                source,
                parameters={
                    "source_timezone": "UTC",
                    "target_timezone": zone,
                    "cast_after_conversion": True,
                },
            )
        )

    formula_match = re.search(
        rf"`?({IDENTIFIER})\s*=\s*({IDENTIFIER})\s*-\s*({IDENTIFIER})`?",
        context,
        flags=re.IGNORECASE,
    )
    if formula_match:
        output, positive, negative = formula_match.groups()
        source = next(
            (sentence for sentence in sentences if output in sentence and "=" in sentence),
            formula_match.group(0),
        )
        matched_sentences.add(source)
        rules.append(
            build_contract_rule(
                RuleKind.SUBTRACTION_FORMULA,
                source,
                output=output,
                fields=[positive, negative],
                parameters={"operator": "subtract"},
            )
        )

    unknown = [
        sentence
        for sentence in sentences
        if sentence not in matched_sentences and not sentence.startswith("#")
    ]
    return ContractSpec(
        context_sha256=sha256_text(context),
        rules=rules,
        unknown_sentences=unknown,
    )
