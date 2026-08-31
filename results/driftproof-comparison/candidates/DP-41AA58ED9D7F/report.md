# DriftProof review: DP-41AA58ED9D7F

- **Verdict:** `approve`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `ba14d73c6bd10df5c252ab18293c8f625978132219d0a76ebc599453f6e9881e`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate built successfully and every compiled visible contract check passed. A qualified human must still authorize any merge or deployment.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`e9c08f885610acdca71c04871f234f3527ad8181980f69983fd6ab2829256c5b` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `e9c08f885610acdca71c04871f234f3527ad8181980f69983fd6ab2829256c5b`<br>`e9c08f885610acdca71c04871f234f3527ad8181980f69983fd6ab2829256c5b` |
| `pass` | **Visible business context compiled into executable checks** (`C-A8E7CDE3332B`) | Compiled 2 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`069da61c411f37b6cddf2c161186c0659ccb8af5bb46d262e6df35d729ca8b29` |
| `pass` | **Public output contract is represented** (`C-A2F47CA3D9BB`) | All documented fields are projected by the candidate. | `models/mart_customers.sql`<br>`models/stg_customers.sql` |
| `pass` | **Documented derived text expression is exact** (`C-D8FBE1EBA346`) | display_name uses trim, both source fields, and the required separator. | `models/stg_customers.sql` |
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

- Candidate tree SHA-256: `e9c08f885610acdca71c04871f234f3527ad8181980f69983fd6ab2829256c5b`
- Business context SHA-256: `069da61c411f37b6cddf2c161186c0659ccb8af5bb46d262e6df35d729ca8b29`
- Disposable worktree SHA-256: `e9c08f885610acdca71c04871f234f3527ad8181980f69983fd6ab2829256c5b`
- Certificate SHA-256: `ba14d73c6bd10df5c252ab18293c8f625978132219d0a76ebc599453f6e9881e`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
