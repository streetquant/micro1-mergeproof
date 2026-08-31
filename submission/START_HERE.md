# DriftProof submission — start here

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
| Safe-approval macro-F1 | 0.333 | 0.681 | +0.348 |
| Accuracy | 50.0% | 70.8% | +20.8 pp |
| Unsafe-repair escape rate | 100.0% | 0.0% | -100.0 pp |
| Safe candidates automatically approved | 12/12 | 5/12 | -7 |
| Unsafe candidates blocked from automatic approval | 0/12 | 12/12 | +12 |
| Qualified-human escalations | 0/24 | 7/24 | +7 |

DriftProof eliminated measured unsafe escapes on this frozen benchmark, but it is intentionally conservative: only 5 of 12 safe candidates were automatically approved and 7 cases were escalated. The benchmark is balanced, synthetic, and project-authored; it is not evidence of universal correctness, formal verification, or unseen-project generalization.

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
