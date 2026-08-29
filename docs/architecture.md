# MergeProof Architecture

## System boundary

MergeProof reviews an agent-authored change and returns evidence for a human merge decision. It is read-only with respect to the reviewed repository and never invokes Git merge, push, deployment, email, ticket updates, or other consequential actions.

## Data flow

```text
Task + repository + optional trajectory
                 |
                 v
        deterministic intake
  (manifest, diff, hashes, policy scan)
                 |
          +------+------+
          |             |
          v             v
   contract agent   sandbox verifier
          |             |
          +------+------+
                 v
         evidence reviewer
                 |
                 v
          skeptical reviewer
                 |
                 v
       evidence admission gate
                 |
                 v
 JSON + Markdown + self-contained HTML
                 |
                 v
       qualified human decision
```

## Planned packages

- `mergeproof.models`: typed schemas for cases, evidence, findings, trajectories, and results.
- `mergeproof.providers`: Gemini, OpenAI-compatible, and immutable replay clients.
- `mergeproof.prompts`: versioned agent instructions and strict JSON contracts.
- `mergeproof.collector`: deterministic repository intake and evidence ledger.
- `mergeproof.sandbox`: bounded command execution in an isolated copy/Docker.
- `mergeproof.pipeline`: baseline and workflow variants.
- `mergeproof.evidence_gate`: referential-integrity and fail-closed decision logic.
- `mergeproof.reporting`: Markdown, JSON, and self-contained HTML output.
- `mergeproof.benchmark`: gold-separated case runner and independent metrics.
- `mergeproof.cli`: setup-free command surface.

## Agent roles

1. **Contract Agent** — extracts requirements and ambiguity; it does not judge the patch.
2. **Evidence Reviewer** — proposes categorized findings using deterministic evidence.
3. **Skeptic Agent** — tries to falsify the provisional decision and identify missing evidence.
4. **Synthesis Agent** — reconciles disagreement but cannot create new evidence.

The number of agents is intentionally small. Each role has a different input contract and a mechanically checked output; orchestration exists to reduce correlated failure, not for spectacle.

## Evidence ledger

Every evidence artifact receives a stable identifier derived from its kind, source path, and SHA-256. Examples include `diff:...`, `file:...`, `command:...`, and `scan:...`. A final finding is publishable only when all referenced IDs exist and the finding's admission policy is satisfied. Model-only suspicions remain clearly labeled hypotheses and force human review rather than rejection unless supported by evidence.

## Failure posture

- Provider error or malformed output: record the failure and return `human_review`; never approve by default.
- Verification timeout: retain partial output, emit timeout evidence, and return `human_review` or `reject` according to policy.
- Missing tests: report the absence rather than claiming success.
- Broken evidence reference: drop the finding, record a gate violation, and prevent automatic approval.
- Untrusted execution without Docker: refuse unless the operator explicitly marks the repository trusted.
