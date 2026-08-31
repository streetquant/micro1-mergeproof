from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPLAY_FILES = (
    ROOT / "results/baseline-replay-gpt-oss-20b/manifest.json",
    ROOT / "results/baseline-replay-gpt-oss-20b/metrics.json",
    ROOT / "results/baseline-replay-gpt-oss-20b/predictions-manifest.json",
    ROOT / "results/baseline-replay-gpt-oss-20b/raw-results.jsonl",
    ROOT / "results/baseline-replay-gpt-oss-20b/replay-verification.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_replay_verification_is_non_mutating() -> None:
    before = {path: _sha256(path) for path in CANONICAL_REPLAY_FILES}

    completed = subprocess.run(
        [sys.executable, "scripts/verify_replay.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    payload = json.loads(completed.stdout)
    assert payload["verified"] is True
    assert payload["non_mutating"] is True
    assert payload["temporary_replay_removed"] is True
    assert payload["case_count"] == 24
    assert payload["fixture_count"] == 24
    assert before == {path: _sha256(path) for path in CANONICAL_REPLAY_FILES}
