# DriftProof review: DP-7898CF99DC3F

- **Verdict:** `human_review`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `c9def16e381f562ce9a6c5ea312cfefce313f562df1ff6e20a4a5b354ea9fd1f`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate requires qualified human review because the visible contract could not be verified conclusively: C-CF6D36F15526.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`90353cf56da62950cd2b4bd2df1f0ad01c24b49f7c70436c4eec684a10a621fa` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `90353cf56da62950cd2b4bd2df1f0ad01c24b49f7c70436c4eec684a10a621fa`<br>`90353cf56da62950cd2b4bd2df1f0ad01c24b49f7c70436c4eec684a10a621fa` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`89e1b61bd32efdce140ac8fbc1f064df7c9b44609f159fc9f735725c20302cf0` |
| `pass` | **Macro call uses the current documented keyword and value** (`C-75CBA4CDAE8A`) | Observed scale=100. | `macros/normalize_currency.sql`<br>`models/stg_payments.sql` |
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

- Candidate tree SHA-256: `90353cf56da62950cd2b4bd2df1f0ad01c24b49f7c70436c4eec684a10a621fa`
- Business context SHA-256: `89e1b61bd32efdce140ac8fbc1f064df7c9b44609f159fc9f735725c20302cf0`
- Disposable worktree SHA-256: `90353cf56da62950cd2b4bd2df1f0ad01c24b49f7c70436c4eec684a10a621fa`
- Certificate SHA-256: `c9def16e381f562ce9a6c5ea312cfefce313f562df1ff6e20a4a5b354ea9fd1f`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
