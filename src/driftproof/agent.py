from __future__ import annotations

import re
from typing import Any

from pydantic import Field, ValidationError

from mergeproof.models import StrictModel
from mergeproof.providers import LLMProvider, ProviderError
from mergeproof.utils import canonical_json, sha256_text, stable_request_hash

from .contracts import build_contract_rule
from .models import AgentTrace, ContractRule, ContractSpec, RuleKind
from .project import ProjectSnapshot

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

CLARIFIER_SYSTEM = """You are DriftProof's bounded Contract Clarifier Agent.
Your only task is to translate supplied unresolved business-contract sentences into typed rules.
You cannot approve a repair, run code, modify files, create SQL, weaken a check, or use hidden facts.
Use only identifiers listed in OBSERVED_IDENTIFIERS or explicitly written in the source sentence.
Every proposed source_text must be copied exactly from UNRESOLVED_SENTENCES.
Return one JSON object with keys rules and unresolved_sentences. Do not add prose outside JSON.

Allowed rule kinds and required shapes:
- public_contract: fields=[documented output columns]
- source_alias: output=<downstream column>, parameters.semantic_token=<source-name token>
- derived_concat: output=<output>, fields=[first,last], parameters={separator:" ",trim:true}
- numeric_null_policy: output=<output>, parameters={invalid_policy:"null",required_conversion:"try_cast",target_type:"decimal"}
- latest_record: fields=[entity_key,order_field], parameters={order_field:<field>,direction:"desc"}
- required_identifier: output=<identifier>, fields=[identifier], parameters={reject_null:true,reject_empty:true,reject_whitespace:true}
- categorical_mapping: parameters.pairs=[{source:<value>,target:<value>}]
- macro_keyword: output=<keyword>, parameters={keyword:<keyword>,value:<literal>}
- timezone_date: parameters={source_timezone:<zone>,target_timezone:<zone>,cast_after_conversion:true}
- subtraction_formula: output=<result>, fields=[positive_term,negative_term], parameters={operator:"subtract"}

When the sentence does not justify one exact supported rule, leave it unresolved. Never infer a
stronger policy than the sentence states."""


class RuleProposal(StrictModel):
    kind: RuleKind
    source_text: str
    output: str | None = None
    fields: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str


class ClarifierEnvelope(StrictModel):
    rules: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_sentences: list[str] = Field(default_factory=list)


def _infer_kind(raw: dict[str, Any]) -> str | None:
    if raw.get("kind") is not None:
        return str(raw["kind"])
    parameters = raw.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    fields = raw.get("fields")
    if not isinstance(fields, list):
        fields = []
    if parameters.get("operator") == "subtract":
        return RuleKind.SUBTRACTION_FORMULA.value
    if parameters.get("direction") == "desc" and parameters.get("order_field"):
        return RuleKind.LATEST_RECORD.value
    if parameters.get("invalid_policy") == "null":
        return RuleKind.NUMERIC_NULL_POLICY.value
    if parameters.get("separator") == " " and parameters.get("trim") is True:
        return RuleKind.DERIVED_CONCAT.value
    if parameters.get("pairs"):
        return RuleKind.CATEGORICAL_MAPPING.value
    if parameters.get("source_timezone") and parameters.get("target_timezone"):
        return RuleKind.TIMEZONE_DATE.value
    if parameters.get("keyword"):
        return RuleKind.MACRO_KEYWORD.value
    if parameters.get("reject_null") and parameters.get("reject_empty"):
        return RuleKind.REQUIRED_IDENTIFIER.value
    if parameters.get("semantic_token"):
        return RuleKind.SOURCE_ALIAS.value
    if fields and raw.get("output") is None and not parameters:
        return RuleKind.PUBLIC_CONTRACT.value
    return None


def _infer_source_text(
    raw: dict[str, Any],
    *,
    kind: str | None,
    unresolved: list[str],
) -> str | None:
    supplied = raw.get("source_text")
    if isinstance(supplied, str):
        return supplied
    fields = [str(value) for value in raw.get("fields", []) if isinstance(value, str)]
    output = raw.get("output")
    identifiers = [*fields, *([str(output)] if isinstance(output, str) else [])]
    candidates: list[str] = []
    for sentence in unresolved:
        backticked = set(_BACKTICKED.findall(sentence))
        lower = sentence.lower()
        matches_public = (
            kind == RuleKind.PUBLIC_CONTRACT.value and fields and set(fields) <= backticked
        )
        matches_subtraction = kind == RuleKind.SUBTRACTION_FORMULA.value and any(
            token in lower for token in ("deduct", "subtract", "less", "refund", "reduce")
        )
        matches_identifiers = bool(identifiers) and all(
            value in backticked for value in identifiers
        )
        if matches_public or matches_subtraction or matches_identifiers:
            candidates.append(sentence)
    return candidates[0] if len(candidates) == 1 else None


def _normalize_raw_proposal(
    raw: dict[str, Any],
    *,
    unresolved: list[str],
) -> dict[str, Any]:
    normalized = dict(raw)
    kind = _infer_kind(normalized)
    if kind is not None:
        normalized["kind"] = kind
    source_text = _infer_source_text(normalized, kind=kind, unresolved=unresolved)
    if source_text is not None:
        normalized["source_text"] = source_text
    normalized.setdefault(
        "rationale",
        "Schema normalized deterministically; strict identifier and rule admission still applies.",
    )
    normalized.setdefault("fields", [])
    normalized.setdefault("parameters", {})
    normalized.setdefault("output", None)
    return normalized


def _trace_output_sha256(
    *,
    accepted_rule_ids: list[str],
    rejected_proposals: list[str],
    unresolved_sentences: list[str],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "accepted_rule_ids": accepted_rule_ids,
                "rejected_proposals": rejected_proposals,
                "unresolved_sentences": unresolved_sentences,
            }
        )
    )


class ContractClarifier:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def _user_prompt(self, contract: ContractSpec, snapshot: ProjectSnapshot) -> str:
        observed = sorted(
            snapshot.csv_headers
            | snapshot.model_names
            | snapshot.refs
            | {item.output for item in snapshot.select_items}
        )
        payload = {
            "UNRESOLVED_SENTENCES": contract.unknown_sentences,
            "OBSERVED_IDENTIFIERS": observed,
            "EXISTING_RULES": [rule.model_dump(mode="json") for rule in contract.rules],
            "OUTPUT_SCHEMA": {
                "rules": [
                    {
                        "kind": "allowed rule kind",
                        "source_text": "exact unresolved sentence",
                        "output": "identifier or null",
                        "fields": ["identifiers"],
                        "parameters": {},
                        "rationale": "one sentence",
                    }
                ],
                "unresolved_sentences": ["exact unresolved sentences not converted"],
            },
        }
        return canonical_json(payload)

    def _validate_proposal(
        self,
        proposal: RuleProposal,
        *,
        unresolved: set[str],
        observed: set[str],
    ) -> str | None:
        if proposal.source_text not in unresolved:
            return "source_text is not an exact unresolved sentence"
        sentence_identifiers = set(_BACKTICKED.findall(proposal.source_text))
        allowed = observed | sentence_identifiers
        identifiers = [value for value in [proposal.output, *proposal.fields] if value]
        invalid = [value for value in identifiers if not _IDENTIFIER.fullmatch(value)]
        if invalid:
            return f"invalid identifier syntax: {invalid}"
        invented = [value for value in identifiers if value not in allowed]
        if invented:
            return f"identifiers are not observed or stated: {invented}"

        if proposal.kind == RuleKind.SUBTRACTION_FORMULA:
            if proposal.output is None or len(proposal.fields) != 2:
                return "subtraction_formula requires one output and two fields"
            if proposal.parameters != {"operator": "subtract"}:
                return "subtraction_formula parameters must be exactly operator=subtract"
        elif proposal.kind == RuleKind.DERIVED_CONCAT:
            if proposal.output is None or len(proposal.fields) != 2:
                return "derived_concat requires one output and two fields"
            if proposal.parameters != {"separator": " ", "trim": True}:
                return "derived_concat parameters must require trim and a single-space separator"
        elif proposal.kind == RuleKind.NUMERIC_NULL_POLICY:
            expected = {
                "invalid_policy": "null",
                "required_conversion": "try_cast",
                "target_type": "decimal",
            }
            if proposal.output is None or proposal.parameters != expected:
                return "numeric_null_policy shape is invalid"
        elif proposal.kind == RuleKind.LATEST_RECORD:
            if len(proposal.fields) != 2:
                return "latest_record requires entity and order fields"
            if proposal.parameters.get("direction") != "desc":
                return "latest_record may only encode an explicit greatest/latest rule"
        elif proposal.kind == RuleKind.REQUIRED_IDENTIFIER:
            if proposal.output is None or proposal.fields != [proposal.output]:
                return "required_identifier must bind exactly one output"
        elif proposal.kind == RuleKind.MACRO_KEYWORD:
            if proposal.output is None or proposal.parameters.get("keyword") != proposal.output:
                return "macro_keyword output and keyword must match"
        elif proposal.kind == RuleKind.SOURCE_ALIAS:
            if proposal.output is None or not proposal.parameters.get("semantic_token"):
                return "source_alias requires output and semantic_token"
        elif proposal.kind == RuleKind.PUBLIC_CONTRACT:
            if not proposal.fields:
                return "public_contract requires at least one documented field"
        elif proposal.kind == RuleKind.CATEGORICAL_MAPPING:
            pairs = proposal.parameters.get("pairs")
            if not isinstance(pairs, list) or not pairs:
                return "categorical_mapping requires non-empty pairs"
        elif proposal.kind == RuleKind.TIMEZONE_DATE:
            required = {"source_timezone", "target_timezone", "cast_after_conversion"}
            if set(proposal.parameters) != required or not proposal.parameters.get(
                "cast_after_conversion"
            ):
                return "timezone_date parameters are incomplete"
        else:
            return f"agent proposals may not create {proposal.kind.value} rules"
        return None

    def clarify(
        self, contract: ContractSpec, snapshot: ProjectSnapshot
    ) -> tuple[ContractSpec, AgentTrace]:
        user_prompt = self._user_prompt(contract, snapshot)
        request_hash = stable_request_hash(
            "contract_clarifier",
            self.provider.model,
            CLARIFIER_SYSTEM,
            user_prompt,
        )
        unresolved_list = list(contract.unknown_sentences)
        unresolved = set(unresolved_list)
        observed = (
            snapshot.csv_headers
            | snapshot.model_names
            | snapshot.refs
            | {item.output for item in snapshot.select_items}
        )
        try:
            response = self.provider.complete_json(
                agent="contract_clarifier",
                system=CLARIFIER_SYSTEM,
                user=user_prompt,
            )
            envelope = ClarifierEnvelope.model_validate(response.data)
        except (ProviderError, ValidationError) as exc:
            trace = AgentTrace(
                provider=self.provider.name,
                model=self.provider.model,
                request_hash=request_hash,
                rejected_proposals=[f"clarifier failure: {exc}"],
                unresolved_sentences=sorted(unresolved),
            )
            return contract, trace

        accepted: list[ContractRule] = []
        rejected: list[str] = []
        resolved_sources: set[str] = set()
        existing_ids = {rule.id for rule in contract.rules}
        for index, raw_proposal in enumerate(envelope.rules):
            try:
                proposal = RuleProposal.model_validate(
                    _normalize_raw_proposal(
                        raw_proposal,
                        unresolved=unresolved_list,
                    )
                )
            except ValidationError as exc:
                rejected.append(f"proposal {index}: schema validation failed: {exc}")
                continue
            error = self._validate_proposal(
                proposal,
                unresolved=unresolved,
                observed=observed,
            )
            if error is not None:
                rejected.append(f"proposal {index}: {error}")
                continue
            rule = build_contract_rule(
                proposal.kind,
                proposal.source_text,
                output=proposal.output,
                fields=proposal.fields,
                parameters=proposal.parameters,
            )
            if rule.id in existing_ids or any(item.id == rule.id for item in accepted):
                rejected.append(f"proposal {index}: duplicate rule {rule.id}")
                continue
            accepted.append(rule)
            resolved_sources.add(proposal.source_text)

        declared_unresolved = set(envelope.unresolved_sentences)
        invalid_unresolved = sorted(declared_unresolved - unresolved)
        if invalid_unresolved:
            rejected.append(f"invented unresolved sentences: {invalid_unresolved}")
        remaining = sorted((unresolved - resolved_sources) | (declared_unresolved & unresolved))
        enriched = ContractSpec(
            context_sha256=contract.context_sha256,
            rules=[*contract.rules, *accepted],
            unknown_sentences=remaining,
        )
        trace = AgentTrace(
            provider=response.usage.provider,
            model=response.usage.model,
            request_hash=response.usage.request_hash,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            accepted_rule_ids=[rule.id for rule in accepted],
            rejected_proposals=rejected,
            unresolved_sentences=remaining,
        )
        return enriched, trace
