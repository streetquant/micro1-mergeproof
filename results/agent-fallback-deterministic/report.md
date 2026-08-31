# DriftProof review: DP-AGENT-FALLBACK-UNSAFE

- **Verdict:** `human_review`
- **Build isolation:** `disposable_copy`
- **Build return code:** `0`
- **Certificate:** `8679da417c604d683c5d126b9ddb66f1355a13e37de7353e7235706b73923bc7`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate requires qualified human review because the visible contract could not be verified conclusively: C-19CFACF0103C, C-CF6D36F15526.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-046C625A4D7B`) | dbt returned 0 under disposable_copy isolation. | `dbt build`<br>`1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735`<br>`1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735` |
| `inconclusive` | **At least one visible business contract was compiled** (`C-19CFACF0103C`) | No supported machine-verifiable rule could be derived from the supplied context. | `BUSINESS_CONTEXT.md` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-CF6D36F15526`) | 2 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`fe3ec5bb473225fad8be26266ab80d538e2d195b329b129a978adaeb13744865` |

## Visible contract

No machine-verifiable rule was admitted from the supplied context.

### Unresolved context

- Finance policy treats refunded cash as a deduction from booked sales.
- The published columns are `sales`, `refunds`, and `net_revenue`.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735`
- Business context SHA-256: `fe3ec5bb473225fad8be26266ab80d538e2d195b329b129a978adaeb13744865`
- Disposable worktree SHA-256: `1107fbbc6ab00b861f65007aaee5d66ed0010b205beeb959604111ddeafcf735`
- Certificate SHA-256: `8679da417c604d683c5d126b9ddb66f1355a13e37de7353e7235706b73923bc7`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
