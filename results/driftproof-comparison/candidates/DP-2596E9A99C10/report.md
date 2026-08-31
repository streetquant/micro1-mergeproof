# DriftProof review: DP-2596E9A99C10

- **Verdict:** `human_review`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `c609ba88d3d9d4244a72d478166eb681f26a8e35a4f058b2a343add217edd201`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate requires qualified human review because the visible contract could not be verified conclusively: C-56E06F939337.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`8619e23a747063d3d086c1a8f1eb7392f19a8e4b9edab4292fc7062c27b9354b` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `8619e23a747063d3d086c1a8f1eb7392f19a8e4b9edab4292fc7062c27b9354b`<br>`8619e23a747063d3d086c1a8f1eb7392f19a8e4b9edab4292fc7062c27b9354b` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`c8d19fa781c89ba0b30e3ecaa493097053be051155b3cfb5a219734b0e4bee0d` |
| `pass` | **Latest-record selection follows the greatest documented timestamp** (`C-4ADCF4AB36B0`) | A descending updated_at window selection is visible. | `models/current_customers.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-56E06F939337`) | 1 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`c8d19fa781c89ba0b30e3ecaa493097053be051155b3cfb5a219734b0e4bee0d` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `latest_record` | `R-3AE7216508EE` | If multiple records exist, choose the row with the greatest `updated_at`. | `customer_id, updated_at` |

### Unresolved context

- The model contract is exactly one current row per `customer_id`.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `8619e23a747063d3d086c1a8f1eb7392f19a8e4b9edab4292fc7062c27b9354b`
- Business context SHA-256: `c8d19fa781c89ba0b30e3ecaa493097053be051155b3cfb5a219734b0e4bee0d`
- Disposable worktree SHA-256: `8619e23a747063d3d086c1a8f1eb7392f19a8e4b9edab4292fc7062c27b9354b`
- Certificate SHA-256: `c609ba88d3d9d4244a72d478166eb681f26a8e35a4f058b2a343add217edd201`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
