#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

if (($# > 1)); then
  printf 'Usage: %s [ABSENT_OUTPUT_DIRECTORY]\n' "$0" >&2
  exit 2
fi

ARGS=(demo)
if (($# == 1)); then
  OUTPUT=$(python -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1")
  ARGS+=(--output "$OUTPUT")
fi

exec uv run driftproof "${ARGS[@]}"
