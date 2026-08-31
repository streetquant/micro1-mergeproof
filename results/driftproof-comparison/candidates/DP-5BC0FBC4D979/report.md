# DriftProof review: DP-5BC0FBC4D979

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `efd557136963ab5b246c81b47a986337aac203c0c8e6a1954c21932a20972d2f`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-5F8FEDFE59ED.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`06cff5afd26a79f40882bddb3190023a2bbfe8a46bcf208b9519e6369c61ec8d` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `06cff5afd26a79f40882bddb3190023a2bbfe8a46bcf208b9519e6369c61ec8d`<br>`06cff5afd26a79f40882bddb3190023a2bbfe8a46bcf208b9519e6369c61ec8d` |
| `pass` | **Visible business context compiled into executable checks** (`C-A8E7CDE3332B`) | Compiled 2 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`4794059279b492dc93f2b3b064fa43fd996d6bd12994f3a502c63fa25a4d3eff` |
| `pass` | **Public output contract is represented** (`C-E7A37014CC66`) | All documented fields are projected by the candidate. | `models/mart_customer_revenue.sql`<br>`models/stg_customers.sql` |
| `fail` | **Renamed source field is preserved through an alias** (`C-5F8FEDFE59ED`) | The unique semantic source field full_name is not used to produce customer_name. | `models/mart_customer_revenue.sql`<br>`models/stg_customers.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `public_contract` | `R-626688F98AA7` | The public mart contract is `customer_id`, `customer_name`, `revenue_amount`. | `customer_id, customer_name, revenue_amount` |
| `source_alias` | `R-EDFECDF02EA4` | Upstream renamed the human-readable customer field, but downstream names must remain stable. | `customer_name` |

### Unresolved context

- None

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `06cff5afd26a79f40882bddb3190023a2bbfe8a46bcf208b9519e6369c61ec8d`
- Business context SHA-256: `4794059279b492dc93f2b3b064fa43fd996d6bd12994f3a502c63fa25a4d3eff`
- Disposable worktree SHA-256: `06cff5afd26a79f40882bddb3190023a2bbfe8a46bcf208b9519e6369c61ec8d`
- Certificate SHA-256: `efd557136963ab5b246c81b47a986337aac203c0c8e6a1954c21932a20972d2f`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
