#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

if (($# > 1)); then
  printf 'Usage: %s [ABSENT_OUTPUT_DIRECTORY]\n' "$0" >&2
  exit 2
fi

if (($# == 1)); then
  OUTPUT=$(python -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1")
  if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
    printf 'Output already exists; choose an absent path: %s\n' "$OUTPUT" >&2
    exit 2
  fi
  mkdir -p -- "$OUTPUT"
else
  OUTPUT=$(mktemp -d "${TMPDIR:-/tmp}/driftproof-judge-quickstart.XXXXXX")
fi

mkdir -p -- "$OUTPUT/projects" "$OUTPUT/baseline" "$OUTPUT/reviews" "$OUTPUT/work"

uv run driftproof doctor --json > "$OUTPUT/doctor.json"

for KIND in safe unsafe; do
  SOURCE="$ROOT/examples/judge-demo-$KIND"
  PROJECT="$OUTPUT/projects/$KIND"
  BASELINE="$OUTPUT/baseline/$KIND"
  cp -a -- "$SOURCE" "$PROJECT"
  cp -a -- "$SOURCE" "$BASELINE"

  (
    cd "$BASELINE"
    "$ROOT/.venv/bin/dbt" build \
      --project-dir . \
      --profiles-dir . \
      --no-use-colors
  ) > "$OUTPUT/baseline-$KIND.log" 2>&1
  printf '0\n' > "$OUTPUT/baseline-$KIND.exit"

  uv run driftproof preflight "$PROJECT" --json > "$OUTPUT/preflight-$KIND.json"

  set +e
  uv run driftproof review "$PROJECT" \
    --context "$PROJECT/BUSINESS_CONTEXT.md" \
    --output "$OUTPUT/reviews/$KIND" \
    --work-root "$OUTPUT/work/$KIND" \
    --json > "$OUTPUT/navigation-$KIND.json"
  REVIEW_EXIT=$?
  set -e
  printf '%s\n' "$REVIEW_EXIT" > "$OUTPUT/review-$KIND.exit"

  EXPECTED=0
  [[ "$KIND" == unsafe ]] && EXPECTED=10
  if [[ "$REVIEW_EXIT" -ne "$EXPECTED" ]]; then
    printf 'Unexpected %s review exit: got %s, expected %s\n' \
      "$KIND" "$REVIEW_EXIT" "$EXPECTED" >&2
    cat "$OUTPUT/navigation-$KIND.json" >&2
    exit 1
  fi

  uv run driftproof verify-report "$OUTPUT/reviews/$KIND" \
    > "$OUTPUT/verification-$KIND.json"
done

python - "$OUTPUT" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

output = Path(sys.argv[1]).resolve()


def load(name: str) -> dict[str, object]:
    payload = json.loads((output / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"expected object in {name}")
    return payload


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

safe = load("navigation-safe.json")
unsafe = load("navigation-unsafe.json")
safe_verification = load("verification-safe.json")
unsafe_verification = load("verification-unsafe.json")

assert safe["verdict"] == "approve"
assert safe["exit_code"] == 0
assert safe["bundle_verified"] is True
assert unsafe["verdict"] == "reject"
assert unsafe["exit_code"] == 10
assert unsafe["bundle_verified"] is True
assert safe_verification["verified"] is True
assert unsafe_verification["verified"] is True

receipt = {
    "schema_version": 1,
    "protocol": "driftproof.judge-quickstart.v1",
    "verified": True,
    "build_only_baseline": {"safe_exit": 0, "unsafe_exit": 0},
    "driftproof": {
        "safe_verdict": safe["verdict"],
        "safe_exit": safe["exit_code"],
        "unsafe_verdict": unsafe["verdict"],
        "unsafe_exit": unsafe["exit_code"],
        "unsafe_failed_check_ids": unsafe["failed_check_ids"],
    },
    "reports": {
        "safe": str(output / "reviews/safe/report.html"),
        "unsafe": str(output / "reviews/unsafe/report.html"),
    },
    "artifact_sha256": {
        name: sha(output / name)
        for name in (
            "doctor.json",
            "preflight-safe.json",
            "preflight-unsafe.json",
            "navigation-safe.json",
            "navigation-unsafe.json",
            "verification-safe.json",
            "verification-unsafe.json",
        )
    },
    "human_approval_required": True,
    "consequential_action_taken": False,
    "scope": "Credential-free paired demonstration. It is not the 24-case benchmark.",
}
(output / "quickstart-receipt.json").write_text(
    json.dumps(receipt, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(receipt, indent=2, sort_keys=True))
PY

printf '\nBuild-only baseline: safe PASS, unsafe PASS\n'
printf 'DriftProof: safe APPROVE, unsafe REJECT\n'
printf 'Safe report:   %s\n' "$OUTPUT/reviews/safe/report.html"
printf 'Unsafe report: %s\n' "$OUTPUT/reviews/unsafe/report.html"
printf 'Receipt:       %s\n' "$OUTPUT/quickstart-receipt.json"
