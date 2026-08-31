from __future__ import annotations

import json
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    HUMAN_REVIEW = "human_review"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(StrEnum):
    VERIFIED = "verified"
    HYPOTHESIS = "hypothesis"


class FindingCategory(StrEnum):
    BEHAVIORAL_REGRESSION = "behavioral_regression"
    EDGE_CASE_FAILURE = "edge_case_failure"
    TEST_FAILURE = "test_failure"
    TEST_SKIP = "test_skip"
    UNVERIFIED_CLAIM = "unverified_claim"
    OUT_OF_SCOPE_CHANGE = "out_of_scope_change"
    DEPENDENCY_DRIFT = "dependency_drift"
    SECRET_EXPOSURE = "secret_exposure"
    PATH_TRAVERSAL = "path_traversal"
    FLAKY_BEHAVIOR = "flaky_behavior"
    UNSAFE_COMMAND = "unsafe_command"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_FAILURE = "provider_failure"
    OTHER = "other"


class CommandSpec(StrictModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    cwd: str = "."
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    repeat: int = Field(default=1, ge=1, le=10)
    expected_exit_codes: list[int] = Field(default_factory=lambda: [0], min_length=1, max_length=16)
    label: str = Field(default="verification", min_length=1, max_length=160)

    @field_validator("argv")
    @classmethod
    def nonempty_argv(cls, value: list[str]) -> list[str]:
        if any(not token or "\x00" in token or len(token) > 4_096 for token in value):
            raise ValueError("argv must contain bounded, non-empty tokens")
        return value

    @field_validator("cwd")
    @classmethod
    def safe_cwd(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value or "\\" in value or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe verification cwd: {value}")
        return value

    @field_validator("expected_exit_codes")
    @classmethod
    def unique_exit_codes(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("expected_exit_codes must be unique")
        if any(code < 0 or code > 255 for code in value):
            raise ValueError("expected exit codes must be between 0 and 255")
        return value


class CaseInput(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    title: str = Field(min_length=1, max_length=300)
    task: str = Field(min_length=1, max_length=100_000)
    before: dict[str, str]
    candidate: dict[str, str]
    trajectory: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)
    verification_commands: list[CommandSpec] = Field(default_factory=list, max_length=64)
    allowed_changed_globs: list[str] = Field(default_factory=lambda: ["**"], min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("before", "candidate")
    @classmethod
    def safe_relative_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:
            parsed = PurePosixPath(path)
            if (
                not path
                or "\x00" in path
                or "\\" in path
                or path.startswith("~")
                or parsed.is_absolute()
                or parsed in {PurePosixPath("."), PurePosixPath("..")}
                or ".." in parsed.parts
            ):
                raise ValueError(f"unsafe case path: {path}")
            if len(path) > 1_024:
                raise ValueError(f"case path is too long: {path[:80]}")
        if len(value) > 2_000:
            raise ValueError("a case may contain at most 2,000 files")
        return value

    @field_validator("allowed_changed_globs")
    @classmethod
    def safe_globs(cls, value: list[str]) -> list[str]:
        if len(value) > 256:
            raise ValueError("too many allowed changed-path globs")
        for item in value:
            parsed = PurePosixPath(item)
            if (
                not item
                or "\x00" in item
                or "\\" in item
                or item.startswith(("/", "~"))
                or parsed.is_absolute()
                or parsed in {PurePosixPath("."), PurePosixPath("..")}
                or ".." in parsed.parts
            ):
                raise ValueError(
                    "allowed changed-path globs must be non-empty POSIX-relative patterns without parent traversal"
                )
        return value

    @model_validator(mode="after")
    def bounded_payload(self) -> CaseInput:
        encoded_sizes = [
            len(content.encode("utf-8"))
            for tree in (self.before, self.candidate)
            for content in tree.values()
        ]
        if sum(encoded_sizes) > 10_000_000:
            raise ValueError("case text payload exceeds the 10 MB limit")
        if any(size > 1_000_000 for size in encoded_sizes):
            raise ValueError("an individual case file exceeds the 1 MB limit")
        auxiliary = json.dumps(
            {"trajectory": self.trajectory, "metadata": self.metadata},
            ensure_ascii=False,
            separators=(",", ":"),
            default=repr,
        ).encode("utf-8")
        if len(auxiliary) > 1_000_000:
            raise ValueError("trajectory and metadata exceed the 1 MB limit")
        return self


class GoldCase(StrictModel):
    id: str
    safe_to_merge: bool
    categories: list[FindingCategory] = Field(default_factory=list)
    rationale: str
    challenging: bool = False


class EvidenceRecord(StrictModel):
    id: str
    kind: str
    source: str
    sha256: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(StrictModel):
    category: FindingCategory
    severity: Severity
    title: str
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: FindingStatus = FindingStatus.VERIFIED


class ModelUsage(StrictModel):
    provider: str
    model: str
    agent: str
    request_hash: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    http_attempts: int = Field(default=1, ge=1)
    rate_limit_wait_ms: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = None


class ProviderResponse(StrictModel):
    data: dict[str, Any]
    raw_text: str
    usage: ModelUsage


class AgentTrace(StrictModel):
    schema_version: Literal[1] = 1
    agent: str
    provider: str
    model: str
    request_hash: str
    input_evidence_ids: list[str] = Field(default_factory=list)
    output_sha256: str
    accepted_output: dict[str, Any] = Field(default_factory=dict)
    gate_violations: list[str] = Field(default_factory=list)
    usage: ModelUsage


class Contract(StrictModel):
    requirements: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)


class AuditResult(StrictModel):
    schema_version: Literal[2] = 2
    case_id: str
    mode: str
    decision: Decision
    summary: str
    confidence: float = Field(ge=0, le=1)
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    valid_evidence_rate: float = Field(default=1.0, ge=0, le=1)
    gate_violations: list[str] = Field(default_factory=list)
    usage: list[ModelUsage] = Field(default_factory=list)
    contract: Contract | None = None
    agent_traces: list[AgentTrace] = Field(default_factory=list)
    consequential_action_taken: Literal[False] = False
    human_approval_required: Literal[True] = True
    duration_ms: int = 0
    provider: str
    model: str


class ReviewNavigationResponse(StrictModel):
    """Stable machine-facing navigation object emitted after a valid review."""

    schema_version: Literal[1]
    protocol: Literal["mergeproof.agent.v1"] = "mergeproof.agent.v1"
    status: Literal["valid_review"] = "valid_review"
    case_id: str
    decision: Decision
    confidence: float = Field(ge=0, le=1)
    exit_code: Literal[0, 10, 20]
    recommended_action: Literal[
        "human_approval",
        "repair_required",
        "evidence_or_human_escalation",
    ]
    bundle: str
    request: str
    manifest: str
    machine_result: str
    evidence_ledger: str
    agent_traces: str
    human_report: str
    human_report_markdown: str
    verified_findings: int = Field(ge=0)
    hypotheses: int = Field(ge=0)
    bundle_verified: Literal[True]
    bundle_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_file: str | None = None
    human_approval_required: Literal[True]
    consequential_action_taken: Literal[False]

    @model_validator(mode="after")
    def decision_fields_are_consistent(self) -> ReviewNavigationResponse:
        expected_exit = {
            Decision.APPROVE: 0,
            Decision.REJECT: 10,
            Decision.HUMAN_REVIEW: 20,
        }[self.decision]
        expected_action = {
            Decision.APPROVE: "human_approval",
            Decision.REJECT: "repair_required",
            Decision.HUMAN_REVIEW: "evidence_or_human_escalation",
        }[self.decision]
        if self.exit_code != expected_exit:
            raise ValueError("navigation exit code does not match the review decision")
        if self.recommended_action != expected_action:
            raise ValueError("recommended action does not match the review decision")
        return self


class ReviewErrorResponse(StrictModel):
    """Stable machine-facing object emitted when no review result is valid."""

    schema_version: Literal[1]
    protocol: Literal["mergeproof.agent.v1"] = "mergeproof.agent.v1"
    status: Literal["invalid_review"]
    decision: Literal["human_review"]
    exit_code: Literal[30]
    context: str
    error: str
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    detail: str
    hint: str
    retryable: bool
    response_file: str | None = None
    human_approval_required: Literal[True]
    consequential_action_taken: Literal[False]


class ReviewPreparationResponse(StrictModel):
    """Machine-facing response emitted after a request is prepared successfully."""

    schema_version: Literal[1]
    protocol: Literal["mergeproof.prepare.v1"] = "mergeproof.prepare.v1"
    status: Literal["request_prepared"] = "request_prepared"
    request: str
    case_id: str
    changed_paths: list[str]
    base_commit: str
    verification_commands: int = Field(ge=0)
    response_file: str | None = None
    human_approval_required: Literal[True]
    consequential_action_taken: Literal[False]


class AgentProtocolResponse(RootModel[ReviewNavigationResponse | ReviewErrorResponse]):
    """The complete one-object stdout protocol for autonomous review callers."""
