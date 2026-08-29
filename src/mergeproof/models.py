from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    argv: list[str]
    cwd: str = "."
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    repeat: int = Field(default=1, ge=1, le=10)
    expected_exit_codes: list[int] = Field(default_factory=lambda: [0])
    label: str = "verification"

    @field_validator("argv")
    @classmethod
    def nonempty_argv(cls, value: list[str]) -> list[str]:
        if not value or any(not token for token in value):
            raise ValueError("argv must contain non-empty tokens")
        return value


class CaseInput(StrictModel):
    id: str
    title: str
    task: str
    before: dict[str, str]
    candidate: dict[str, str]
    trajectory: list[dict[str, Any]] = Field(default_factory=list)
    verification_commands: list[CommandSpec] = Field(default_factory=list)
    allowed_changed_globs: list[str] = Field(default_factory=lambda: ["**"])
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("before", "candidate")
    @classmethod
    def safe_relative_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:
            if path.startswith(("/", "~")) or ".." in path.split("/"):
                raise ValueError(f"unsafe case path: {path}")
        return value


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


class AuditResult(StrictModel):
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
    duration_ms: int = 0
    provider: str
    model: str


class Contract(StrictModel):
    requirements: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
