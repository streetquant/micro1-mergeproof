from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mergeproof.utils import pretty_json


class JudgePacketError(RuntimeError):
    """Raised when judge-facing evidence is missing, inconsistent, or unsafe."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise JudgePacketError(f"required judge-packet source is missing or unsafe: {relative}")
    return path


def _load_object(root: Path, relative: str) -> dict[str, Any]:
    path = _regular_file(root, relative)
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JudgePacketError(f"invalid JSON judge-packet source: {relative}") from exc
    if not isinstance(value, dict):
        raise JudgePacketError(f"judge-packet source must be a JSON object: {relative}")
    return value


def _load_jsonl(root: Path, relative: str) -> list[dict[str, Any]]:
    path = _regular_file(root, relative)
    rows: list[dict[str, Any]] = []
    for index, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="strict").splitlines(), 1
    ):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise JudgePacketError(f"invalid JSONL at {relative}:{index}") from exc
        if not isinstance(value, dict):
            raise JudgePacketError(f"JSONL row must be an object at {relative}:{index}")
        rows.append(value)
    if not rows:
        raise JudgePacketError(f"judge-packet JSONL source is empty: {relative}")
    return rows


def _source_record(root: Path, relative: str, purpose: str) -> dict[str, object]:
    path = _regular_file(root, relative)
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "purpose": purpose,
    }


def _trajectory_from_evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = row.get("evidence")
    if not isinstance(evidence, list):
        raise JudgePacketError("canonical baseline row lacks evidence")
    trajectory_records = [
        item for item in evidence if isinstance(item, dict) and item.get("kind") == "trajectory"
    ]
    if len(trajectory_records) != 1:
        raise JudgePacketError("canonical baseline row must contain one submitted trajectory")
    content = trajectory_records[0].get("content")
    if not isinstance(content, str):
        raise JudgePacketError("submitted trajectory content must be a JSON string")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise JudgePacketError("submitted trajectory content is invalid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise JudgePacketError("submitted trajectory must be a list of event objects")
    return value


def _baseline_trace(
    root: Path,
    row: dict[str, Any],
    *,
    source_row: int,
) -> dict[str, Any]:
    raw_traces = row.get("agent_traces")
    if not isinstance(raw_traces, list) or len(raw_traces) != 1:
        raise JudgePacketError("canonical baseline row must contain exactly one agent trace")
    trace = raw_traces[0]
    if not isinstance(trace, dict) or trace.get("agent") != "baseline_reviewer":
        raise JudgePacketError("canonical baseline trace has an unexpected agent identity")
    request_hash = trace.get("request_hash")
    if not isinstance(request_hash, str) or len(request_hash) != 64:
        raise JudgePacketError("canonical baseline trace has an invalid request hash")
    fixture_relative = f"fixtures/replay/groq-gpt-oss-20b/{request_hash}.json"
    fixture = _load_object(root, fixture_relative)
    if fixture.get("agent") != "baseline_reviewer" or fixture.get("request_hash") != request_hash:
        raise JudgePacketError("baseline replay fixture identity differs from the canonical result")
    request = fixture.get("request")
    response = fixture.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise JudgePacketError("baseline replay fixture lacks request or response evidence")
    raw_result_path = _regular_file(
        root, "results/baseline-live-groq-gpt-oss-20b/raw-results.jsonl"
    )
    raw_line = raw_result_path.read_text(encoding="utf-8", errors="strict").splitlines()[
        source_row - 1
    ]
    return {
        "agent": "baseline_reviewer",
        "role": "frozen one-shot comparison baseline",
        "case_id": row.get("case_id"),
        "decision": row.get("decision"),
        "request_hash": request_hash,
        "provider": trace.get("provider"),
        "model": trace.get("model"),
        "instructions": {
            "capture_scope": "recorded bounded previews plus full prompt hashes",
            "system_preview": request.get("system_preview"),
            "system_sha256": request.get("system_sha256"),
            "user_preview": request.get("user_preview"),
            "user_sha256": request.get("user_sha256"),
        },
        "submitted_upstream_trajectory": _trajectory_from_evidence(row),
        "agent_tool_calls": [],
        "agent_tool_boundary": (
            "The baseline reviewer had no executable tools; submitted repair-agent tool claims were "
            "untrusted input evidence."
        ),
        "model_response": response,
        "accepted_output": trace.get("accepted_output"),
        "verifier_feedback": {
            "decision": row.get("decision"),
            "summary": row.get("summary"),
            "findings": row.get("findings"),
            "gate_violations": row.get("gate_violations"),
            "valid_evidence_rate": row.get("valid_evidence_rate"),
        },
        "retry_evidence": trace.get("usage"),
        "human_checkpoint": (
            "The submitted trajectory and result both retain a qualified-human checkpoint; the "
            "baseline never performs a merge or deployment."
        ),
        "source": {
            "result": "results/baseline-live-groq-gpt-oss-20b/raw-results.jsonl",
            "row": source_row,
            "row_sha256": _sha256_bytes((raw_line + "\n").encode("utf-8")),
            "fixture": fixture_relative,
            "fixture_sha256": _sha256_file(root / fixture_relative),
        },
    }


def _baseline_coverage(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relative = "results/baseline-live-groq-gpt-oss-20b/raw-results.jsonl"
    rows = _load_jsonl(root, relative)
    if len(rows) != 24:
        raise JudgePacketError(
            f"canonical baseline trace set must contain 24 rows, observed {len(rows)}"
        )
    coverage: list[dict[str, Any]] = []
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, row in enumerate(rows, 1):
        traces = row.get("agent_traces")
        if not isinstance(traces, list) or len(traces) != 1 or not isinstance(traces[0], dict):
            raise JudgePacketError(f"baseline row {index} lacks exactly one agent trace")
        trace = traces[0]
        if trace.get("agent") != "baseline_reviewer":
            raise JudgePacketError(f"baseline row {index} uses an unexpected agent")
        request_hash = trace.get("request_hash")
        decision = row.get("decision")
        case_id = row.get("case_id")
        if not isinstance(request_hash, str) or not isinstance(case_id, str):
            raise JudgePacketError(f"baseline row {index} lacks stable identities")
        if decision not in {"approve", "reject", "human_review"}:
            raise JudgePacketError(f"baseline row {index} has an unsupported decision")
        fixture = f"fixtures/replay/groq-gpt-oss-20b/{request_hash}.json"
        coverage.append(
            {
                "case_id": case_id,
                "decision": decision,
                "request_hash": request_hash,
                "fixture": fixture,
                "fixture_sha256": _sha256_file(_regular_file(root, fixture)),
            }
        )
        if decision in {"approve", "reject"} and decision not in selected:
            selected[decision] = (index, row)
    if set(selected) != {"approve", "reject"}:
        raise JudgePacketError(
            "baseline representative traces require one approval and one rejection"
        )
    representatives = [
        _baseline_trace(root, selected[decision][1], source_row=selected[decision][0])
        for decision in ("approve", "reject")
    ]
    return coverage, representatives


def _contract_clarifier_trace(root: Path) -> dict[str, Any]:
    live_relative = "results/agent-fallback-live/gate-report.json"
    replay_relative = "results/agent-fallback-replay/gate-report.json"
    live = _load_object(root, live_relative)
    replay = _load_object(root, replay_relative)
    live_trace = live.get("agent_trace")
    replay_trace = replay.get("agent_trace")
    if not isinstance(live_trace, dict) or not isinstance(replay_trace, dict):
        raise JudgePacketError("contract clarifier live/replay reports lack agent traces")
    if live_trace.get("agent") != "contract_clarifier":
        raise JudgePacketError("contract clarifier live trace has the wrong identity")
    request_hash = live_trace.get("request_hash")
    if request_hash != replay_trace.get("request_hash") or not isinstance(request_hash, str):
        raise JudgePacketError("contract clarifier live/replay request identities differ")
    fixture_relative = f"fixtures/agent/driftproof-contract-clarifier/{request_hash}.json"
    fixture = _load_object(root, fixture_relative)
    if fixture.get("agent") != "contract_clarifier" or fixture.get("request_hash") != request_hash:
        raise JudgePacketError("contract clarifier fixture identity differs from the gate report")
    request = fixture.get("request")
    response = fixture.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise JudgePacketError("contract clarifier fixture lacks request or response evidence")
    checks = live.get("checks")
    if not isinstance(checks, list):
        raise JudgePacketError("contract clarifier gate report lacks verifier checks")
    build = live.get("build")
    if not isinstance(build, dict):
        raise JudgePacketError("contract clarifier gate report lacks deterministic build evidence")
    return {
        "agent": "contract_clarifier",
        "role": "bounded optional translation of unresolved visible context into admitted typed rules",
        "request_hash": request_hash,
        "provider": live_trace.get("provider"),
        "model": live_trace.get("model"),
        "instructions": {
            "capture_scope": "recorded bounded previews plus full prompt hashes",
            "system_preview": request.get("system_preview"),
            "system_sha256": request.get("system_sha256"),
            "user_preview": request.get("user_preview"),
            "user_sha256": request.get("user_sha256"),
        },
        "agent_tool_calls": [],
        "agent_tool_boundary": (
            "The clarifier cannot run code or approve a candidate. Deterministic dbt execution and "
            "rule admission remain outside the model agent."
        ),
        "model_response": response,
        "live_agent_trace": live_trace,
        "replay_agent_trace": replay_trace,
        "deterministic_tool_response": {
            "command": build.get("command"),
            "returncode": build.get("returncode"),
            "passed": build.get("passed"),
            "isolation": build.get("isolation"),
            "worktree_sha256": build.get("worktree_sha256"),
        },
        "verifier_feedback": {
            "verdict": live.get("verdict"),
            "summary": live.get("summary"),
            "failed_check_ids": live.get("failed_check_ids"),
            "inconclusive_check_ids": live.get("inconclusive_check_ids"),
            "checks": checks,
        },
        "retry_evidence": response.get("usage"),
        "human_checkpoint": (
            "Unresolved statements force human_review; the clarifier cannot authorize merge or deployment."
        ),
        "source": {
            "live_gate_report": live_relative,
            "live_gate_report_sha256": _sha256_file(root / live_relative),
            "replay_gate_report": replay_relative,
            "replay_gate_report_sha256": _sha256_file(root / replay_relative),
            "fixture": fixture_relative,
            "fixture_sha256": _sha256_file(root / fixture_relative),
        },
    }


def _input_trajectory_index(root: Path) -> dict[str, Any]:
    relative = "benchmark_dbt/cases.json"
    payload = _load_object(root, relative)
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        raise JudgePacketError("dbt benchmark must expose 24 input trajectories")
    records: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("trajectory"), dict):
            raise JudgePacketError("dbt benchmark case lacks a trajectory object")
        candidate_id = case.get("candidate_id")
        trajectory = case["trajectory"]
        if not isinstance(candidate_id, str):
            raise JudgePacketError("dbt benchmark case lacks a candidate identity")
        records.append(
            {
                "candidate_id": candidate_id,
                "producer": trajectory.get("producer"),
                "trajectory_sha256": _sha256_bytes(
                    (pretty_json(trajectory) + "\n").encode("utf-8")
                ),
                "human_checkpoint": trajectory.get("human_checkpoint"),
            }
        )
    return {
        "classification": "untrusted input trajectories, not workflow agents",
        "count": len(records),
        "source": relative,
        "source_sha256": _sha256_file(root / relative),
        "records": records,
    }


def build_agent_trajectories(root: Path) -> dict[str, Any]:
    root = root.resolve()
    coverage, representatives = _baseline_coverage(root)
    clarifier = _contract_clarifier_trace(root)
    observed_agents = sorted({"baseline_reviewer", str(clarifier["agent"])})
    declared_agents = ["baseline_reviewer", "contract_clarifier"]
    if observed_agents != declared_agents:
        raise JudgePacketError(
            f"declared and observed workflow agents differ: {declared_agents} != {observed_agents}"
        )
    return {
        "schema_version": 1,
        "protocol": "driftproof.agent-trajectories.v1",
        "coverage_complete": True,
        "declared_workflow_agents": declared_agents,
        "observed_workflow_agents": observed_agents,
        "agents": {
            "baseline_reviewer": {
                "mode": "one-shot baseline with no executable tools",
                "canonical_case_count": len(coverage),
                "canonical_trace_index": coverage,
                "representative_traces": representatives,
            },
            "contract_clarifier": {
                "mode": "optional bounded agent inside the advanced workflow",
                "canonical_case_count": 1,
                "representative_traces": [clarifier],
            },
        },
        "input_trajectories": _input_trajectory_index(root),
        "limitations": [
            "Provider records retain bounded prompt previews and full prompt hashes, not unrestricted full prompts.",
            "The baseline reviewer and contract clarifier have no executable tools; deterministic tool results are recorded separately.",
            "Synthetic repair-agent trajectories are untrusted benchmark inputs and are not counted as DriftProof workflow agents.",
        ],
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def build_trace_index(root: Path, trajectories_text: str) -> dict[str, Any]:
    root = root.resolve()
    sources = [
        _source_record(
            root,
            "results/baseline-live-groq-gpt-oss-20b/raw-results.jsonl",
            "24 canonical baseline reviewer traces",
        ),
        _source_record(
            root,
            "results/baseline-replay-gpt-oss-20b/replay-verification.json",
            "offline semantic replay verification",
        ),
        _source_record(
            root,
            "results/agent-fallback-live/gate-report.json",
            "live contract clarifier trace plus deterministic verifier feedback",
        ),
        _source_record(
            root,
            "results/agent-fallback-replay/gate-report.json",
            "replayed contract clarifier trace",
        ),
        _source_record(
            root,
            "benchmark_dbt/cases.json",
            "24 submitted synthetic input trajectories",
        ),
    ]
    fixture_paths = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "fixtures/replay/groq-gpt-oss-20b").glob("*.json")
        if path.is_file() and not path.is_symlink()
    )
    if len(fixture_paths) != 24:
        raise JudgePacketError(
            f"baseline replay fixture coverage must contain 24 files, observed {len(fixture_paths)}"
        )
    fixture_records = [
        _source_record(root, relative, "content-addressed baseline provider exchange")
        for relative in fixture_paths
    ]
    return {
        "schema_version": 1,
        "protocol": "driftproof.trace-index.v1",
        "coverage_complete": True,
        "workflow_agents": ["baseline_reviewer", "contract_clarifier"],
        "representative_packet": {
            "path": "submission/AGENT_TRAJECTORIES.json",
            "bytes": len(trajectories_text.encode("utf-8")),
            "sha256": _sha256_bytes(trajectories_text.encode("utf-8")),
        },
        "canonical_sources": sources,
        "baseline_provider_fixtures": fixture_records,
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def _evidence(
    root: Path,
    relative: str,
    scope: str,
) -> dict[str, object]:
    record = _source_record(root, relative, scope)
    return {
        "path": record["path"],
        "bytes": record["bytes"],
        "sha256": record["sha256"],
        "scope": scope,
    }


def build_claim_ledger(
    root: Path,
    metrics: dict[str, Any],
    *,
    trajectories_text: str,
    trace_index_text: str,
) -> dict[str, Any]:
    root = root.resolve()
    generated_trace_evidence = [
        {
            "path": "submission/AGENT_TRAJECTORIES.json",
            "bytes": len(trajectories_text.encode("utf-8")),
            "sha256": _sha256_bytes(trajectories_text.encode("utf-8")),
            "scope": "representative instructions, responses, deterministic feedback, retries, and human checkpoints for every workflow agent",
        },
        {
            "path": "submission/TRACE_INDEX.json",
            "bytes": len(trace_index_text.encode("utf-8")),
            "sha256": _sha256_bytes(trace_index_text.encode("utf-8")),
            "scope": "complete content-addressed trace inventory",
        },
    ]
    claims: list[dict[str, Any]] = [
        {
            "id": "DP-PROBLEM-001",
            "claim": "Agent-authored patches can build green while violating a visible business contract, leaving software leads with slow, fragmented release decisions.",
            "status": "supported",
            "evidence": [
                _evidence(
                    root, "oracle/problem-brief.md", "frozen user, bottleneck, and non-goals"
                ),
                _evidence(
                    root,
                    "docs/requirements.md",
                    "pre-implementation requirements and acceptance gates",
                ),
            ],
            "limitations": [
                "The chosen domain is dbt repair review, not every software-change domain."
            ],
        },
        {
            "id": "DP-ENGINEERING-001",
            "claim": "DriftProof executes untrusted dbt candidates in a networkless bubblewrap worktree, preserves the original source, compiles visible contracts, fails closed, and publishes hash-bound evidence for a human checkpoint.",
            "status": "supported",
            "evidence": [
                _evidence(root, "docs/architecture.md", "architecture and trust boundary"),
                _evidence(root, "src/driftproof/runner.py", "sandboxed execution implementation"),
                _evidence(
                    root,
                    "src/driftproof/reporting.py",
                    "atomic bundle publication and verification",
                ),
                _evidence(
                    root,
                    "reviews/2026-08-31-round-6-response-binding/qualification.json",
                    "response-to-bundle authenticity qualification",
                ),
            ],
            "limitations": [
                "The safe execution profile currently requires Linux and working rootless bubblewrap."
            ],
        },
        {
            "id": "DP-DEMO-001",
            "claim": "The installed credential-free demonstration makes both transparent fixtures pass build-only review, then approves the contract-preserving repair and rejects the green-but-wrong repair.",
            "status": "supported",
            "evidence": [
                _evidence(
                    root,
                    "reviews/2026-08-31-round-5-installed-demo/qualification.json",
                    "installed-wheel demonstration qualification",
                ),
                _evidence(
                    root, "src/driftproof/demo.py", "package-native demonstration implementation"
                ),
            ],
            "limitations": ["The two-case demonstration is not the scored 24-case benchmark."],
        },
        {
            "id": "DP-METRIC-001",
            "claim": (
                "On the frozen 24-case benchmark, unsafe-repair escape rate fell from "
                f"{metrics['baseline_unsafe_escape_rate']:.3f} to {metrics['advanced_unsafe_escape_rate']:.3f}; "
                f"safe-approval macro-F1 rose from {metrics['baseline_macro_f1']:.3f} to {metrics['advanced_macro_f1']:.3f}; "
                f"{metrics['advanced_safe_approved']} of {metrics['safe_total']} safe cases were auto-approved and "
                f"{metrics['advanced_human_reviews']} cases escalated."
            ),
            "status": "supported",
            "evidence": [
                _evidence(
                    root,
                    "results/driftproof-comparison/comparison.json",
                    "authoritative metric comparison and fairness disclosure",
                ),
                _evidence(root, "benchmark_dbt/manifest.json", "frozen benchmark identities"),
                _evidence(
                    root,
                    "results/driftproof-benchmark-validation/summary.json",
                    "external-oracle validation of all dbt cases",
                ),
            ],
            "limitations": [
                "The benchmark is balanced, synthetic, and project-authored.",
                "Zero measured unsafe escapes does not establish universal correctness or formal verification.",
            ],
        },
        {
            "id": "DP-REPRO-001",
            "claim": "A clean environment can reproduce the benchmark, replay recorded model responses, rebuild packages, and verify the committed comparison without an API key.",
            "status": "supported",
            "evidence": [
                _evidence(root, "scripts/reproduce.sh", "complete reproduction entry point"),
                _evidence(
                    root,
                    "results/baseline-replay-gpt-oss-20b/replay-verification.json",
                    "non-mutating offline replay receipt",
                ),
                _evidence(root, "CHANGELOG.md", "kept, removed, and failed experiments"),
            ],
            "limitations": [
                "Replay proves processing of recorded responses, not unseen-input model generalization."
            ],
        },
        {
            "id": "DP-TRACES-001",
            "claim": "Representative, content-addressed trajectories are supplied for every workflow agent: baseline_reviewer and contract_clarifier.",
            "status": "supported",
            "evidence": generated_trace_evidence,
            "limitations": [
                "Prompt records contain bounded previews plus full prompt hashes rather than unrestricted full prompts."
            ],
        },
        {
            "id": "DP-ATTRIBUTION-001",
            "claim": "DriftDoctor is pinned and credited as prior MIT-licensed work; DriftProof claims only its independent release-gate contribution.",
            "status": "supported",
            "evidence": [
                _evidence(root, "docs/driftdoctor-upstream.md", "explicit prior-work boundary"),
                _evidence(root, "upstream/driftdoctor.lock.json", "pinned upstream identity"),
                _evidence(root, "upstream/DriftDoctor-LICENSE", "upstream license"),
            ],
            "limitations": [],
        },
        {
            "id": "DP-SAFETY-001",
            "claim": "Every result remains advisory: human approval is required and no merge, deployment, publication, notification, or deletion is performed by DriftProof.",
            "status": "supported",
            "evidence": [
                _evidence(
                    root,
                    "schemas/driftproof/agent-response.schema.json",
                    "runtime-derived fixed response boundary",
                ),
                _evidence(
                    root,
                    "schemas/driftproof/response-verification.schema.json",
                    "independent response verification boundary",
                ),
            ],
            "limitations": ["External orchestrators must preserve the human checkpoint."],
        },
    ]
    return {
        "schema_version": 1,
        "protocol": "driftproof.claim-ledger.v1",
        "claim_count": len(claims),
        "claims": claims,
        "all_claims_supported": all(claim["status"] == "supported" for claim in claims),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def build_rubric_map(root: Path, claim_ledger_text: str) -> dict[str, Any]:
    root = root.resolve()
    criteria: list[dict[str, Any]] = [
        {
            "criterion": "Problem & User Value",
            "points": 15,
            "claim_ids": ["DP-PROBLEM-001"],
            "judge_actions": [
                {"kind": "read", "path": "submission/START_HERE.md"},
                {"kind": "read", "path": "oracle/problem-brief.md"},
            ],
        },
        {
            "criterion": "Agent Solution & Engineering",
            "points": 30,
            "claim_ids": ["DP-ENGINEERING-001", "DP-TRACES-001", "DP-SAFETY-001"],
            "judge_actions": [
                {"kind": "command", "argv": ["driftproof", "capabilities"]},
                {"kind": "command", "argv": ["driftproof", "schema", "agent-response"]},
                {"kind": "read", "path": "submission/AGENT_TRAJECTORIES.json"},
            ],
        },
        {
            "criterion": "End to End Quality",
            "points": 20,
            "claim_ids": ["DP-DEMO-001", "DP-ENGINEERING-001"],
            "judge_actions": [
                {"kind": "command", "argv": ["driftproof", "demo", "--json"]},
                {"kind": "command", "argv": ["driftproof", "verify-report", "<bundle-from-demo>"]},
            ],
        },
        {
            "criterion": "Measured Improvement",
            "points": 15,
            "claim_ids": ["DP-METRIC-001"],
            "judge_actions": [
                {"kind": "read", "path": "results/driftproof-comparison/comparison.json"},
                {"kind": "read", "path": "submission/CLAIM_LEDGER.json"},
            ],
        },
        {
            "criterion": "Reproducibility",
            "points": 15,
            "claim_ids": ["DP-REPRO-001", "DP-ATTRIBUTION-001"],
            "judge_actions": [
                {"kind": "command", "argv": ["make", "check"]},
                {"kind": "command", "argv": ["bash", "scripts/reproduce.sh"]},
            ],
        },
        {
            "criterion": "Hot Take / Insights",
            "points": 5,
            "claim_ids": ["DP-METRIC-001", "DP-SAFETY-001"],
            "judge_actions": [{"kind": "read", "path": "CHANGELOG.md"}],
            "insight": (
                "A green build is evidence about executability, not business correctness; a useful "
                "agentic release gate should optimize unsafe escapes first and make uncertainty visible "
                "instead of hiding it behind an approval rate."
            ),
        },
    ]
    total = sum(int(item["points"]) for item in criteria)
    if total != 100:
        raise JudgePacketError(f"rubric map must total 100 points, observed {total}")
    return {
        "schema_version": 1,
        "protocol": "driftproof.rubric-map.v1",
        "total_points": total,
        "criteria": criteria,
        "claim_ledger": {
            "path": "submission/CLAIM_LEDGER.json",
            "bytes": len(claim_ledger_text.encode("utf-8")),
            "sha256": _sha256_bytes(claim_ledger_text.encode("utf-8")),
        },
        "source": _source_record(root, "oracle/problem-brief.md", "frozen challenge framing"),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def build_judge_checklist(metrics: dict[str, Any]) -> str:
    return f"""# DriftProof judge checklist

This is the shortest evidence-first evaluation path. No command below merges or deploys code.

## 1. Understand the problem and measured trade-off

- Open [`START_HERE.md`](START_HERE.md).
- Check [`CLAIM_LEDGER.json`](CLAIM_LEDGER.json) before relying on a headline claim.
- Confirm the frozen result: macro-F1 `{metrics["baseline_macro_f1"]:.3f}` → `{metrics["advanced_macro_f1"]:.3f}`, accuracy `{100 * metrics["baseline_accuracy"]:.1f}%` → `{100 * metrics["advanced_accuracy"]:.1f}%`, unsafe escapes `{100 * metrics["baseline_unsafe_escape_rate"]:.1f}%` → `{100 * metrics["advanced_unsafe_escape_rate"]:.1f}%`.
- Note the conservative cost: only `{metrics["advanced_safe_approved"]}/{metrics["safe_total"]}` safe candidates were auto-approved and `{metrics["advanced_human_reviews"]}/{metrics["cases"]}` cases escalated.

## 2. Run the installed end-to-end demonstration

```bash
uv sync --locked --extra dbt
uv run driftproof demo
```

Both fixtures must build green. DriftProof must approve the contract-preserving fixture and reject the green-but-wrong fixture. Open both printed HTML reports.

## 3. Watch and authenticate the exact-source solution video

- Open `driftproof-demo.mp4` from the downloaded release.
- Use `driftproof-demo-transcript.md` for searchable narration and accessibility.
- Confirm `driftproof-demo-verification.json` reports a complete decode, one 1920x1080 H.264 stream, one AAC 48 kHz stream, audible narration, and a duration below five minutes.
- Confirm the video receipt and `driftproof-demo-source-manifest.json` name the same source commit as `release-manifest.json`.

## 4. Inspect every workflow-agent trace

- Open [`AGENT_TRAJECTORIES.json`](AGENT_TRAJECTORIES.json).
- Confirm coverage for `baseline_reviewer` and `contract_clarifier`.
- Confirm instructions, response, retry/usage evidence, deterministic verifier feedback, and human checkpoints are present.
- Use [`TRACE_INDEX.json`](TRACE_INDEX.json) to verify the canonical source hashes.

## 5. Map evidence to the scoring rubric

- Open [`RUBRIC_MAP.json`](RUBRIC_MAP.json).
- Confirm its six criteria total 100 points.
- Follow the argument vectors; do not paste untrusted fields into a shell.

## 6. Verify a downloaded release independently

From the downloaded release directory:

```bash
python verify-release.pyz .
```

This requires only Python's standard library and Git. It validates the exact file set, SHA-256 records, all archives and CRCs, judge-packet cross-bindings, verifier-source identity, and the embedded Git bundle. Exit `30` means the delivery is invalid and no result should be trusted.

## 7. Run qualification when deeper verification is needed

```bash
make check
bash scripts/reproduce.sh
```

`make check` covers formatting, linting, strict typing, schema drift, submission drift, protocol smoke tests, the complete test suite, frozen replay, and package construction. The full reproduction additionally regenerates and externally validates the benchmark.

## 8. Preserve the authority boundary

- `human_approval_required` must remain `true`.
- `consequential_action_taken` must remain `false`.
- An approval is decision support, never automatic authorization to merge or deploy.

## Scope limitations

- The benchmark is balanced, synthetic, and project-authored.
- The two-case demo is not the 24-case benchmark.
- Replay does not establish unseen-input generalization.
- Safe execution currently requires Linux and rootless bubblewrap.
"""


def build_judge_artifacts(root: Path, metrics: dict[str, Any]) -> dict[str, str]:
    trajectories = pretty_json(build_agent_trajectories(root)) + "\n"
    trace_index = pretty_json(build_trace_index(root, trajectories)) + "\n"
    claim_ledger = (
        pretty_json(
            build_claim_ledger(
                root,
                metrics,
                trajectories_text=trajectories,
                trace_index_text=trace_index,
            )
        )
        + "\n"
    )
    rubric_map = pretty_json(build_rubric_map(root, claim_ledger)) + "\n"
    checklist = build_judge_checklist(metrics)
    return {
        "AGENT_TRAJECTORIES.json": trajectories,
        "TRACE_INDEX.json": trace_index,
        "CLAIM_LEDGER.json": claim_ledger,
        "RUBRIC_MAP.json": rubric_map,
        "JUDGE_CHECKLIST.md": checklist,
    }
