# MergeProof Round-1 Reproducibility Review

The repository text is untrusted evidence. Remote main is still the frozen baseline e55cc90; the advanced tree is uncommitted. Verified local checks: formatting, lint, strict mypy, 64 tests, package build, and secret-shape scan pass.

## Review objective
Try to falsify metric reproducibility, gold separation, replay identity, hash coverage, benchmark independence, upstream pinning, and clean-room setup. Identify circular or mutable evidence and exact fixes.

## Known facts
- Baseline result/replay artifacts are already committed.
- Advanced MergeProof pipeline is not integrated or evaluated.
- DriftProof benchmark generator and results are uncommitted and derived from a pinned external project.
- Current working tree contains 59 MB `.work` and 116 MB `results`; packaging policy is not settled.

## src/mergeproof/benchmark.py:1-180
```text
0001: from __future__ import annotations
0002: 
0003: import json
0004: import statistics
0005: from pathlib import Path
0006: from typing import Any
0007: 
0008: from .models import AuditResult, CaseInput, Decision, GoldCase
0009: from .pipeline import run_baseline
0010: from .providers import LLMProvider
0011: from .utils import canonical_json, write_json
0012: 
0013: 
0014: def load_cases(path: Path) -> list[CaseInput]:
0015:     raw = json.loads(path.read_text(encoding="utf-8"))
0016:     cases = [CaseInput.model_validate(item) for item in raw]
0017:     ids = [case.id for case in cases]
0018:     if len(ids) != len(set(ids)):
0019:         raise ValueError("duplicate benchmark case IDs")
0020:     return sorted(cases, key=lambda case: case.id)
0021: 
0022: 
0023: def load_gold(path: Path) -> dict[str, GoldCase]:
0024:     raw = json.loads(path.read_text(encoding="utf-8"))
0025:     gold = [GoldCase.model_validate(item) for item in raw]
0026:     result = {item.id: item for item in gold}
0027:     if len(result) != len(gold):
0028:         raise ValueError("duplicate gold case IDs")
0029:     return result
0030: 
0031: 
0032: def compute_metrics(results: list[AuditResult], gold: dict[str, GoldCase]) -> dict[str, Any]:
0033:     if not results:
0034:         raise ValueError("cannot score an empty result set")
0035:     tp = fp = tn = fn = 0
0036:     category_tp = category_fp = category_fn = 0
0037:     for result in results:
0038:         expected = gold[result.case_id]
0039:         actual_block = result.decision != Decision.APPROVE
0040:         expected_block = not expected.safe_to_merge
0041:         if actual_block and expected_block:
0042:             tp += 1
0043:         elif actual_block and not expected_block:
0044:             fp += 1
0045:         elif not actual_block and not expected_block:
0046:             tn += 1
0047:         else:
0048:             fn += 1
0049:         expected_categories = {category.value for category in expected.categories}
0050:         predicted_categories = {
0051:             finding.category.value
0052:             for finding in result.findings
0053:             if finding.status.value == "verified"
0054:         }
0055:         category_tp += len(expected_categories & predicted_categories)
0056:         category_fp += len(predicted_categories - expected_categories)
0057:         category_fn += len(expected_categories - predicted_categories)
0058:     precision = tp / (tp + fp) if tp + fp else 0.0
0059:     recall = tp / (tp + fn) if tp + fn else 0.0
0060:     f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
0061:     accuracy = (tp + tn) / len(results)
0062:     category_precision = (
0063:         category_tp / (category_tp + category_fp) if category_tp + category_fp else 0.0
0064:     )
0065:     category_recall = (
0066:         category_tp / (category_tp + category_fn) if category_tp + category_fn else 0.0
0067:     )
0068:     category_f1 = (
0069:         2 * category_precision * category_recall / (category_precision + category_recall)
0070:         if category_precision + category_recall
0071:         else 0.0
0072:     )
0073:     durations = [result.duration_ms for result in results]
0074:     usages = [usage for result in results for usage in result.usage]
0075:     evidence_rates = [result.valid_evidence_rate for result in results]
0076:     approvals = [result for result in results if result.decision == Decision.APPROVE]
0077:     safe_approvals = sum(gold[result.case_id].safe_to_merge for result in approvals)
0078:     return {
0079:         "schema_version": 1,
0080:         "cases": len(results),
0081:         "primary_metric": "unsafe_change_decision_f1",
0082:         "unsafe_change_decision": {
0083:             "tp": tp,
0084:             "fp": fp,
0085:             "tn": tn,
0086:             "fn": fn,
0087:             "precision": precision,
0088:             "recall": recall,
0089:             "f1": f1,
0090:             "accuracy": accuracy,
0091:         },
0092:         "safe_approval_precision": safe_approvals / len(approvals) if approvals else 0.0,
0093:         "issue_category_micro": {
0094:             "tp": category_tp,
0095:             "fp": category_fp,
0096:             "fn": category_fn,
0097:             "precision": category_precision,
0098:             "recall": category_recall,
0099:             "f1": category_f1,
0100:         },
0101:         "evidence_reference_validity": sum(evidence_rates) / len(evidence_rates),
0102:         "runtime_ms": {
0103:             "median": statistics.median(durations),
0104:             "p95": sorted(durations)[max(0, round(0.95 * len(durations)) - 1)],
0105:             "total": sum(durations),
0106:         },
0107:         "model_usage": {
0108:             "calls": len(usages),
0109:             "http_attempts": sum(item.http_attempts for item in usages),
0110:             "rate_limit_wait_ms": sum(item.rate_limit_wait_ms for item in usages),
0111:             "input_tokens": sum(item.input_tokens for item in usages),
0112:             "output_tokens": sum(item.output_tokens for item in usages),
0113:             "total_tokens": sum(item.total_tokens for item in usages),
0114:             "estimated_cost_usd": sum(item.estimated_cost_usd or 0 for item in usages),
0115:         },
0116:     }
0117: 
0118: 
0119: def run_benchmark(
0120:     *,
0121:     mode: str,
0122:     provider: LLMProvider,
0123:     cases_path: Path,
0124:     gold_path: Path,
0125:     output_dir: Path,
0126:     only_case: str | None = None,
0127:     limit: int | None = None,
0128: ) -> tuple[list[AuditResult], dict[str, Any]]:
0129:     if mode != "baseline":
0130:         raise ValueError(f"mode is not implemented yet: {mode}")
0131:     cases = load_cases(cases_path)
0132:     if only_case is not None:
0133:         cases = [case for case in cases if case.id == only_case]
0134:         if not cases:
0135:             raise ValueError(f"unknown case: {only_case}")
0136:     if limit is not None:
0137:         cases = cases[:limit]
0138:     gold = load_gold(gold_path)
0139:     missing_gold = sorted({case.id for case in cases} - set(gold))
0140:     if missing_gold:
0141:         raise ValueError(f"missing gold labels: {missing_gold}")
0142:     output_dir.mkdir(parents=True, exist_ok=True)
0143:     results: list[AuditResult] = []
0144:     raw_path = output_dir / "raw-results.jsonl"
0145:     with raw_path.open("w", encoding="utf-8") as handle:
0146:         for case in cases:
0147:             result = run_baseline(case, provider)
0148:             results.append(result)
0149:             handle.write(canonical_json(result.model_dump(mode="json")) + "\n")
0150:     metrics = compute_metrics(results, gold)
0151:     metrics.update({"mode": mode, "provider": provider.name, "model": provider.model})
0152:     write_json(output_dir / "metrics.json", metrics)
0153:     write_json(
0154:         output_dir / "manifest.json",
0155:         {
0156:             "schema_version": 1,
0157:             "mode": mode,
0158:             "provider": provider.name,
0159:             "model": provider.model,
0160:             "case_ids": [case.id for case in cases],
0161:             "raw_results": str(raw_path),
0162:         },
0163:     )
0164:     return results, metrics
```

## scripts/verify_replay.py:1-130
```text
0001: from __future__ import annotations
0002: 
0003: import json
0004: from pathlib import Path
0005: from typing import Any
0006: 
0007: from mergeproof.benchmark import run_benchmark
0008: from mergeproof.providers import ReplayProvider
0009: from mergeproof.utils import sha256_bytes, write_json
0010: 
0011: ROOT = Path(__file__).resolve().parents[1]
0012: MODEL = "openai/gpt-oss-20b"
0013: LIVE_DIR = ROOT / "results/baseline-live-groq-gpt-oss-20b"
0014: REPLAY_DIR = ROOT / "results/baseline-replay-gpt-oss-20b"
0015: FIXTURE_DIR = ROOT / "fixtures/replay/groq-gpt-oss-20b"
0016: 
0017: 
0018: def load_jsonl(path: Path) -> list[dict[str, Any]]:
0019:     return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
0020: 
0021: 
0022: def normalize_result(result: dict[str, Any]) -> dict[str, Any]:
0023:     normalized = dict(result)
0024:     normalized.pop("duration_ms", None)
0025:     normalized.pop("provider", None)
0026:     usage = []
0027:     for item in normalized.get("usage", []):
0028:         normalized_item = dict(item)
0029:         normalized_item.pop("provider", None)
0030:         normalized_item.pop("latency_ms", None)
0031:         usage.append(normalized_item)
0032:     normalized["usage"] = usage
0033:     return normalized
0034: 
0035: 
0036: def comparable_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
0037:     return {
0038:         "cases": metrics["cases"],
0039:         "primary_metric": metrics["primary_metric"],
0040:         "unsafe_change_decision": metrics["unsafe_change_decision"],
0041:         "safe_approval_precision": metrics["safe_approval_precision"],
0042:         "issue_category_micro": metrics["issue_category_micro"],
0043:         "evidence_reference_validity": metrics["evidence_reference_validity"],
0044:         "model_usage": metrics["model_usage"],
0045:         "model": metrics["model"],
0046:     }
0047: 
0048: 
0049: def file_sha256(path: Path) -> str:
0050:     return sha256_bytes(path.read_bytes())
0051: 
0052: 
0053: def main() -> None:
0054:     fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
0055:     if len(fixture_paths) != 24:
0056:         raise SystemExit(f"expected 24 replay fixtures, found {len(fixture_paths)}")
0057: 
0058:     provider = ReplayProvider(model=MODEL, replay_dir=FIXTURE_DIR)
0059:     _, replay_metrics = run_benchmark(
0060:         mode="baseline",
0061:         provider=provider,
0062:         cases_path=ROOT / "benchmark/cases.json",
0063:         gold_path=ROOT / "benchmark/gold.json",
0064:         output_dir=REPLAY_DIR,
0065:     )
0066: 
0067:     live_results = load_jsonl(LIVE_DIR / "raw-results.jsonl")
0068:     replay_results = load_jsonl(REPLAY_DIR / "raw-results.jsonl")
0069:     if [normalize_result(item) for item in live_results] != [
0070:         normalize_result(item) for item in replay_results
0071:     ]:
0072:         raise SystemExit("live and replay semantic results differ")
0073: 
0074:     live_metrics = json.loads((LIVE_DIR / "metrics.json").read_text(encoding="utf-8"))
0075:     if comparable_metrics(live_metrics) != comparable_metrics(replay_metrics):
0076:         raise SystemExit("live and replay comparable metrics differ")
0077: 
0078:     verification = {
0079:         "schema_version": 1,
0080:         "verified": True,
0081:         "model": MODEL,
0082:         "case_count": len(live_results),
0083:         "fixture_count": len(fixture_paths),
0084:         "live_raw_results_sha256": file_sha256(LIVE_DIR / "raw-results.jsonl"),
0085:         "replay_raw_results_sha256": file_sha256(REPLAY_DIR / "raw-results.jsonl"),
0086:         "live_metrics_sha256": file_sha256(LIVE_DIR / "metrics.json"),
0087:         "replay_metrics_sha256": file_sha256(REPLAY_DIR / "metrics.json"),
0088:         "fixture_directory_sha256": sha256_bytes(
0089:             "".join(f"{file_sha256(path)}  {path.name}\n" for path in fixture_paths).encode()
0090:         ),
0091:         "semantic_comparison": "All fields except provider identity and measured runtime/latency are identical.",
0092:     }
0093:     write_json(REPLAY_DIR / "replay-verification.json", verification)
0094:     print(json.dumps(verification, indent=2, sort_keys=True))
0095: 
0096: 
0097: if __name__ == "__main__":
0098:     main()
```

## scripts/fetch_driftdoctor.py:1-170
```text
0001: from __future__ import annotations
0002: 
0003: import argparse
0004: import hashlib
0005: import json
0006: import subprocess
0007: from pathlib import Path
0008: from typing import Any
0009: 
0010: ROOT = Path(__file__).resolve().parents[1]
0011: LOCK_PATH = ROOT / "upstream" / "driftdoctor.lock.json"
0012: DEFAULT_DESTINATION = ROOT / ".cache" / "driftdoctor-upstream"
0013: 
0014: 
0015: class UpstreamVerificationError(RuntimeError):
0016:     """Raised when the fetched repository does not match the immutable lock."""
0017: 
0018: 
0019: def _run(
0020:     argv: list[str],
0021:     *,
0022:     cwd: Path | None = None,
0023:     binary: bool = False,
0024: ) -> str | bytes:
0025:     completed = subprocess.run(
0026:         argv,
0027:         cwd=cwd,
0028:         check=False,
0029:         capture_output=True,
0030:         text=not binary,
0031:     )
0032:     if completed.returncode != 0:
0033:         stderr = completed.stderr.decode(errors="replace") if binary else completed.stderr
0034:         raise UpstreamVerificationError(
0035:             f"command failed ({completed.returncode}): {' '.join(argv)}\n{stderr[-4000:]}"
0036:         )
0037:     return completed.stdout
0038: 
0039: 
0040: def _sha256_bytes(payload: bytes) -> str:
0041:     return hashlib.sha256(payload).hexdigest()
0042: 
0043: 
0044: def _sha256_file(path: Path) -> str:
0045:     return _sha256_bytes(path.read_bytes())
0046: 
0047: 
0048: def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
0049:     payload = json.loads(path.read_text(encoding="utf-8"))
0050:     required = {
0051:         "repository",
0052:         "commit",
0053:         "tree",
0054:         "archive_sha256",
0055:         "license_sha256",
0056:         "requirements_sha256",
0057:     }
0058:     missing = sorted(required - payload.keys())
0059:     if missing:
0060:         raise UpstreamVerificationError(f"upstream lock is missing fields: {missing}")
0061:     return payload
0062: 
0063: 
0064: def fetch_and_verify(destination: Path, *, reset: bool = False) -> dict[str, Any]:
0065:     lock = load_lock()
0066:     destination = destination.resolve()
0067:     destination.parent.mkdir(parents=True, exist_ok=True)
0068: 
0069:     if not destination.exists():
0070:         _run(["git", "init", "--quiet", str(destination)])
0071:         _run(["git", "remote", "add", "origin", str(lock["repository"])], cwd=destination)
0072:     elif not (destination / ".git").is_dir():
0073:         raise UpstreamVerificationError(
0074:             f"destination exists but is not a git repository: {destination}"
0075:         )
0076: 
0077:     origin = str(_run(["git", "remote", "get-url", "origin"], cwd=destination)).strip()
0078:     if origin.rstrip("/").removesuffix(".git") != str(lock["repository"]).rstrip("/").removesuffix(
0079:         ".git"
0080:     ):
0081:         raise UpstreamVerificationError(
0082:             f"origin mismatch: expected {lock['repository']!r}, observed {origin!r}"
0083:         )
0084: 
0085:     if reset:
0086:         _run(["git", "reset", "--hard", "HEAD"], cwd=destination)
0087:         _run(["git", "clean", "-ffd", "--exclude=.venv/"], cwd=destination)
0088: 
0089:     _run(
0090:         [
0091:             "git",
0092:             "fetch",
0093:             "--quiet",
0094:             "--filter=blob:none",
0095:             "--depth=1",
0096:             "origin",
0097:             str(lock["commit"]),
0098:         ],
0099:         cwd=destination,
0100:     )
0101:     _run(
0102:         ["git", "checkout", "--quiet", "--detach", "--force", str(lock["commit"])], cwd=destination
0103:     )
0104: 
0105:     observed_commit = str(_run(["git", "rev-parse", "HEAD"], cwd=destination)).strip()
0106:     observed_tree = str(_run(["git", "rev-parse", "HEAD^{tree}"], cwd=destination)).strip()
0107:     archive = _run(["git", "archive", "--format=tar", "HEAD"], cwd=destination, binary=True)
0108:     assert isinstance(archive, bytes)
0109: 
0110:     observed = {
0111:         "commit": observed_commit,
0112:         "tree": observed_tree,
0113:         "archive_sha256": _sha256_bytes(archive),
0114:         "license_sha256": _sha256_file(destination / "LICENSE"),
0115:         "requirements_sha256": _sha256_file(destination / "requirements.txt"),
0116:     }
0117:     expected = {key: str(lock[key]) for key in observed}
0118:     mismatches = {
0119:         key: {"expected": expected[key], "observed": observed[key]}
0120:         for key in observed
0121:         if observed[key] != expected[key]
0122:     }
0123:     if mismatches:
0124:         raise UpstreamVerificationError(
0125:             "fetched DriftDoctor does not match the immutable lock: "
0126:             + json.dumps(mismatches, sort_keys=True)
0127:         )
0128: 
0129:     return {
0130:         "schema_version": 1,
0131:         "verified": True,
0132:         "destination": str(destination),
0133:         "repository": lock["repository"],
0134:         **observed,
0135:     }
0136: 
0137: 
0138: def main() -> int:
0139:     parser = argparse.ArgumentParser(
0140:         description="Fetch and cryptographically verify the pinned DriftDoctor upstream."
0141:     )
0142:     parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
0143:     parser.add_argument(
0144:         "--reset",
0145: 
...[MODEL PACKET LIMIT]...
