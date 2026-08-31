# DriftProof review: DP-6AA24B281651

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `3d2ca6ceedd0a98a2d11967eb9396b5ab5f1d346ef1561b4c6a41ef0052700a0`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-2508236DFBEB.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`01869673c27d4c253ea1bf12b0391464f73654fbfa31a3807603010d1208a81a` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `01869673c27d4c253ea1bf12b0391464f73654fbfa31a3807603010d1208a81a`<br>`01869673c27d4c253ea1bf12b0391464f73654fbfa31a3807603010d1208a81a` |
| `pass` | **Visible business context compiled into executable checks** (`C-829C865DC1D5`) | Compiled 3 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`d3de6def201fb1f45a368fb635c81036488fb7ff4da089daf306c6e2d34b7d39` |
| `pass` | **Public output contract is represented** (`C-E7A37014CC66`) | All documented fields are projected by the candidate. | `models/mart_customer_revenue.sql`<br>`models/stg_customer_revenue.sql` |
| `pass` | **Renamed source field is preserved through an alias** (`C-8FA8B51E5395`) | client_name is visibly aliased to customer_name. | `models/stg_customer_revenue.sql` |
| `fail` | **Invalid numeric input follows the documented NULL policy** (`C-2508236DFBEB`) | The numeric conversion is missing or replaces invalid values instead of preserving NULL. | `models/stg_customer_revenue.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `public_contract` | `R-626688F98AA7` | The public mart contract is `customer_id`, `customer_name`, `revenue_amount`. | `customer_id, customer_name, revenue_amount` |
| `source_alias` | `R-CD72C5C28B55` | The upstream name field may have changed. | `customer_name` |
| `numeric_null_policy` | `R-553C6652E7F9` | Revenue now arrives as text: valid numeric text becomes DECIMAL and invalid text becomes NULL; invalid values must not be silently coerced to zero or discarded. | `revenue_amount` |

### Unresolved context

- None

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `01869673c27d4c253ea1bf12b0391464f73654fbfa31a3807603010d1208a81a`
- Business context SHA-256: `d3de6def201fb1f45a368fb635c81036488fb7ff4da089daf306c6e2d34b7d39`
- Disposable worktree SHA-256: `01869673c27d4c253ea1bf12b0391464f73654fbfa31a3807603010d1208a81a`
- Certificate SHA-256: `3d2ca6ceedd0a98a2d11967eb9396b5ab5f1d346ef1561b4c6a41ef0052700a0`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
