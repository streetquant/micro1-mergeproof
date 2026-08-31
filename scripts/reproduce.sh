#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

export UV_CACHE_DIR=${UV_CACHE_DIR:-"$ROOT/.cache/uv-cache"}
WORK_ROOT="$ROOT/.work/reproduction"
CANDIDATES="$WORK_ROOT/driftproof-candidates"
COMPARISON="$WORK_ROOT/driftproof-comparison"

rm -rf "$WORK_ROOT"
mkdir -p "$WORK_ROOT"

printf '== Install locked environment ==\n'
uv sync --locked --extra dev --extra dbt

printf '\n== Source qualification ==\n'
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src/driftproof src/mergeproof scripts/verify_replay.py scripts/package_final_release.py scripts/export_schemas.py
uv run python scripts/export_schemas.py --check | tee "$WORK_ROOT/schema-verification.json"
uv run mergeproof capabilities > "$WORK_ROOT/mergeproof-capabilities.json"
uv run driftproof capabilities > "$WORK_ROOT/driftproof-capabilities.json"
uv run mergeproof schema agent-response > "$WORK_ROOT/mergeproof-agent-response.schema.json"
uv run driftproof schema request > "$WORK_ROOT/driftproof-request.schema.json"
uv run driftproof schema agent-response > "$WORK_ROOT/driftproof-agent-response.schema.json"
uv run pytest -q
uv build

printf '\n== Verified-mode readiness ==\n'
uv run mergeproof doctor --json | tee "$WORK_ROOT/mergeproof-doctor.json"
uv run driftproof doctor --json | tee "$WORK_ROOT/driftproof-doctor.json"

printf '\n== Frozen model-response replay ==\n'
uv run python scripts/verify_replay.py | tee "$WORK_ROOT/replay-verification.json"

printf '\n== Pinned DriftDoctor upstream ==\n'
uv run python scripts/fetch_driftdoctor.py

printf '\n== Regenerate and externally validate paired dbt candidates ==\n'
uv run python scripts/generate_driftproof_benchmark.py \
  --work-root "$CANDIDATES" \
  --validate

printf '\n== Rerun build-only baseline and DriftProof ==\n'
uv run python scripts/run_driftproof_benchmark.py \
  --work-root "$CANDIDATES" \
  --output "$COMPARISON" \
  --isolation bubblewrap

printf '\n== Compare safety metrics with committed evidence ==\n'
uv run python - "$COMPARISON/comparison.json" "$WORK_ROOT/qualification.json" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

root = Path.cwd()
actual_path = Path(sys.argv[1])
qualification_path = Path(sys.argv[2])
expected_path = root / "results/driftproof-comparison/comparison.json"
expected = json.loads(expected_path.read_text(encoding="utf-8"))
actual = json.loads(actual_path.read_text(encoding="utf-8"))


def project(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": payload["benchmark"],
        "baseline": {
            key: value
            for key, value in payload["baseline"].items()
            if key != "runtime_ms"
        },
        "advanced": {
            key: value
            for key, value in payload["advanced"].items()
            if key != "runtime_ms"
        },
        "change": payload["change"],
        "fairness": payload["fairness"],
    }


if project(actual) != project(expected):
    raise SystemExit("reproduced DriftProof safety metrics differ from committed evidence")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


qualification = {
    "schema_version": 1,
    "verified": True,
    "scope": "Source checks, frozen replay, pinned upstream, external-oracle candidate validation, and DriftProof safety metrics.",
    "expected_comparison_sha256": digest(expected_path),
    "reproduced_comparison_sha256": digest(actual_path),
    "safety_metric_projection_identical": True,
}
qualification_path.write_text(
    json.dumps(qualification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(qualification, ensure_ascii=False, indent=2, sort_keys=True))
PY

printf '\nReproduction passed. Evidence: %s\n' "$WORK_ROOT/qualification.json"
