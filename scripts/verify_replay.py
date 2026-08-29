from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mergeproof.benchmark import run_benchmark
from mergeproof.providers import ReplayProvider
from mergeproof.utils import sha256_bytes, write_json

ROOT = Path(__file__).resolve().parents[1]
MODEL = "openai/gpt-oss-20b"
LIVE_DIR = ROOT / "results/baseline-live-groq-gpt-oss-20b"
REPLAY_DIR = ROOT / "results/baseline-replay-gpt-oss-20b"
FIXTURE_DIR = ROOT / "fixtures/replay/groq-gpt-oss-20b"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized.pop("duration_ms", None)
    normalized.pop("provider", None)
    usage = []
    for item in normalized.get("usage", []):
        normalized_item = dict(item)
        normalized_item.pop("provider", None)
        normalized_item.pop("latency_ms", None)
        usage.append(normalized_item)
    normalized["usage"] = usage
    return normalized


def comparable_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "cases": metrics["cases"],
        "primary_metric": metrics["primary_metric"],
        "unsafe_change_decision": metrics["unsafe_change_decision"],
        "safe_approval_precision": metrics["safe_approval_precision"],
        "issue_category_micro": metrics["issue_category_micro"],
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
        "semantic_comparison": "All fields except provider identity and measured runtime/latency are identical.",
    }
    write_json(REPLAY_DIR / "replay-verification.json", verification)
    print(json.dumps(verification, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
