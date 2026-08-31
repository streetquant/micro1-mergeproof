# DriftProof review: DP-F4246B8D029D

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `1f12cb9404ef1a05200ebf395d74beed609f455d703f9bf7ad1399b9e2fc09f8`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-67216231F6B9.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`8da011ad805ca6e349136f2c7b52849385ab0610e7d6efe348eb8a9dce3bc4b5` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `8da011ad805ca6e349136f2c7b52849385ab0610e7d6efe348eb8a9dce3bc4b5`<br>`8da011ad805ca6e349136f2c7b52849385ab0610e7d6efe348eb8a9dce3bc4b5` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`84ad2c20f8d391c39485233d70f8fc5bfc47cd2a3c936c95bdc31efabc545710` |
| `fail` | **Documented subtraction formula retains a negative refund term** (`C-67216231F6B9`) | No subtractive expression produces net_revenue. | `models/revenue_summary.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-56E06F939337`) | 1 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`84ad2c20f8d391c39485233d70f8fc5bfc47cd2a3c936c95bdc31efabc545710` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `subtraction_formula` | `R-D1481008C300` | `gross_sales` is the sum of sales, `refunds` is the positive refund magnitude, and `net_revenue = sales - refunds`. | `net_revenue, sales, refunds` |

### Unresolved context

- Source refund amounts are stored as positive magnitudes.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `8da011ad805ca6e349136f2c7b52849385ab0610e7d6efe348eb8a9dce3bc4b5`
- Business context SHA-256: `84ad2c20f8d391c39485233d70f8fc5bfc47cd2a3c936c95bdc31efabc545710`
- Disposable worktree SHA-256: `8da011ad805ca6e349136f2c7b52849385ab0610e7d6efe348eb8a9dce3bc4b5`
- Certificate SHA-256: `1f12cb9404ef1a05200ebf395d74beed609f455d703f9bf7ad1399b9e2fc09f8`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
