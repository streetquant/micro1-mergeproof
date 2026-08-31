from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from mergeproof.benchmark import run_benchmark
from mergeproof.providers import ReplayProvider
from mergeproof.utils import canonical_json, sha256_bytes, sha256_text

ROOT = Path(__file__).resolve().parents[1]
MODEL = "openai/gpt-oss-20b"
LIVE_DIR = ROOT / "results/baseline-live-groq-gpt-oss-20b"
CANONICAL_REPLAY_DIR = ROOT / "results/baseline-replay-gpt-oss-20b"
FIXTURE_DIR = ROOT / "fixtures/replay/groq-gpt-oss-20b"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"expected a JSON object: {path}")
    return payload


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    results = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if any(not isinstance(item, dict) for item in results):
        raise SystemExit(f"expected JSON objects in {path}")
    return results


def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project live, frozen, and fresh replay results onto decision semantics."""

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


def complete_request_hashes(results: list[dict[str, Any]], *, label: str) -> set[str]:
    if len(results) != 24:
        raise SystemExit(f"expected 24 {label} results, found {len(results)}")
    request_hashes: list[str] = []
    for result in results:
        verify_schema_v2_envelope(result)
        if result.get("gate_violations"):
            raise SystemExit(f"{label} gate violations for {result['case_id']}")
        if any(
            finding.get("category") == "provider_failure"
            for finding in result.get("findings", [])
            if isinstance(finding, dict)
        ):
            raise SystemExit(f"{label} provider failure for {result['case_id']}")
        usage = result["usage"]
        request_hash = usage[0].get("request_hash")
        if not isinstance(request_hash, str) or len(request_hash) != 64:
            raise SystemExit(f"invalid {label} request hash for {result['case_id']}")
        request_hashes.append(request_hash)
    if len(set(request_hashes)) != 24:
        raise SystemExit(f"{label} request hashes are not unique")
    return set(request_hashes)


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
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"missing or unsafe replay artifact: {path}")
    return sha256_bytes(path.read_bytes())


def fixture_manifest(paths: list[Path]) -> list[dict[str, object]]:
    return [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in paths
    ]


def fixture_manifest_sha256(paths: list[Path]) -> str:
    return sha256_text(canonical_json(fixture_manifest(paths)))


def require_equal(actual: object, expected: object, *, label: str) -> None:
    if actual != expected:
        raise SystemExit(f"{label} mismatch: {actual!r} != {expected!r}")


def verify_canonical_replay_artifacts(
    fixture_paths: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Validate the committed replay evidence without rewriting it."""

    raw_path = CANONICAL_REPLAY_DIR / "raw-results.jsonl"
    metrics_path = CANONICAL_REPLAY_DIR / "metrics.json"
    predictions_path = CANONICAL_REPLAY_DIR / "predictions-manifest.json"
    manifest_path = CANONICAL_REPLAY_DIR / "manifest.json"
    receipt_path = CANONICAL_REPLAY_DIR / "replay-verification.json"

    results = load_jsonl(raw_path)
    metrics = load_json(metrics_path)
    predictions = load_json(predictions_path)
    manifest = load_json(manifest_path)
    receipt = load_json(receipt_path)

    expected_case_ids = [f"C{index:03d}" for index in range(1, 25)]
    require_equal(manifest.get("case_ids"), expected_case_ids, label="canonical case IDs")
    require_equal(predictions.get("case_ids"), expected_case_ids, label="prediction case IDs")
    for payload, label in ((manifest, "manifest"), (predictions, "predictions")):
        require_equal(payload.get("mode"), "baseline", label=f"{label} mode")
        require_equal(payload.get("provider"), "replay", label=f"{label} provider")
        require_equal(payload.get("model"), MODEL, label=f"{label} model")
    require_equal(
        predictions.get("predictions_completed_before_gold_load"),
        True,
        label="prediction/gold ordering",
    )
    require_equal(
        manifest.get("predictions_committed_before_gold_load"),
        True,
        label="manifest prediction/gold ordering",
    )

    raw_sha = file_sha256(raw_path)
    metrics_sha = file_sha256(metrics_path)
    predictions_sha = file_sha256(predictions_path)
    manifest_sha = file_sha256(manifest_path)
    require_equal(predictions.get("raw_results_sha256"), raw_sha, label="prediction raw hash")
    require_equal(manifest.get("raw_results_sha256"), raw_sha, label="manifest raw hash")
    require_equal(manifest.get("metrics_sha256"), metrics_sha, label="manifest metrics hash")
    require_equal(
        manifest.get("predictions_manifest_sha256"),
        predictions_sha,
        label="manifest prediction hash",
    )
    require_equal(
        manifest.get("cases_sha256"),
        file_sha256(ROOT / "benchmark/cases.json"),
        label="canonical cases hash",
    )
    require_equal(
        manifest.get("gold_sha256"),
        file_sha256(ROOT / "benchmark/gold.json"),
        label="canonical gold hash",
    )

    stable_receipt_fields = {
        "verified": True,
        "model": MODEL,
        "case_count": 24,
        "fixture_count": 24,
        "live_raw_results_sha256": file_sha256(LIVE_DIR / "raw-results.jsonl"),
        "live_metrics_sha256": file_sha256(LIVE_DIR / "metrics.json"),
        "live_manifest_sha256": file_sha256(LIVE_DIR / "manifest.json"),
        "live_assembly_receipt_sha256": file_sha256(LIVE_DIR / "assembly-receipt.json"),
        "replay_raw_results_sha256": raw_sha,
        "replay_metrics_sha256": metrics_sha,
        "replay_manifest_sha256": manifest_sha,
        "fixture_manifest_sha256": fixture_manifest_sha256(fixture_paths),
        "cases_sha256": file_sha256(ROOT / "benchmark/cases.json"),
        "gold_sha256": file_sha256(ROOT / "benchmark/gold.json"),
    }
    for key, expected in stable_receipt_fields.items():
        require_equal(receipt.get(key), expected, label=f"canonical receipt {key}")
    require_equal(
        receipt.get("fixture_manifest"),
        fixture_manifest(fixture_paths),
        label="canonical receipt fixture manifest",
    )
    return results, metrics, receipt


def verify_live_assembly() -> dict[str, Any]:
    assembly_path = LIVE_DIR / "assembly-receipt.json"
    assembly = load_json(assembly_path)
    if (
        assembly.get("fixture_count") != 24
        or assembly.get("unique_request_hashes") != 24
        or assembly.get("gold_loaded_only_after_complete_predictions_written") is not True
    ):
        raise SystemExit("live baseline assembly receipt failed integrity checks")
    return assembly


def main() -> None:
    fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
    if len(fixture_paths) != 24:
        raise SystemExit(f"expected 24 replay fixtures, found {len(fixture_paths)}")

    live_results = load_jsonl(LIVE_DIR / "raw-results.jsonl")
    canonical_results, canonical_metrics, canonical_receipt = verify_canonical_replay_artifacts(
        fixture_paths
    )
    live_metrics = load_json(LIVE_DIR / "metrics.json")
    assembly = verify_live_assembly()

    with tempfile.TemporaryDirectory(prefix="mergeproof-replay-verification-") as raw_temp:
        fresh_dir = Path(raw_temp)
        provider = ReplayProvider(model=MODEL, replay_dir=FIXTURE_DIR)
        _, fresh_metrics = run_benchmark(
            mode="baseline",
            provider=provider,
            cases_path=ROOT / "benchmark/cases.json",
            gold_path=ROOT / "benchmark/gold.json",
            output_dir=fresh_dir,
        )
        fresh_results = load_jsonl(fresh_dir / "raw-results.jsonl")

        live_request_hashes = complete_request_hashes(live_results, label="live")
        canonical_request_hashes = complete_request_hashes(
            canonical_results, label="canonical replay"
        )
        fresh_request_hashes = complete_request_hashes(fresh_results, label="fresh replay")
        fixture_hashes = {path.stem for path in fixture_paths}
        if not (
            live_request_hashes
            == canonical_request_hashes
            == fresh_request_hashes
            == fixture_hashes
        ):
            raise SystemExit("live, canonical replay, fresh replay, and fixture identities differ")

        live_semantics = [normalize_result(item) for item in live_results]
        canonical_semantics = [normalize_result(item) for item in canonical_results]
        fresh_semantics = [normalize_result(item) for item in fresh_results]
        if live_semantics != canonical_semantics or live_semantics != fresh_semantics:
            raise SystemExit("live, canonical replay, and fresh replay semantics differ")

        for label, metrics in (
            ("live", live_metrics),
            ("canonical replay", canonical_metrics),
            ("fresh replay", fresh_metrics),
        ):
            if metrics.get("model_usage", {}).get("calls") != 24:
                raise SystemExit(f"expected 24 {label} model usages")
        expected_metrics = comparable_metrics(live_metrics)
        if comparable_metrics(canonical_metrics) != expected_metrics:
            raise SystemExit("canonical replay metrics differ from live metrics")
        if comparable_metrics(fresh_metrics) != expected_metrics:
            raise SystemExit("fresh replay metrics differ from live metrics")

    verification = {
        "schema_version": 3,
        "verified": True,
        "non_mutating": True,
        "temporary_replay_removed": True,
        "model": MODEL,
        "case_count": len(live_results),
        "fixture_count": len(fixture_paths),
        "canonical_replay_receipt_sha256": file_sha256(
            CANONICAL_REPLAY_DIR / "replay-verification.json"
        ),
        "canonical_replay_manifest_sha256": file_sha256(CANONICAL_REPLAY_DIR / "manifest.json"),
        "canonical_replay_raw_results_sha256": file_sha256(
            CANONICAL_REPLAY_DIR / "raw-results.jsonl"
        ),
        "live_raw_results_sha256": file_sha256(LIVE_DIR / "raw-results.jsonl"),
        "live_assembly_receipt_sha256": file_sha256(LIVE_DIR / "assembly-receipt.json"),
        "fixture_manifest_sha256": fixture_manifest_sha256(fixture_paths),
        "cases_sha256": file_sha256(ROOT / "benchmark/cases.json"),
        "gold_sha256": file_sha256(ROOT / "benchmark/gold.json"),
        "verification_code_sha256": file_sha256(Path(__file__)),
        "canonical_receipt_schema_version": canonical_receipt.get("schema_version"),
        "assembly_fixture_count": assembly.get("fixture_count"),
        "semantic_comparison": "Live, committed replay, and fresh temporary replay have identical decision semantics and request identities; volatile timing fields are excluded.",
        "scope": "Replay verifies processing of recorded model responses. It is not an unseen-input or model-generalization test.",
    }
    print(json.dumps(verification, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
