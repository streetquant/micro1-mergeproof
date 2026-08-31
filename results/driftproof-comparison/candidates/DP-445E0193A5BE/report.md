# DriftProof review: DP-445E0193A5BE

- **Verdict:** `reject`
- **Build isolation:** `bubblewrap`
- **Build return code:** `0`
- **Certificate:** `20def177b8fcb890b90bca3af23c07d46f78bf8c8849a6a65afced1a9ca32868`

> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.

## Summary

Candidate is not approval-ready; failed checks: C-A5A72F0D1591.

## Verification checks

| Status | Check | Detail | Evidence |
|---|---|---|---|
| `pass` | **Candidate builds from the disposable review worktree** (`C-C0B46176F241`) | dbt returned 0 under bubblewrap isolation. | `dbt build`<br>`14548b60f76f1752b03972a0ae29ff90eba64ed81cd88e7f351a8b4dc7fd5430` |
| `pass` | **Original candidate project remained unchanged** (`C-2EE4ED4DFCAA`) | The source tree hash is unchanged. | `14548b60f76f1752b03972a0ae29ff90eba64ed81cd88e7f351a8b4dc7fd5430`<br>`14548b60f76f1752b03972a0ae29ff90eba64ed81cd88e7f351a8b4dc7fd5430` |
| `pass` | **Visible business context compiled into executable checks** (`C-AB2C57D37E34`) | Compiled 1 rules without using an external benchmark oracle. | `BUSINESS_CONTEXT.md`<br>`508b5af714676ab5d16ea0576cae8015864cf1b6b43d8784f85ac77f4cc131e7` |
| `fail` | **Categorical mapping and validation match the documented table** (`C-A5A72F0D1591`) | Mapping logic or accepted-values validation is incomplete or incorrect. | `models/schema.yml`<br>`models/sources.yml`<br>`models/stg_order_status.sql` |
| `pass` | **Verification controls were not weakened** (`C-3A141B290B9B`) | No disabled or warning-only validation controls were found. | `models/schema.yml`<br>`models/sources.yml` |
| `inconclusive` | **Every visible business statement was resolved or verified** (`C-56E06F939337`) | 1 visible business statements remain unresolved; approval is not permitted. | `BUSINESS_CONTEXT.md`<br>`508b5af714676ab5d16ea0576cae8015864cf1b6b43d8784f85ac77f4cc131e7` |

## Visible contract

| Kind | Rule ID | Source | Output / fields |
|---|---|---|---|
| `categorical_mapping` | `R-8061F5A1C387` | Business status mapping is: `paid -> revenue`, `refunded -> refund`, `chargeback -> loss`. | `paid, revenue, refunded, refund, chargeback, loss` |

### Unresolved context

- Validation must explicitly allow exactly the mapped business statuses rather than being removed.

## Bounded agent trace

No model agent was invoked.

## Integrity and execution boundary

- Candidate tree SHA-256: `14548b60f76f1752b03972a0ae29ff90eba64ed81cd88e7f351a8b4dc7fd5430`
- Business context SHA-256: `508b5af714676ab5d16ea0576cae8015864cf1b6b43d8784f85ac77f4cc131e7`
- Disposable worktree SHA-256: `14548b60f76f1752b03972a0ae29ff90eba64ed81cd88e7f351a8b4dc7fd5430`
- Certificate SHA-256: `20def177b8fcb890b90bca3af23c07d46f78bf8c8849a6a65afced1a9ca32868`
- Human approval required: **true**
- Consequential action taken: **false**

The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.
