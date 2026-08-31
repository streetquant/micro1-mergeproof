# DriftProof review: DP-7E6835344182

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `7c2a6de0fd092130a157c877b7440327f85981fd405c9345bde52ba0a5beed7a`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-88B26083405B.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`8293237c98d417fd185f3eb0e84f7efe5ed293064b474fec21bb6f18c2b2d24c` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `8293237c98d417fd185f3eb0e84f7efe5ed293064b474fec21bb6f18c2b2d24c`<br>`8293237c98d417fd185f3eb0e84f7efe5ed293064b474fec21bb6f18c2b2d24c` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`732f7c4b698ecb118aa8926c623a9c91922c2a7655e1d340c64aef1e4c2ff856` |
| `fail` | **Invalid numeric input follows the documented NULL policy** (`C-88B26083405B`) | The numeric conversion is missing or replaces invalid values instead of preserving NULL. | `models/stg_orders.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `numeric_null_policy` | `R-A8F67D835778` | `amount` is numeric downstream. Source text that is valid numeric data should be converted to DECIMAL. Invalid numeric text must become NULL rather than be coerced to zero or dropped. | `amount` |

### Unresolved context

- None

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `8293237c98d417fd185f3eb0e84f7efe5ed293064b474fec21bb6f18c2b2d24c`
- Business context SHA-256: `732f7c4b698ecb118aa8926c623a9c91922c2a7655e1d340c64aef1e4c2ff856`
- Disposable worktree SHA-256: `8293237c98d417fd185f3eb0e84f7efe5ed293064b474fec21bb6f18c2b2d24c`
- Certificate SHA-256: `7c2a6de0fd092130a157c877b7440327f85981fd405c9345bde52ba0a5beed7a`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
