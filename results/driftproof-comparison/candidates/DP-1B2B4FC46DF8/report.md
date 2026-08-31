# DriftProof review: DP-1B2B4FC46DF8

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `975f31e48f7848c3955158298bb7830e527c7fe50e6d897763c9ff06250e61eb`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-D8FBE1EBA346.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`d7e91032750b3af00bfff8caa3246ad530cfee685a212d49151042ec1aa21a52` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `d7e91032750b3af00bfff8caa3246ad530cfee685a212d49151042ec1aa21a52`<br>`d7e91032750b3af00bfff8caa3246ad530cfee685a212d49151042ec1aa21a52` |
| `pass` | **Visible business context compiled into executable checks** (`C-A8E7CDE3332B`) | Compiled 2 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`069da61c411f37b6cddf2c161186c0659ccb8af5bb46d262e6df35d729ca8b29` |
| `pass` | **Public output contract is represented** (`C-A2F47CA3D9BB`) | All documented fields are projected by the candidate. | `models/mart_customers.sql`<br>`models/stg_customers.sql` |
| `fail` | **Documented derived text expression is exact** (`C-D8FBE1EBA346`) | No expression for display_name contains trim(first_name + ' ' + last_name). | `models/stg_customers.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `public_contract` | `R-5D732A1DAB5A` | The public customer model must expose `customer_id`, `display_name`, and `revenue_amount`. | `customer_id, display_name, revenue_amount` |
| `derived_concat` | `R-032CBBF6D9D8` | `display_name` is the trimmed concatenation of `first_name`, a single space, and `last_name` | `display_name, first_name, last_name` |

### Unresolved context

- None

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `d7e91032750b3af00bfff8caa3246ad530cfee685a212d49151042ec1aa21a52`
- Business context SHA-256: `069da61c411f37b6cddf2c161186c0659ccb8af5bb46d262e6df35d729ca8b29`
- Disposable worktree SHA-256: `d7e91032750b3af00bfff8caa3246ad530cfee685a212d49151042ec1aa21a52`
- Certificate SHA-256: `975f31e48f7848c3955158298bb7830e527c7fe50e6d897763c9ff06250e61eb`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
