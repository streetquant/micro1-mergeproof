from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path
from typing import Any

from mergeproof.utils import atomic_write_text, pretty_json

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from scripts.judge_packet import build_judge_artifacts  # noqa: E402

OUTPUT_DIRECTORY = Path("submission")
README_START = "<!-- DRIFTPROOF-METRICS:START -->"
README_END = "<!-- DRIFTPROOF-METRICS:END -->"


class SubmissionRenderError(RuntimeError):
    """Raised when judge-facing submission artifacts are missing or stale."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SubmissionRenderError(f"required submission source is missing or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionRenderError(f"required submission source is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SubmissionRenderError(f"required submission source must be a JSON object: {path}")
    return value


def _source_paths(root: Path) -> list[Path]:
    fixed = [
        root / "results/driftproof-comparison/comparison.json",
        root / "benchmark_dbt/manifest.json",
        root / "schemas/manifest.json",
        root / "results/baseline-replay-gpt-oss-20b/replay-verification.json",
        root / "reviews/recovery-promotion/qualification.json",
        root / "reviews/replay-nonmutating/qualification.json",
    ]
    rounds = sorted((root / "reviews").glob("2026-08-31-round-*/qualification.json"))
    paths = [*fixed, *rounds]
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise SubmissionRenderError(f"required submission source is missing or unsafe: {path}")
    return paths


def _round_count(value: float, total: int) -> int:
    return round(value * total)


def _metrics(root: Path) -> dict[str, Any]:
    comparison = _load_object(root / "results/driftproof-comparison/comparison.json")
    try:
        baseline = comparison["baseline"]
        advanced = comparison["advanced"]
        change = comparison["change"]
        cases = int(advanced["cases"])
        if int(baseline["cases"]) != cases:
            raise ValueError("baseline and advanced case counts differ")
        metrics = {
            "benchmark": str(comparison["benchmark"]),
            "cases": cases,
            "baseline_macro_f1": float(baseline["safe_approval_macro_f1"]),
            "advanced_macro_f1": float(advanced["safe_approval_macro_f1"]),
            "macro_f1_change": float(change["safe_approval_macro_f1"]),
            "baseline_accuracy": float(baseline["accuracy"]),
            "advanced_accuracy": float(advanced["accuracy"]),
            "accuracy_change": float(change["accuracy"]),
            "baseline_unsafe_escape_rate": float(baseline["unsafe_repair_escape_rate"]),
            "advanced_unsafe_escape_rate": float(advanced["unsafe_repair_escape_rate"]),
            "unsafe_escape_rate_change": float(change["unsafe_repair_escape_rate"]),
            "baseline_safe_approved": int(baseline["safe_class"]["tp"]),
            "advanced_safe_approved": int(advanced["safe_class"]["tp"]),
            "safe_total": int(advanced["safe_class"]["tp"]) + int(advanced["safe_class"]["fn"]),
            "baseline_unsafe_blocked": int(baseline["unsafe_class"]["tp"]),
            "advanced_unsafe_blocked": int(advanced["unsafe_class"]["tp"]),
            "unsafe_total": int(advanced["unsafe_class"]["tp"])
            + int(advanced["unsafe_class"]["fn"]),
            "advanced_human_reviews": _round_count(float(advanced["human_review_rate"]), cases),
            "advanced_human_review_rate": float(advanced["human_review_rate"]),
            "fairness": comparison["fairness"],
            "provenance": comparison["provenance"],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SubmissionRenderError(
            "comparison.json does not match the expected metric contract"
        ) from exc

    if metrics["safe_total"] + metrics["unsafe_total"] != cases:
        raise SubmissionRenderError("class counts do not equal the benchmark case count")
    if metrics["advanced_unsafe_escape_rate"] != 0.0:
        raise SubmissionRenderError("submission promise requires the measured unsafe escape rate")
    return metrics


def _percentage(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _signed_percentage_points(value: float) -> str:
    return f"{100.0 * value:+.1f} pp"


def _signed_decimal(value: float) -> str:
    return f"{value:+.3f}"


def _metrics_markdown(metrics: dict[str, Any]) -> str:
    return "\n".join(
        [
            "The frozen, balanced benchmark contains "
            f"{metrics['cases']} project-authored candidates: {metrics['safe_total']} externally "
            f"safe and {metrics['unsafe_total']} green-but-semantically-wrong. Both workflows receive "
            "the same candidate, visible business context, trajectory, and `dbt build` command; gold "
            "labels are opened only after predictions are written.",
            "",
            "| Metric | Build-only baseline | DriftProof | Change |",
            "|---|---:|---:|---:|",
            f"| Safe-approval macro-F1 | {metrics['baseline_macro_f1']:.3f} | **{metrics['advanced_macro_f1']:.3f}** | **{_signed_decimal(metrics['macro_f1_change'])}** |",
            f"| Accuracy | {_percentage(metrics['baseline_accuracy'])} | **{_percentage(metrics['advanced_accuracy'])}** | **{_signed_percentage_points(metrics['accuracy_change'])}** |",
            f"| Unsafe-repair escape rate | {_percentage(metrics['baseline_unsafe_escape_rate'])} | **{_percentage(metrics['advanced_unsafe_escape_rate'])}** | **{_signed_percentage_points(metrics['unsafe_escape_rate_change'])}** |",
            f"| Safe candidates automatically approved | {metrics['baseline_safe_approved']}/{metrics['safe_total']} | **{metrics['advanced_safe_approved']}/{metrics['safe_total']}** | {metrics['advanced_safe_approved'] - metrics['baseline_safe_approved']:+d} |",
            f"| Unsafe candidates blocked from automatic approval | {metrics['baseline_unsafe_blocked']}/{metrics['unsafe_total']} | **{metrics['advanced_unsafe_blocked']}/{metrics['unsafe_total']}** | **{metrics['advanced_unsafe_blocked'] - metrics['baseline_unsafe_blocked']:+d}** |",
            f"| Qualified-human escalations | 0/{metrics['cases']} | **{metrics['advanced_human_reviews']}/{metrics['cases']}** | +{metrics['advanced_human_reviews']} |",
            "",
            "The measured trade-off is deliberate and visible: DriftProof reduced unsafe escapes from "
            f"{_percentage(metrics['baseline_unsafe_escape_rate'])} to {_percentage(metrics['advanced_unsafe_escape_rate'])}, "
            f"while automatically approving {metrics['advanced_safe_approved']} of {metrics['safe_total']} safe candidates and "
            f"escalating {metrics['advanced_human_reviews']} cases to a qualified human. It does not claim universal correctness or formal verification.",
            "",
            "The authoritative comparison, raw predictions, candidate bundles, and exact metric inputs are in "
            "[`results/driftproof-comparison/`](results/driftproof-comparison/).",
        ]
    )


def _readme_with_metrics(readme: str, section: str) -> str:
    replacement = f"{README_START}\n{section}\n{README_END}"
    if README_START in readme or README_END in readme:
        if readme.count(README_START) != 1 or readme.count(README_END) != 1:
            raise SubmissionRenderError("README metric markers are missing or duplicated")
        before, remainder = readme.split(README_START, 1)
        _old, after = remainder.split(README_END, 1)
        return before + replacement + after

    heading = "## Result on the frozen benchmark"
    next_heading = "## Credential-free judge demonstration"
    if heading not in readme or next_heading not in readme:
        raise SubmissionRenderError("README metric section headings are missing")
    prefix, remainder = readme.split(heading, 1)
    _old, suffix = remainder.split(next_heading, 1)
    return f"{prefix}{heading}\n\n{replacement}\n\n{next_heading}{suffix}"


def _start_here_markdown(metrics: dict[str, Any]) -> str:
    return f"""# DriftProof submission — start here

DriftProof is an independent, fail-closed release gate for agent-authored dbt repairs. It checks a visible business contract in a networkless disposable worktree and publishes a self-verifying bundle for a qualified human. It never merges or deploys code.

## One-command judge path

```bash
uv sync --locked --extra dbt
uv run driftproof demo
```

Both transparent fixtures pass the same build-only `dbt build`. DriftProof approves the contract-preserving fixture and rejects the green-but-wrong fixture, independently verifies both bundles, and prints the HTML report paths plus a machine receipt.

## Judge packet

- [`JUDGE_CHECKLIST.md`](JUDGE_CHECKLIST.md) — shortest evidence-first evaluation path.
- [`CLAIM_LEDGER.json`](CLAIM_LEDGER.json) — every headline claim bound to exact evidence and limitations.
- [`RUBRIC_MAP.json`](RUBRIC_MAP.json) — the complete 100-point rubric mapped to claims and executable checks.
- [`AGENT_TRAJECTORIES.json`](AGENT_TRAJECTORIES.json) — representative instructions, responses, verifier feedback, retry evidence, and human checkpoints for every workflow agent.
- [`TRACE_INDEX.json`](TRACE_INDEX.json) — content-addressed coverage of all canonical trace sources.

For complete source qualification:

```bash
make check
```

For an exact-source solution video and deterministic release set from clean private `main`:

```bash
make submission-check
make video VIDEO_OUTPUT=release/video
make video-verify VIDEO_OUTPUT=release/video
make release MEDIA_DIRECTORY=release/video
```

The renderer derives every scene and spoken claim from the exact commit and frozen evidence. The verifier requires a complete decode, one 1920x1080 H.264 stream, one AAC 48 kHz stream, audible narration, a duration below five minutes, and commit-bound transcript/storyboard/source receipts.

A recipient can verify the downloaded release without installing DriftProof or retaining the source checkout:

```bash
python verify-release.pyz .
```

The standalone verifier uses only Python's standard library and Git, emits one JSON object, validates every checksum and archive, cross-binds the judge packet, and verifies the embedded Git bundle.

## Measured result

| Metric | Build-only baseline | DriftProof | Change |
|---|---:|---:|---:|
| Safe-approval macro-F1 | {metrics["baseline_macro_f1"]:.3f} | {metrics["advanced_macro_f1"]:.3f} | {_signed_decimal(metrics["macro_f1_change"])} |
| Accuracy | {_percentage(metrics["baseline_accuracy"])} | {_percentage(metrics["advanced_accuracy"])} | {_signed_percentage_points(metrics["accuracy_change"])} |
| Unsafe-repair escape rate | {_percentage(metrics["baseline_unsafe_escape_rate"])} | {_percentage(metrics["advanced_unsafe_escape_rate"])} | {_signed_percentage_points(metrics["unsafe_escape_rate_change"])} |
| Safe candidates automatically approved | {metrics["baseline_safe_approved"]}/{metrics["safe_total"]} | {metrics["advanced_safe_approved"]}/{metrics["safe_total"]} | {metrics["advanced_safe_approved"] - metrics["baseline_safe_approved"]:+d} |
| Unsafe candidates blocked from automatic approval | {metrics["baseline_unsafe_blocked"]}/{metrics["unsafe_total"]} | {metrics["advanced_unsafe_blocked"]}/{metrics["unsafe_total"]} | {metrics["advanced_unsafe_blocked"] - metrics["baseline_unsafe_blocked"]:+d} |
| Qualified-human escalations | 0/{metrics["cases"]} | {metrics["advanced_human_reviews"]}/{metrics["cases"]} | +{metrics["advanced_human_reviews"]} |

DriftProof eliminated measured unsafe escapes on this frozen benchmark, but it is intentionally conservative: only {metrics["advanced_safe_approved"]} of {metrics["safe_total"]} safe candidates were automatically approved and {metrics["advanced_human_reviews"]} cases were escalated. The benchmark is balanced, synthetic, and project-authored; it is not evidence of universal correctness, formal verification, or unseen-project generalization.

## Human reviewer path

```bash
uv run driftproof doctor --json
uv run driftproof onboard /absolute/path/to/dbt-project --run-id reviewer-1 --json
uv run driftproof preflight /absolute/path/to/dbt-project --json
uv run driftproof review /absolute/path/to/dbt-project --run-id reviewer-1
uv run driftproof verify-report /path/to/bundle
```

`onboard --apply` creates only a missing `BUSINESS_CONTEXT.md` and never overwrites human-authored content. Every valid verdict still ends at a qualified-human checkpoint.

## AI-agent path

```python
from pathlib import Path

from driftproof.sdk import (
    ReviewRequest,
    fingerprint_for_agent,
    review_and_verify_for_agent,
)

request = ReviewRequest(project="candidate", context="candidate/BUSINESS_CONTEXT.md")
identity = fingerprint_for_agent(request, base_dir=Path.cwd())
response, verification = review_and_verify_for_agent(request, base_dir=Path.cwd())
assert verification.request_identity_verified
assert verification.request_sha256 == identity.configuration_request_sha256
assert verification.bundle_verified == verification.review_result_trusted
raise SystemExit(response.exit_code)
```

The typed SDK validates the one-object protocol, rejects malformed output and process/response disagreement, independently binds bundle-backed response claims, and assigns disjoint control run IDs to independent concurrent callers. Content-bound retries can use `request_with_stable_run_id`. Machine contracts are discoverable through `driftproof capabilities` and [`../schemas/driftproof/`](../schemas/driftproof/).

## Evidence map

- Authoritative comparison: [`../results/driftproof-comparison/comparison.json`](../results/driftproof-comparison/comparison.json)
- Candidate reports and raw predictions: [`../results/driftproof-comparison/`](../results/driftproof-comparison/)
- Benchmark validation: [`../results/driftproof-benchmark-validation/summary.json`](../results/driftproof-benchmark-validation/summary.json)
- Human/judge adversarial review: [`../reviews/2026-08-31-round-1-human-judge/`](../reviews/2026-08-31-round-1-human-judge/)
- AI-agent/SDK adversarial review: [`../reviews/2026-08-31-round-2-agent-sdk/`](../reviews/2026-08-31-round-2-agent-sdk/)
- Release/delivery adversarial review: [`../reviews/2026-08-31-round-3-release-delivery/`](../reviews/2026-08-31-round-3-release-delivery/)
- Downloaded-release consumer review: [`../reviews/2026-08-31-round-4-consumer-verifier/`](../reviews/2026-08-31-round-4-consumer-verifier/)
- Installed demo and runtime-recovery review: [`../reviews/2026-08-31-round-5-installed-demo/`](../reviews/2026-08-31-round-5-installed-demo/)
- Response authenticity and retry-semantics review: [`../reviews/2026-08-31-round-6-response-binding/`](../reviews/2026-08-31-round-6-response-binding/)
- Hostile judge-packet and evidence-binding review: [`../reviews/2026-08-31-round-7-judge-packet/`](../reviews/2026-08-31-round-7-judge-packet/)
- Standalone downloaded-release verifier review: [`../reviews/2026-08-31-round-8-standalone-verifier/`](../reviews/2026-08-31-round-8-standalone-verifier/)
- Exact-source video and downloaded-media review: [`../reviews/2026-08-31-round-11-exact-source-video/`](../reviews/2026-08-31-round-11-exact-source-video/)
- Machine-readable submission manifest: [`manifest.json`](manifest.json)
- Full product and trust-boundary documentation: [`../README.md`](../README.md)

## Fixed safety boundary

- `human_approval_required` is always `true`.
- `consequential_action_taken` is always `false`.
- An approval certificate is decision support, never authorization to merge, deploy, publish, notify, or delete.
"""


def _start_here_html(metrics: dict[str, Any]) -> str:
    rows = [
        (
            "Safe-approval macro-F1",
            f"{metrics['baseline_macro_f1']:.3f}",
            f"{metrics['advanced_macro_f1']:.3f}",
            _signed_decimal(metrics["macro_f1_change"]),
        ),
        (
            "Accuracy",
            _percentage(metrics["baseline_accuracy"]),
            _percentage(metrics["advanced_accuracy"]),
            _signed_percentage_points(metrics["accuracy_change"]),
        ),
        (
            "Unsafe-repair escape rate",
            _percentage(metrics["baseline_unsafe_escape_rate"]),
            _percentage(metrics["advanced_unsafe_escape_rate"]),
            _signed_percentage_points(metrics["unsafe_escape_rate_change"]),
        ),
        (
            "Safe candidates automatically approved",
            f"{metrics['baseline_safe_approved']}/{metrics['safe_total']}",
            f"{metrics['advanced_safe_approved']}/{metrics['safe_total']}",
            f"{metrics['advanced_safe_approved'] - metrics['baseline_safe_approved']:+d}",
        ),
        (
            "Unsafe candidates blocked",
            f"{metrics['baseline_unsafe_blocked']}/{metrics['unsafe_total']}",
            f"{metrics['advanced_unsafe_blocked']}/{metrics['unsafe_total']}",
            f"{metrics['advanced_unsafe_blocked'] - metrics['baseline_unsafe_blocked']:+d}",
        ),
        (
            "Qualified-human escalations",
            f"0/{metrics['cases']}",
            f"{metrics['advanced_human_reviews']}/{metrics['cases']}",
            f"+{metrics['advanced_human_reviews']}",
        ),
    ]
    table_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DriftProof submission — start here</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1040px;margin:0 auto;padding:2rem;color:#171717}}
h1,h2{{line-height:1.2}} code,pre{{font-family:ui-monospace,SFMono-Regular,monospace}} pre{{padding:1rem;background:#f4f4f4;overflow:auto;border-radius:.5rem}} table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #bbb;padding:.55rem;text-align:left}} th{{background:#eee}} .boundary{{border:2px solid #333;padding:1rem;border-radius:.5rem}} a{{color:#0645ad}}
</style>
</head>
<body>
<h1>DriftProof submission — start here</h1>
<p>Independent, fail-closed release gate for agent-authored dbt repairs. It verifies a visible business contract and publishes a self-verifying bundle for a qualified human. It never merges or deploys code.</p>
<h2>One-command judge path</h2>
<pre>uv sync --locked --extra dbt
uv run driftproof demo</pre>
<p>Both fixtures pass the same build-only <code>dbt build</code>. DriftProof approves the contract-preserving fixture and rejects the green-but-wrong fixture, verifies both bundles, and prints the report paths.</p>
<h2>Judge packet</h2>
<ul>
<li><a href="JUDGE_CHECKLIST.md">Judge checklist</a></li>
<li><a href="CLAIM_LEDGER.json">Evidence-bound claim ledger</a></li>
<li><a href="RUBRIC_MAP.json">100-point rubric map</a></li>
<li><a href="AGENT_TRAJECTORIES.json">Representative trajectories for every workflow agent</a></li>
<li><a href="TRACE_INDEX.json">Content-addressed trace index</a></li>
</ul>
<h2>Exact-source solution video</h2>
<pre>make video VIDEO_OUTPUT=release/video
make video-verify VIDEO_OUTPUT=release/video
make release MEDIA_DIRECTORY=release/video</pre>
<p>The video is derived from the exact source commit and frozen evidence, remains below five minutes, and ships with transcript, storyboard, scene-duration, source-manifest, and verification receipts.</p>
<h2>Downloaded release verification</h2>
<pre>python verify-release.pyz .</pre>
<p>The standalone verifier requires only Python's standard library and Git. It validates checksums, archives, the judge packet, and the embedded Git bundle, then emits one JSON object.</p>
<h2>Measured result</h2>
<table><thead><tr><th>Metric</th><th>Build-only baseline</th><th>DriftProof</th><th>Change</th></tr></thead><tbody>{table_rows}</tbody></table>
<p><strong>Trade-off:</strong> measured unsafe escapes fell to {_percentage(metrics["advanced_unsafe_escape_rate"])}, while only {metrics["advanced_safe_approved"]} of {metrics["safe_total"]} safe candidates were automatically approved and {metrics["advanced_human_reviews"]} cases were escalated. This is a balanced, synthetic, project-authored benchmark—not universal correctness or formal verification.</p>
<h2>Human workflow</h2>
<pre>uv run driftproof doctor --json
uv run driftproof onboard /absolute/path/to/dbt-project --run-id reviewer-1 --json
uv run driftproof preflight /absolute/path/to/dbt-project --json
uv run driftproof review /absolute/path/to/dbt-project --run-id reviewer-1
uv run driftproof verify-report /path/to/bundle</pre>
<h2>AI-agent workflow</h2>
<pre>from pathlib import Path
from driftproof.sdk import ReviewRequest, fingerprint_for_agent, review_and_verify_for_agent
request = ReviewRequest(project="candidate", context="candidate/BUSINESS_CONTEXT.md")
identity = fingerprint_for_agent(request, base_dir=Path.cwd())
response, verification = review_and_verify_for_agent(request, base_dir=Path.cwd())
assert verification.request_identity_verified
assert verification.request_sha256 == identity.configuration_request_sha256
assert verification.bundle_verified == verification.review_result_trusted</pre>
<p>See <a href="../schemas/driftproof/">machine schemas</a>, the <a href="../results/driftproof-comparison/comparison.json">authoritative comparison</a>, and the <a href="manifest.json">submission manifest</a>.</p>
<div class="boundary"><strong>Fixed boundary:</strong> human approval is always required; no consequential action is taken.</div>
</body>
</html>
"""


def _manifest(
    root: Path,
    metrics: dict[str, Any],
    markdown: str,
    html_text: str,
    judge_artifacts: dict[str, str],
) -> dict[str, Any]:
    sources = {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in _source_paths(root)
    }
    return {
        "schema_version": 1,
        "protocol": "driftproof.submission-manifest.v1",
        "product": "DriftProof",
        "benchmark": metrics["benchmark"],
        "metrics": metrics,
        "entry_points": {
            "human": "submission/START_HERE.md",
            "browser": "submission/START_HERE.html",
            "machine": "submission/manifest.json",
            "judge_checklist": "submission/JUDGE_CHECKLIST.md",
            "claim_ledger": "submission/CLAIM_LEDGER.json",
            "rubric_map": "submission/RUBRIC_MAP.json",
            "agent_trajectories": "submission/AGENT_TRAJECTORIES.json",
            "trace_index": "submission/TRACE_INDEX.json",
        },
        "commands": {
            "judge_demo": ["driftproof", "demo", "--json"],
            "source_qualification": ["make", "check"],
            "submission_drift_check": ["make", "submission-check"],
            "video_render": ["make", "video", "VIDEO_OUTPUT=release/video"],
            "video_verify": ["make", "video-verify", "VIDEO_OUTPUT=release/video"],
            "release_package": ["make", "release", "MEDIA_DIRECTORY=release/video"],
            "downloaded_release_verify": ["python", "verify-release.pyz", "."],
            "agent_capabilities": ["driftproof", "capabilities"],
            "agent_schema": ["driftproof", "schema", "agent-response"],
            "response_verification_schema": [
                "driftproof",
                "schema",
                "response-verification",
            ],
            "verify_agent_response": [
                "driftproof",
                "verify-response",
                "response.json",
            ],
        },
        "generated_files": {
            name: {
                "bytes": len(content.encode("utf-8")),
                "sha256": _sha256_bytes(content.encode("utf-8")),
            }
            for name, content in sorted(
                {
                    "START_HERE.md": markdown,
                    "START_HERE.html": html_text,
                    **judge_artifacts,
                }.items()
            )
        },
        "source_artifacts": sources,
        "limitations": [
            "The benchmark is balanced, synthetic, and project-authored.",
            "Zero measured unsafe escapes does not establish universal correctness or formal verification.",
            f"Only {metrics['advanced_safe_approved']} of {metrics['safe_total']} safe candidates were automatically approved; {metrics['advanced_human_reviews']} cases were escalated.",
            "Replay verifies processing of recorded provider responses, not unseen-input generalization.",
        ],
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def expected_artifacts(root: Path = ROOT) -> tuple[dict[Path, str], str]:
    root = root.resolve()
    metrics = _metrics(root)
    judge_artifacts = build_judge_artifacts(root, metrics)
    markdown = _start_here_markdown(metrics)
    html_text = _start_here_html(metrics)
    manifest = pretty_json(_manifest(root, metrics, markdown, html_text, judge_artifacts)) + "\n"
    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8", errors="strict")
    updated_readme = _readme_with_metrics(readme, _metrics_markdown(metrics))
    artifacts = {
        root / OUTPUT_DIRECTORY / "START_HERE.md": markdown,
        root / OUTPUT_DIRECTORY / "START_HERE.html": html_text,
        root / OUTPUT_DIRECTORY / "manifest.json": manifest,
    }
    artifacts.update(
        {root / OUTPUT_DIRECTORY / name: content for name, content in judge_artifacts.items()}
    )
    return artifacts, updated_readme


def write_submission(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    artifacts, readme = expected_artifacts(root)
    for path, content in artifacts.items():
        atomic_write_text(path, content)
    atomic_write_text(root / "README.md", readme)
    return _receipt(root, artifacts)


def check_submission(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    artifacts, expected_readme = expected_artifacts(root)
    changed: list[str] = []
    for path, content in artifacts.items():
        if path.is_symlink() or not path.is_file():
            changed.append(path.relative_to(root).as_posix())
            continue
        if path.read_text(encoding="utf-8", errors="strict") != content:
            changed.append(path.relative_to(root).as_posix())
    readme_path = root / "README.md"
    readme_changed = readme_path.is_symlink() or not readme_path.is_file()
    if not readme_changed:
        readme_changed = readme_path.read_text(encoding="utf-8", errors="strict") != expected_readme
    if readme_changed:
        changed.append("README.md")
    if changed:
        raise SubmissionRenderError(
            "judge-facing submission artifacts differ from committed evidence: "
            + ", ".join(changed)
        )
    return _receipt(root, artifacts)


def _receipt(root: Path, artifacts: dict[Path, str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol": "driftproof.submission-render.v1",
        "verified": True,
        "files": {
            path.relative_to(root).as_posix(): {
                "bytes": len(content.encode("utf-8")),
                "sha256": _sha256_bytes(content.encode("utf-8")),
            }
            for path, content in sorted(artifacts.items(), key=lambda item: item[0].as_posix())
        },
        "readme_metrics_bound": True,
        "comparison_sha256": _sha256_file(root / "results/driftproof-comparison/comparison.json"),
        "human_approval_required": True,
        "consequential_action_taken": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render or verify judge-facing DriftProof submission artifacts from committed evidence."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = check_submission(args.root) if args.check else write_submission(args.root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
