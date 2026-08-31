from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


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
    schema_version: Literal[1] = 1
    agent: Literal["contract_clarifier"] = "contract_clarifier"
    provider: str
    model: str
    request_hash: str
    output_sha256: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    accepted_rule_ids: list[str] = Field(default_factory=list)
    rejected_proposals: list[str] = Field(default_factory=list)
    unresolved_sentences: list[str] = Field(default_factory=list)


class GateReport(StrictModel):
    schema_version: Literal[1] = 1
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
    human_approval_required: Literal[True] = True
    consequential_action_taken: Literal[False] = False
    certificate_sha256: str | None = None


class ApprovalCertificate(StrictModel):
    schema_version: Literal[1] = 1
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
    human_approval_required: Literal[True] = True
    consequential_action_taken: Literal[False] = False
    self_sha256: str = ""


class DriftProofReviewRequest(StrictModel):
    """Versioned declarative input for one dbt review."""

    schema_version: Literal[1] = 1
    protocol: Literal["driftproof.request.v1"] = "driftproof.request.v1"
    project: str = Field(min_length=1)
    context: str | None = None
    output: str | None = None
    work_root: str | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    isolation: Literal["auto", "disposable_copy", "bubblewrap"] = "auto"
    allow_unconfined: bool = False
    agent_provider: str | None = None
    agent_model: str = "openai/gpt-oss-20b"
    agent_record_dir: str | None = None
    agent_replay_dir: str | None = None
    allow_external_provider: bool = False
    response_file: str | None = None
    replace_output: bool = False
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

    @field_validator(
        "project",
        "context",
        "output",
        "work_root",
        "agent_record_dir",
        "agent_replay_dir",
        "response_file",
    )
    @classmethod
    def paths_may_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("path fields may not be blank")
        return value


class DriftProofPreflightResponse(StrictModel):
    schema_version: Literal[1] = 1
    protocol: Literal["driftproof.preflight.v1"] = "driftproof.preflight.v1"
    status: Literal["valid_input"] = "valid_input"
    project: str
    context: str
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sql_files: int = Field(ge=0)
    yaml_files: int = Field(ge=0)
    models: int = Field(ge=0)
    references: int = Field(ge=0)
    compiled_rules: int = Field(ge=0)
    rule_kinds: list[RuleKind]
    unresolved_sentences: list[str]
    deterministic_contract_complete: bool
    review_can_run: Literal[True] = True
    recommended_action: Literal["run_review", "clarify_business_context"]
    human_approval_required: Literal[True] = True
    consequential_action_taken: Literal[False] = False


class DriftProofContextTemplateResponse(StrictModel):
    schema_version: Literal[1] = 1
    protocol: Literal["driftproof.context-template.v1"] = "driftproof.context-template.v1"
    content: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    supported_rule_kinds: list[RuleKind]
    output: str | None = None
    consequential_action_taken: Literal[False] = False


class DriftProofNavigationResponse(StrictModel):
    schema_version: Literal[1] = 1
    protocol: Literal["driftproof.agent.v1"] = "driftproof.agent.v1"
    tool_version: str = Field(min_length=1)
    status: Literal["valid_review"] = "valid_review"
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    candidate_id: str
    verdict: Verdict
    exit_code: Literal[0, 10, 20]
    recommended_action: Literal[
        "human_approval",
        "repair_required",
        "evidence_or_human_escalation",
    ]
    summary: str
    project: str
    context: str
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_worktree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle: str
    report: str
    certificate: str
    manifest: str
    human_report: str
    human_report_markdown: str
    certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_verified: Literal[True]
    failed_checks: int = Field(ge=0)
    inconclusive_checks: int = Field(ge=0)
    failed_check_ids: list[str]
    inconclusive_check_ids: list[str]
    verify_argv: list[str] = Field(min_length=3)
    response_file: str | None = None
    human_approval_required: Literal[True]
    consequential_action_taken: Literal[False]

    @model_validator(mode="after")
    def verdict_fields_are_consistent(self) -> DriftProofNavigationResponse:
        expected_exit = {
            Verdict.APPROVE: 0,
            Verdict.REJECT: 10,
            Verdict.HUMAN_REVIEW: 20,
        }[self.verdict]
        expected_action = {
            Verdict.APPROVE: "human_approval",
            Verdict.REJECT: "repair_required",
            Verdict.HUMAN_REVIEW: "evidence_or_human_escalation",
        }[self.verdict]
        if self.exit_code != expected_exit:
            raise ValueError("navigation exit code does not match the verdict")
        if self.recommended_action != expected_action:
            raise ValueError("recommended action does not match the verdict")
        return self


class DriftProofErrorResponse(StrictModel):
    schema_version: Literal[1] = 1
    protocol: Literal["driftproof.agent.v1"] = "driftproof.agent.v1"
    tool_version: str = Field(min_length=1)
    status: Literal["invalid_review"] = "invalid_review"
    verdict: Literal["human_review"] = "human_review"
    exit_code: Literal[30] = 30
    context: str
    error: str
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    detail: str
    hint: str
    retryable: bool
    recommended_action: Literal["repair_input_or_runtime"] = "repair_input_or_runtime"
    partial_result_trusted: Literal[False] = False
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    response_file: str | None = None
    human_approval_required: Literal[True]
    consequential_action_taken: Literal[False]


class DriftProofAgentProtocolResponse(
    RootModel[DriftProofNavigationResponse | DriftProofErrorResponse]
):
    """Complete one-object protocol for autonomous DriftProof callers."""
