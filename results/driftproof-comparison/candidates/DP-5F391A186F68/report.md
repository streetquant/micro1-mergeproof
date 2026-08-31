# DriftProof review: DP-5F391A186F68

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `a785bc5a8b243338edcf7a7f63b492d588baad9b5f63ce27aea4a88113d05120`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-4ADCF4AB36B0.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`6a15e8cb9023e818d5d2141c3a960ce4d7d8e4b6398be0c3dec832fa5260987e` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `6a15e8cb9023e818d5d2141c3a960ce4d7d8e4b6398be0c3dec832fa5260987e`<br>`6a15e8cb9023e818d5d2141c3a960ce4d7d8e4b6398be0c3dec832fa5260987e` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`c8d19fa781c89ba0b30e3ecaa493097053be051155b3cfb5a219734b0e4bee0d` |
| `fail` | **Latest-record selection follows the greatest documented timestamp** (`C-4ADCF4AB36B0`) | Expected a latest-record operator ordered by updated_at DESC. | `models/current_customers.sql` |
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

- Candidate tree SHA-256: `6a15e8cb9023e818d5d2141c3a960ce4d7d8e4b6398be0c3dec832fa5260987e`
- Business context SHA-256: `c8d19fa781c89ba0b30e3ecaa493097053be051155b3cfb5a219734b0e4bee0d`
- Disposable worktree SHA-256: `6a15e8cb9023e818d5d2141c3a960ce4d7d8e4b6398be0c3dec832fa5260987e`
- Certificate SHA-256: `a785bc5a8b243338edcf7a7f63b492d588baad9b5f63ce27aea4a88113d05120`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
