# DriftProof review: DP-DF8B15B5A6CC

- **Verdict:** `human_review`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `f03f6d505439218d625509b373f04a11fee2f17db64f015adf97c79f8489a6ab`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate requires qualified human review because the visible contract could not be verified conclusively: C-56E06F939337.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`2b27d8fc3efa6700aa1cc2cd43e606a28cec10bbf9a68b0471de6ca38ce9b0bb` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `2b27d8fc3efa6700aa1cc2cd43e606a28cec10bbf9a68b0471de6ca38ce9b0bb`<br>`2b27d8fc3efa6700aa1cc2cd43e606a28cec10bbf9a68b0471de6ca38ce9b0bb` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`84ad2c20f8d391c39485233d70f8fc5bfc47cd2a3c936c95bdc31efabc545710` |
| `pass` | **Documented subtraction formula retains a negative refund term** (`C-67216231F6B9`) | net_revenue contains an explicit subtraction/negative term. | `models/revenue_summary.sql` |
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

- Candidate tree SHA-256: `2b27d8fc3efa6700aa1cc2cd43e606a28cec10bbf9a68b0471de6ca38ce9b0bb`
- Business context SHA-256: `84ad2c20f8d391c39485233d70f8fc5bfc47cd2a3c936c95bdc31efabc545710`
- Disposable worktree SHA-256: `2b27d8fc3efa6700aa1cc2cd43e606a28cec10bbf9a68b0471de6ca38ce9b0bb`
- Certificate SHA-256: `f03f6d505439218d625509b373f04a11fee2f17db64f015adf97c79f8489a6ab`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
