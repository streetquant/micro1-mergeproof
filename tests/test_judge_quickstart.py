from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from driftproof.models import DriftProofDemoResponse
from driftproof.reporting import verify_gate_bundle

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("dbt") is None,
    reason="judge quickstart requires the qualified dbt and bubblewrap runtime",
)
def test_judge_quickstart_delegates_to_the_installed_demo(tmp_path: Path) -> None:
    output = tmp_path / "judge output with spaces"

    completed = subprocess.run(
        ["bash", "scripts/judge_quickstart.sh", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )

    receipt = DriftProofDemoResponse.model_validate_json(
        (output / "demo-receipt.json").read_text(encoding="utf-8")
    )
    assert receipt.verified is True
    assert receipt.safe.baseline_verdict == "approve"
    assert receipt.unsafe.baseline_verdict == "approve"
    assert receipt.safe.driftproof_verdict == "approve"
    assert receipt.safe.driftproof_exit_code == 0
    assert receipt.unsafe.driftproof_verdict == "reject"
    assert receipt.unsafe.driftproof_exit_code == 10
    assert receipt.unsafe.failed_check_ids
    assert receipt.human_approval_required is True
    assert receipt.consequential_action_taken is False
    assert verify_gate_bundle(Path(receipt.safe.bundle))["verified"] is True
    assert verify_gate_bundle(Path(receipt.unsafe.bundle))["verified"] is True
    assert "DriftProof credential-free installed demo" in completed.stderr
    assert "Both candidates built green" in completed.stderr
