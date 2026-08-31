# DriftProof review: DP-69B90162904D

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `d7b42bbd0fa155b4fb2b77795de8a755ce41e1eca8276d6697403d83f6fe9302`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-2037A54A2115.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`47ef67ddf81139bf0871ab399688abcfad52b03dadb8e52e854f619381b8721f` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `47ef67ddf81139bf0871ab399688abcfad52b03dadb8e52e854f619381b8721f`<br>`47ef67ddf81139bf0871ab399688abcfad52b03dadb8e52e854f619381b8721f` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`61c151c84cfae4cb348b7372e28ee5339e1ce373b610124139d962a8372fbf9f` |
| `fail` | **Required identifier rejects NULL, empty, and whitespace-only values** (`C-2037A54A2115`) | customer_id is not protected by a trimmed empty-value filter. | `models/stg_customers.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-56E06F939337`) | 1 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`61c151c84cfae4cb348b7372e28ee5339e1ce373b610124139d962a8372fbf9f` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `required_identifier` | `R-FA36F892BF35` | `customer_id` is required. | `customer_id, customer_id` |

### Unresolved context

- Source rows with NULL, empty, or whitespace-only customer IDs are invalid and must be excluded; do not invent replacement identifiers.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `47ef67ddf81139bf0871ab399688abcfad52b03dadb8e52e854f619381b8721f`
- Business context SHA-256: `61c151c84cfae4cb348b7372e28ee5339e1ce373b610124139d962a8372fbf9f`
- Disposable worktree SHA-256: `47ef67ddf81139bf0871ab399688abcfad52b03dadb8e52e854f619381b8721f`
- Certificate SHA-256: `d7b42bbd0fa155b4fb2b77795de8a755ce41e1eca8276d6697403d83f6fe9302`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
