# DriftProof review: DP-CFDEACDE53B7

- **Verdict:** `approve`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `b3feb7b4e0d6366f2fb6d3cba5e5c654843bac8d0fba9db3663eab0a20dd5d2c`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate built successfully and every compiled visible contract check passed. A qualified human must still authorize any merge or deployment.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`184133e0bb1bb5d4a7826ad47d7ac491f8577276b733dacf0c4538c39f44ab33` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `184133e0bb1bb5d4a7826ad47d7ac491f8577276b733dacf0c4538c39f44ab33`<br>`184133e0bb1bb5d4a7826ad47d7ac491f8577276b733dacf0c4538c39f44ab33` |
| `pass` | **Visible business context compiled into executable checks** (`C-98B308A9B658`) | Compiled 5 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`b0bf4e1a92c8cc49707123c9fb125afa7a9f2b1dba314999c283c35730937e4d` |
| `pass` | **Public output contract is represented** (`C-3829DDC1E5FC`) | All documented fields are projected by the candidate. | `models/mart_orders.sql`<br>`models/stg_orders_v2.sql` |
| `pass` | **All dbt model references resolve to observed models** (`C-5B64FCC1DA2D`) | No stale dbt refs remain. | `models/mart_orders.sql`<br>`models/stg_orders_v2.sql` |
| `pass` | **Unchanged contract field remains a pass-through** (`C-874261405A14`) | Preserved field order_id remains a direct pass-through. | `models/mart_orders.sql` |
| `pass` | **Unchanged contract field remains a pass-through** (`C-CCEAADAF69E7`) | Preserved field customer_id remains a direct pass-through. | `models/mart_orders.sql` |
| `pass` | **Unchanged contract field remains a pass-through** (`C-156EDFDCAA9B`) | Preserved field amount remains a direct pass-through. | `models/mart_orders.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `public_contract` | `R-7EB5DBCDC415` | The mart contract remains `order_id`, `customer_id`, `amount`. | `order_id, customer_id, amount` |
| `dependency_exists` | `R-16A81136654D` | The staging model was renamed during the refactor; use the current staging model rather than recreating the removed name. | `—` |
| `preserve_field` | `R-F6DB984A3D13` | The mart contract remains `order_id`, `customer_id`, `amount`. | `order_id, order_id` |
| `preserve_field` | `R-BE2319C80449` | The mart contract remains `order_id`, `customer_id`, `amount`. | `customer_id, customer_id` |
| `preserve_field` | `R-EB34F86B9D9A` | The mart contract remains `order_id`, `customer_id`, `amount`. | `amount, amount` |

### Unresolved context

- None

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `184133e0bb1bb5d4a7826ad47d7ac491f8577276b733dacf0c4538c39f44ab33`
- Business context SHA-256: `b0bf4e1a92c8cc49707123c9fb125afa7a9f2b1dba314999c283c35730937e4d`
- Disposable worktree SHA-256: `184133e0bb1bb5d4a7826ad47d7ac491f8577276b733dacf0c4538c39f44ab33`
- Certificate SHA-256: `b3feb7b4e0d6366f2fb6d3cba5e5c654843bac8d0fba9db3663eab0a20dd5d2c`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
