# DriftProof review: DP-E6C082649625

- **Verdict:** `human_review`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `1862e1c5013d51bdbb4b4e343e8ad40df5fa42d35f89368732534fb9e851de72`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate requires qualified human review because the visible contract could not be verified conclusively: C-CF6D36F15526.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`a16141b948b42208d441164149ce079e9de67f24ba77d34b312c8d381b36b6c8` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `a16141b948b42208d441164149ce079e9de67f24ba77d34b312c8d381b36b6c8`<br>`a16141b948b42208d441164149ce079e9de67f24ba77d34b312c8d381b36b6c8` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`3c4d26bc7bfff61c208e7cf969543a42820b914ecf6a6552a41cce3b1db2d3d5` |
| `pass` | **Timezone conversion occurs before DATE truncation in the documented direction** (`C-651BBF8C0EBB`) | Observed UTC to Asia/Kolkata before DATE casting. | `models/daily_events.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-CF6D36F15526`) | 2 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`3c4d26bc7bfff61c208e7cf969543a42820b914ecf6a6552a41cce3b1db2d3d5` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `timezone_date` | `R-C5F8ED92B4FE` | Reporting dates use Asia/Kolkata local calendar dates. | `—` |

### Unresolved context

- Source timestamps are UTC.
- Convert from UTC to Asia/Kolkata before casting to DATE.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `a16141b948b42208d441164149ce079e9de67f24ba77d34b312c8d381b36b6c8`
- Business context SHA-256: `3c4d26bc7bfff61c208e7cf969543a42820b914ecf6a6552a41cce3b1db2d3d5`
- Disposable worktree SHA-256: `a16141b948b42208d441164149ce079e9de67f24ba77d34b312c8d381b36b6c8`
- Certificate SHA-256: `1862e1c5013d51bdbb4b4e343e8ad40df5fa42d35f89368732534fb9e851de72`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
