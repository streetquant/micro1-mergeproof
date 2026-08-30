from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    HUMAN_REVIEW = "human_review"


class RuleKind(StrEnum):
    PUBLIC_CONTRACT = "public_contract"
    SOURCE_ALIAS = "source_alias"
    DERIVED_CONCAT = "derived_concat"
    NUMERIC_NULL_POLICY = "numeric_null_policy"
    DEPENDENCY_EXISTS = "dependency_exists"
    PRESERVE_FIELD = "preserve_field"
    LATEST_RECORD = "latest_record"
    REQUIRED_IDENTIFIER = "required_identifier"
    CATEGORICAL_MAPPING = "categorical_mapping"
    MACRO_KEYWORD = "macro_keyword"
    TIMEZONE_DATE = "timezone_date"
    SUBTRACTION_FORMULA = "subtraction_formula"


class ContractRule(StrictModel):
    id: str
    kind: RuleKind
    source_text: str
    output: str | None = None
    fields: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ContractSpec(StrictModel):
    context_sha256: str
    rules: list[ContractRule]
    unknown_sentences: list[str] = Field(default_factory=list)


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class CheckResult(StrictModel):
    id: str
    rule_id: str | None = None
    status: CheckStatus
    title: str
    detail: str
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BuildResult(StrictModel):
    command: list[str]
    returncode: int
    passed: bool
    stdout: str
    stderr: str
    duration_ms: int
    isolation: Literal["disposable_copy", "bubblewrap"]
    worktree_sha256: str


class AgentTrace(StrictModel):
    provider: str
    model: str
    request_hash: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    accepted_rule_ids: list[str] = Field(default_factory=list)
    rejected_proposals: list[str] = Field(default_factory=list)
    unresolved_sentences: list[str] = Field(default_factory=list)


class GateReport(StrictModel):
    schema_version: int = 1
    candidate_id: str
    verdict: Verdict
    summary: str
    project_sha256: str
    context_sha256: str
    trajectory_sha256: str | None = None
    build: BuildResult
    contract: ContractSpec
    agent_trace: AgentTrace | None = None
    checks: list[CheckResult]
    failed_check_ids: list[str] = Field(default_factory=list)
    inconclusive_check_ids: list[str] = Field(default_factory=list)
    human_approval_required: bool = True
    consequential_action_taken: bool = False
    certificate_sha256: str | None = None


class ApprovalCertificate(StrictModel):
    schema_version: int = 1
    certificate_type: Literal["driftproof.approval-certificate.v1"] = (
        "driftproof.approval-certificate.v1"
    )
    candidate_id: str
    verdict: Verdict
    report_sha256: str
    project_sha256: str
    context_sha256: str
    build_worktree_sha256: str
    passed_check_ids: list[str]
    failed_check_ids: list[str]
    inconclusive_check_ids: list[str]
    human_approval_required: bool = True
    consequential_action_taken: bool = False
    self_sha256: str = ""
