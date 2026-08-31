# DriftProof review: DP-AGENT-FALLBACK-UNSAFE

- **Verdict:** `human_review`
- **Build isolation:** `disposable_copy`
- **Build return code:** `0`
- **Certificate:** `4292a30f93a74f18d37dea356110e58d65a7a36911c61a5bc2585e09e76f9ad7`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate requires qualified human review because the visible contract could not be verified conclusively: C-5F67A34A2097, C-56E06F939337.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-046C625A4D7B`) | dbt returned 0 under disposable_copy isolation. | `dbt build`<br>`1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735`<br>`1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735` |
| `inconclusive` | **Bounded Contract Clarifier produced only admitted typed rules** (`C-5F67A34A2097`) | Accepted 1 typed rules; rejected 1 proposals; left 1 sentences unresolved. | `8048ba79613d4758495aee5c4e0cc11eed6f2f459b85510d3903512e8c042080`<br>`R-26A199356AD7` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`fe3ec5bb473225fad8be26266ab80d538e2d195b329b129a978adaeb13744865` |
| `pass` | **Public output contract is represented** (`C-C9296209BFA0`) | All documented fields are projected by the candidate. | `models/revenue.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | — |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-56E06F939337`) | 1 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`fe3ec5bb473225fad8be26266ab80d538e2d195b329b129a978adaeb13744865` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `public_contract` | `R-26A199356AD7` | The published columns are `sales`, `refunds`, and `net_revenue`. | `sales, refunds, net_revenue` |

### Unresolved context

- Finance policy treats refunded cash as a deduction from booked sales.

## Bounded agent trace

- Provider: `replay`
- Model: `openai/gpt-oss-20b`
- Request hash: `8048ba79613d4758495aee5c4e0cc11eed6f2f459b85510d3903512e8c042080`
- Accepted rules: `1`
- Rejected proposals: `1`
- Remaining unresolved sentences: `1`

## Integrity and execution boundary

- Candidate tree SHA-256: `1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735`
- Business context SHA-256: `fe3ec5bb473225fad8be26266ab80d538e2d195b329b129a978adaeb13744865`
- Disposable worktree SHA-256: `1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735`
- Certificate SHA-256: `4292a30f93a74f18d37dea356110e58d65a7a36911c61a5bc2585e09e76f9ad7`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
