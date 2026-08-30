from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mergeproof.benchmark import run_benchmark
from mergeproof.providers import ReplayProvider
from mergeproof.utils import canonical_json, sha256_bytes, sha256_text, write_json

ROOT = Path(__file__).resolve().parents[1]
MODEL = "openai/gpt-oss-20b"
LIVE_DIR = ROOT / "results/baseline-live-groq-gpt-oss-20b"
REPLAY_DIR = ROOT / "results/baseline-replay-gpt-oss-20b"
FIXTURE_DIR = ROOT / "fixtures/replay/groq-gpt-oss-20b"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project schema-v1 and schema-v2 results onto the frozen baseline semantics."""
    normalized = dict(result)
    normalized.pop("duration_ms", None)
    normalized.pop("provider", None)
    normalized.pop("schema_version", None)
    normalized.pop("contract", None)
    normalized.pop("agent_traces", None)
    normalized.pop("human_approval_required", None)
    normalized.pop("consequential_action_taken", None)
    usage = []
    for item in normalized.get("usage", []):
        normalized_item = dict(item)
        normalized_item.pop("provider", None)
        normalized_item.pop("latency_ms", None)
        usage.append(normalized_item)
    normalized["usage"] = usage
    return normalized


def verify_schema_v2_envelope(result: dict[str, Any]) -> None:
    if result.get("schema_version") != 2:
        raise SystemExit(f"unexpected replay result schema for {result.get('case_id')}")
    if result.get("contract") is not None:
        raise SystemExit(
            f"baseline replay unexpectedly compiled a contract for {result['case_id']}"
        )
    if result.get("human_approval_required") is not True:
        raise SystemExit(f"human approval boundary weakened for {result['case_id']}")
    if result.get("consequential_action_taken") is not False:
        raise SystemExit(f"consequential action boundary weakened for {result['case_id']}")

    traces = result.get("agent_traces")
    if not isinstance(traces, list) or len(traces) != 1:
        raise SystemExit(f"expected one baseline agent trace for {result['case_id']}")
    trace = traces[0]
    usage = result.get("usage")
    evidence = result.get("evidence")
    if not isinstance(usage, list) or len(usage) != 1 or not isinstance(evidence, list):
        raise SystemExit(f"invalid trace inputs for {result['case_id']}")
    if trace.get("agent") != "baseline_reviewer":
        raise SystemExit(f"unexpected agent identity for {result['case_id']}")
    if trace.get("request_hash") != usage[0].get("request_hash"):
        raise SystemExit(f"trace request hash mismatch for {result['case_id']}")
    if trace.get("input_evidence_ids") != [item.get("id") for item in evidence]:
        raise SystemExit(f"trace evidence input mismatch for {result['case_id']}")
    accepted_output = trace.get("accepted_output")
    if not isinstance(accepted_output, dict):
        raise SystemExit(f"trace accepted output is not an object for {result['case_id']}")
    if trace.get("output_sha256") != sha256_text(canonical_json(accepted_output)):
        raise SystemExit(f"trace output hash mismatch for {result['case_id']}")


def comparable_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    category = metrics["issue_category_micro"]
    return {
        "cases": metrics["cases"],
        "primary_metric": metrics["primary_metric"],
        "unsafe_change_decision": metrics["unsafe_change_decision"],
        "safe_approval_precision": metrics["safe_approval_precision"],
        "issue_category_micro": {
            key: category[key] for key in ("tp", "fp", "fn", "precision", "recall", "f1")
        },
        "evidence_reference_validity": metrics["evidence_reference_validity"],
        "model_usage": metrics["model_usage"],
        "model": metrics["model"],
    }


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def main() -> None:
    fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
    if len(fixture_paths) != 24:
        raise SystemExit(f"expected 24 replay fixtures, found {len(fixture_paths)}")

    provider = ReplayProvider(model=MODEL, replay_dir=FIXTURE_DIR)
    _, replay_metrics = run_benchmark(
        mode="baseline",
        provider=provider,
        cases_path=ROOT / "benchmark/cases.json",
        gold_path=ROOT / "benchmark/gold.json",
        output_dir=REPLAY_DIR,
    )

    live_results = load_jsonl(LIVE_DIR / "raw-results.jsonl")
    replay_results = load_jsonl(REPLAY_DIR / "raw-results.jsonl")
    for result in replay_results:
        verify_schema_v2_envelope(result)
    if [normalize_result(item) for item in live_results] != [
        normalize_result(item) for item in replay_results
    ]:
        raise SystemExit("live and replay semantic results differ")

    live_metrics = json.loads((LIVE_DIR / "metrics.json").read_text(encoding="utf-8"))
    if comparable_metrics(live_metrics) != comparable_metrics(replay_metrics):
        raise SystemExit("live and replay comparable metrics differ")

    verification = {
        "schema_version": 1,
        "verified": True,
        "model": MODEL,
        "case_count": len(live_results),
        "fixture_count": len(fixture_paths),
        "live_raw_results_sha256": file_sha256(LIVE_DIR / "raw-results.jsonl"),
        "replay_raw_results_sha256": file_sha256(REPLAY_DIR / "raw-results.jsonl"),
        "live_metrics_sha256": file_sha256(LIVE_DIR / "metrics.json"),
        "replay_metrics_sha256": file_sha256(REPLAY_DIR / "metrics.json"),
        "fixture_directory_sha256": sha256_bytes(
            "".join(f"{file_sha256(path)}  {path.name}\n" for path in fixture_paths).encode()
        ),
        "cases_sha256": file_sha256(ROOT / "benchmark/cases.json"),
        "gold_sha256": file_sha256(ROOT / "benchmark/gold.json"),
        "pyproject_sha256": file_sha256(ROOT / "pyproject.toml"),
        "uv_lock_sha256": file_sha256(ROOT / "uv.lock"),
        "semantic_comparison": "Frozen schema-v1 decision semantics are identical; the schema-v2 human-boundary and trace envelope is independently validated.",
        "scope": "Replay verifies deterministic processing of recorded model responses. It is not an unseen-input or model-generalization test.",
    }
    write_json(REPLAY_DIR / "replay-verification.json", verification)
    print(json.dumps(verification, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
