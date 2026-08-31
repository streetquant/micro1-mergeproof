# DriftProof submission — start here

DriftProof is an independent, fail-closed release gate for agent-authored dbt repairs. It checks a visible business contract in a networkless disposable worktree and publishes a self-verifying bundle for a qualified human. It never merges or deploys code.

## 60-second judge path

```bash
uv sync --locked --extra dev --extra dbt
make judge-demo
```

Both transparent fixtures pass the same build-only `dbt build`. DriftProof approves the contract-preserving fixture and rejects the green-but-wrong fixture, independently verifies both bundles, and prints the HTML report paths plus a machine receipt.

For complete source qualification:

```bash
make check
```

For a deterministic release set from clean private `main`:

```bash
make submission-check
make release
```

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

from driftproof.sdk import ReviewRequest, fingerprint_for_agent, review_for_agent

request = ReviewRequest(project="candidate", context="candidate/BUSINESS_CONTEXT.md")
identity = fingerprint_for_agent(request, base_dir=Path.cwd())
response = review_for_agent(request, base_dir=Path.cwd())
assert response.request_sha256 == identity.configuration_request_sha256
raise SystemExit(response.exit_code)
```

The typed SDK validates the one-object protocol, rejects malformed output and process/response disagreement, and assigns disjoint control run IDs to independent concurrent callers. Machine contracts are discoverable through `driftproof capabilities` and [`../schemas/driftproof/`](../schemas/driftproof/).

## Evidence map

- Authoritative comparison: [`../results/driftproof-comparison/comparison.json`](../results/driftproof-comparison/comparison.json)
- Candidate reports and raw predictions: [`../results/driftproof-comparison/`](../results/driftproof-comparison/)
- Benchmark validation: [`../results/driftproof-benchmark-validation/summary.json`](../results/driftproof-benchmark-validation/summary.json)
- Human/judge adversarial review: [`../reviews/2026-08-31-round-1-human-judge/`](../reviews/2026-08-31-round-1-human-judge/)
- AI-agent/SDK adversarial review: [`../reviews/2026-08-31-round-2-agent-sdk/`](../reviews/2026-08-31-round-2-agent-sdk/)
- Release/delivery adversarial review: [`../reviews/2026-08-31-round-3-release-delivery/`](../reviews/2026-08-31-round-3-release-delivery/)
- Downloaded-release consumer review: [`../reviews/2026-08-31-round-4-consumer-verifier/`](../reviews/2026-08-31-round-4-consumer-verifier/)
- Machine-readable submission manifest: [`manifest.json`](manifest.json)
- Full product and trust-boundary documentation: [`../README.md`](../README.md)

## Fixed safety boundary

- `human_approval_required` is always `true`.
- `consequential_action_taken` is always `false`.
- An approval certificate is decision support, never authorization to merge, deploy, publish, notify, or delete.
