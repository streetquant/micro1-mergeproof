from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("dbt") is None,
    reason="judge quickstart requires the qualified dbt and bubblewrap runtime",
)
def test_judge_quickstart_proves_the_paired_green_build_failure(tmp_path: Path) -> None:
    output = tmp_path / "judge output with spaces"

    completed = subprocess.run(
        ["bash", "scripts/judge_quickstart.sh", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )

    receipt = json.loads((output / "quickstart-receipt.json").read_text(encoding="utf-8"))
    assert receipt["verified"] is True
    assert receipt["build_only_baseline"] == {"safe_exit": 0, "unsafe_exit": 0}
    assert receipt["driftproof"]["safe_verdict"] == "approve"
    assert receipt["driftproof"]["safe_exit"] == 0
    assert receipt["driftproof"]["unsafe_verdict"] == "reject"
    assert receipt["driftproof"]["unsafe_exit"] == 10
    assert receipt["driftproof"]["unsafe_failed_check_ids"]
    assert receipt["human_approval_required"] is True
    assert receipt["consequential_action_taken"] is False
    assert "Build-only baseline: safe PASS, unsafe PASS" in completed.stdout
    assert "DriftProof: safe APPROVE, unsafe REJECT" in completed.stdout
    assert (output / "reviews/safe/report.html").is_file()
    assert (output / "reviews/unsafe/report.html").is_file()
