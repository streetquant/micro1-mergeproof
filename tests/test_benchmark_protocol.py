from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import mergeproof.benchmark as benchmark
from mergeproof.models import AuditResult, CaseInput, Decision, GoldCase
from scripts.fetch_driftdoctor import build_verification_receipt


def test_predictions_are_fsynced_before_gold_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CaseInput(
        id="protocol-case",
        title="Protocol case",
        task="Review the candidate.",
        before={"value.py": "x = 1\n"},
        candidate={"value.py": "x = 2\n"},
        allowed_changed_globs=["value.py"],
    )
    cases_path = tmp_path / "cases.json"
    gold_path = tmp_path / "gold.json"
    output = tmp_path / "results"
    cases_path.write_text(json.dumps([case.model_dump(mode="json")]), encoding="utf-8")
    gold_path.write_text(
        json.dumps(
            [
                GoldCase(
                    id=case.id,
                    safe_to_merge=True,
                    categories=[],
                    rationale="Fixture.",
                ).model_dump(mode="json")
            ]
        ),
        encoding="utf-8",
    )

    result = AuditResult(
        case_id=case.id,
        mode="verified",
        decision=Decision.APPROVE,
        summary="Fixture approval.",
        confidence=1.0,
        provider="deterministic",
        model="fixture",
    )
    monkeypatch.setattr(
        benchmark,
        "_runner_for_mode",
        lambda mode, provider: (lambda _: result, "deterministic", "fixture"),
    )

    real_fsync = os.fsync
    fsync_calls: list[int] = []

    def recording_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(benchmark.os, "fsync", recording_fsync)

    def guarded_load_gold(path: Path) -> dict[str, GoldCase]:
        assert fsync_calls
        raw_path = output / "raw-results.jsonl"
        prediction_manifest = output / "predictions-manifest.json"
        assert raw_path.is_file()
        assert prediction_manifest.is_file()
        assert len(raw_path.read_text(encoding="utf-8").splitlines()) == 1
        marker = json.loads(prediction_manifest.read_text(encoding="utf-8"))
        assert marker["predictions_completed_before_gold_load"] is True
        return {
            case.id: GoldCase(
                id=case.id,
                safe_to_merge=True,
                categories=[],
                rationale="Fixture.",
            )
        }

    monkeypatch.setattr(benchmark, "load_gold", guarded_load_gold)

    _, metrics = benchmark.run_benchmark(
        mode="verified",
        provider=None,
        cases_path=cases_path,
        gold_path=gold_path,
        output_dir=output,
    )

    assert metrics["cases"] == 1
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    assert manifest["predictions_committed_before_gold_load"] is True
    assert manifest["runtime_environment"]["python_version"]
    assert len(manifest["source_tree_sha256"]) == 64


def test_upstream_verification_receipt_is_host_path_independent() -> None:
    receipt = build_verification_receipt(
        {"repository": "https://example.invalid/upstream"},
        {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "archive_sha256": "c" * 64,
            "license_sha256": "d" * 64,
            "requirements_sha256": "e" * 64,
        },
    )

    assert receipt["destination"] == "<UPSTREAM_CACHE>"
    serialized = json.dumps(receipt, sort_keys=True)
    assert "/" + "storage" + "/" not in serialized
    assert "/" + "home" + "/" not in serialized
