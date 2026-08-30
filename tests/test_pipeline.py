from __future__ import annotations

import json
import re

from pytest import MonkeyPatch

from mergeproof.collector import StaticAnalysis
from mergeproof.models import CaseInput, CommandSpec, Decision, FindingStatus
from mergeproof.pipeline import build_static_evidence, run_advanced, run_baseline
from mergeproof.providers import LLMProvider
from mergeproof.sandbox import VerificationAnalysis
from mergeproof.utils import canonical_json, sha256_text


class StaticProvider(LLMProvider):
    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(model="static-model")
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


class SequenceProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(model="sequence-model")
        self.calls = 0

    @property
    def name(self) -> str:
        return "sequence"

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        self.calls += 1
        if self.calls == 1:
            payload: dict[str, object] = {
                "requirements": ["Return two."],
                "invariants": ["Preserve an integer result."],
                "ambiguities": [],
                "acceptance_checks": ["Run bounded syntax verification."],
            }
        else:
            match = re.search(r'"id": "(task:[^"]+)"', user)
            assert match is not None
            payload = {
                "decision": "approve",
                "summary": "A material ambiguity remains.",
                "confidence": 0.7,
                "findings": [
                    {
                        "category": "other",
                        "severity": "high",
                        "title": "Material semantic ambiguity",
                        "explanation": "Executable syntax evidence does not establish the business meaning.",
                        "evidence_ids": [match.group(1)],
                    }
                ],
            }
        return json.dumps(payload), {
            "input_tokens": 12,
            "output_tokens": 6,
            "total_tokens": 18,
        }


class BrokenProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(model="broken-model")

    @property
    def name(self) -> str:
        return "broken"

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        raise ValueError("deliberate provider failure")


def sample_case() -> CaseInput:
    return CaseInput(
        id="sample",
        title="Sample",
        task="Return two and preserve an integer result.",
        before={"src/value.py": "def value():\n    return 1\n"},
        candidate={"src/value.py": "def value():\n    return 2\n"},
        trajectory=[{"role": "agent", "content": "Implemented."}],
        verification_commands=[CommandSpec(argv=["python", "-m", "py_compile", "src/value.py"])],
        allowed_changed_globs=["src/**"],
    )


def test_static_evidence_includes_policy_and_commands() -> None:
    evidence = build_static_evidence(sample_case())
    kinds = {item.kind for item in evidence}
    assert {"task", "diff", "trajectory", "policy", "commands", "file"} <= kinds
    assert len({item.id for item in evidence}) == len(evidence)


def test_unknown_evidence_reference_prevents_approval() -> None:
    provider = StaticProvider(
        {
            "decision": "approve",
            "summary": "Looks good.",
            "confidence": 0.9,
            "findings": [
                {
                    "category": "other",
                    "severity": "medium",
                    "title": "Invented citation",
                    "explanation": "This evidence does not exist.",
                    "evidence_ids": ["file:invented:deadbeef"],
                }
            ],
        }
    )
    result = run_baseline(sample_case(), provider)
    assert result.decision == Decision.HUMAN_REVIEW
    assert result.valid_evidence_rate == 0.0
    assert result.gate_violations
    assert result.findings[0].status == FindingStatus.HYPOTHESIS


def test_missing_evidence_reference_prevents_approval() -> None:
    provider = StaticProvider(
        {
            "decision": "approve",
            "summary": "Looks good.",
            "confidence": 0.9,
            "findings": [
                {
                    "category": "other",
                    "severity": "medium",
                    "title": "Uncited finding",
                    "explanation": "No evidence was supplied.",
                    "evidence_ids": [],
                }
            ],
        }
    )
    result = run_baseline(sample_case(), provider)
    assert result.decision == Decision.HUMAN_REVIEW
    assert "supplied no evidence IDs" in result.gate_violations[0]
    assert result.findings[0].status == FindingStatus.HYPOTHESIS


def test_valid_evidence_reference_is_admitted() -> None:
    case = sample_case()
    evidence_id = build_static_evidence(case)[0].id
    provider = StaticProvider(
        {
            "decision": "reject",
            "summary": "A supported blocker exists.",
            "confidence": 0.8,
            "findings": [
                {
                    "category": "insufficient_evidence",
                    "severity": "high",
                    "title": "Supported",
                    "explanation": "The finding cites supplied evidence.",
                    "evidence_ids": [evidence_id],
                }
            ],
        }
    )
    result = run_baseline(case, provider)
    assert result.decision == Decision.REJECT
    assert result.valid_evidence_rate == 1.0
    assert not result.gate_violations
    assert result.findings[0].status == FindingStatus.VERIFIED


def test_baseline_records_content_addressed_agent_trace() -> None:
    payload: dict[str, object] = {
        "decision": "approve",
        "summary": "Supported.",
        "confidence": 0.9,
        "findings": [],
    }
    result = run_baseline(sample_case(), StaticProvider(payload))
    assert len(result.agent_traces) == 1
    trace = result.agent_traces[0]
    assert trace.agent == "baseline_reviewer"
    assert trace.output_sha256 == sha256_text(canonical_json(payload))
    assert trace.input_evidence_ids == [item.id for item in result.evidence]


def test_material_critic_hypothesis_forces_human_review(monkeypatch: MonkeyPatch) -> None:
    case = sample_case()
    evidence = build_static_evidence(case)
    monkeypatch.setattr(
        "mergeproof.pipeline._deterministic_review",
        lambda _case: (
            evidence,
            [],
            StaticAnalysis(),
            VerificationAnalysis(),
        ),
    )
    result = run_advanced(case, SequenceProvider())

    assert result.decision == Decision.HUMAN_REVIEW
    assert result.contract is not None
    assert [trace.agent for trace in result.agent_traces] == [
        "contract_analyst",
        "skeptical_reviewer",
    ]
    assert result.findings[0].status == FindingStatus.HYPOTHESIS


def test_provider_failure_is_fail_closed() -> None:
    result = run_baseline(sample_case(), BrokenProvider())
    assert result.decision == Decision.HUMAN_REVIEW
    assert result.confidence == 0.0
    assert result.findings[0].category.value == "provider_failure"
