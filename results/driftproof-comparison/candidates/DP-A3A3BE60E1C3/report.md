# DriftProof review: DP-A3A3BE60E1C3

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `d1614fd23613d96a6dd5c9c19371ac5f4eb05c7de1393bec588d5641d2ddf106`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-07FC77401F1B.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`d142d852744768a67d0616c97b53691336b5ab0e3187901ca8650a6a965eff26` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `d142d852744768a67d0616c97b53691336b5ab0e3187901ca8650a6a965eff26`<br>`d142d852744768a67d0616c97b53691336b5ab0e3187901ca8650a6a965eff26` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`2caff91ad4a0b18c5b3c23db4d9935a13abc321b3393e15deffa2f7cb7017d96` |
| `fail` | **Latest-record selection follows the greatest documented timestamp** (`C-07FC77401F1B`) | Expected a latest-record operator ordered by effective_at DESC. | `models/fct_revenue.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-CF6D36F15526`) | 2 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`2caff91ad4a0b18c5b3c23db4d9935a13abc321b3393e15deffa2f7cb7017d96` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `latest_record` | `R-2027CE509E44` | Customer tier is a slowly changing attribute; when multiple tier records exist, use the record with the greatest `effective_at` for that customer. | `order_id, effective_at` |

### Unresolved context

- The finance fact grain is one row per `order_id`.
- Revenue must never be multiplied by dimension history.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `d142d852744768a67d0616c97b53691336b5ab0e3187901ca8650a6a965eff26`
- Business context SHA-256: `2caff91ad4a0b18c5b3c23db4d9935a13abc321b3393e15deffa2f7cb7017d96`
- Disposable worktree SHA-256: `d142d852744768a67d0616c97b53691336b5ab0e3187901ca8650a6a965eff26`
- Certificate SHA-256: `d1614fd23613d96a6dd5c9c19371ac5f4eb05c7de1393bec588d5641d2ddf106`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
