# DriftProof review: DP-1356AA7EA9E1

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `76f0298205c161ffb3fefb1523421daaf718b3c430c7bcb4ac8fcb25c79d3dab`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-75CBA4CDAE8A.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`8d93d8ce327cbbdfc53df481f62a7d02f70c878a8385fdabc1c358e4a3dd70b5` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `8d93d8ce327cbbdfc53df481f62a7d02f70c878a8385fdabc1c358e4a3dd70b5`<br>`8d93d8ce327cbbdfc53df481f62a7d02f70c878a8385fdabc1c358e4a3dd70b5` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`89e1b61bd32efdce140ac8fbc1f064df7c9b44609f159fc9f735725c20302cf0` |
| `fail` | **Macro call uses the current documented keyword and value** (`C-75CBA4CDAE8A`) | Did not observe scale=100. | `macros/normalize_currency.sql`<br>`models/stg_payments.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-CF6D36F15526`) | 2 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`89e1b61bd32efdce140ac8fbc1f064df7c9b44609f159fc9f735725c20302cf0` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `macro_keyword` | `R-0823CA35294C` | The shared currency macro now accepts a required expression plus keyword argument `scale`. | `scale` |

### Unresolved context

- Payment cents should be normalized to dollars with `scale=100`.
- Do not change the macro back to its old interface.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `8d93d8ce327cbbdfc53df481f62a7d02f70c878a8385fdabc1c358e4a3dd70b5`
- Business context SHA-256: `89e1b61bd32efdce140ac8fbc1f064df7c9b44609f159fc9f735725c20302cf0`
- Disposable worktree SHA-256: `8d93d8ce327cbbdfc53df481f62a7d02f70c878a8385fdabc1c358e4a3dd70b5`
- Certificate SHA-256: `76f0298205c161ffb3fefb1523421daaf718b3c430c7bcb4ac8fcb25c79d3dab`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
