# MergeProof Requirements

## Functional requirements

| ID | Requirement | Verification |
|---|---|---|
| R1 | Accept a task statement, baseline/candidate repository state or Git working tree, and an optional agent trajectory. | CLI integration tests. |
| R2 | Produce baseline and advanced results using one shared output schema. | Schema tests and benchmark runner. |
| R3 | Extract explicit requirements, ambiguities, invariants, and agent claims. | Frozen prompt tests and trajectory fixtures. |
| R4 | Collect deterministic evidence: file manifest, content hashes, patch, dependency changes, scope signals, secret-shaped strings, and verification output. | Unit tests with synthetic repositories. |
| R5 | Run only configured verification commands in an isolated copy with time and output limits; prefer Docker network isolation for untrusted code. | Sandbox integration and timeout tests. |
| R6 | Bind every final finding to one or more resolvable evidence IDs. Unsupported model assertions must be rejected or marked as hypotheses. | Evidence-gate property tests. |
| R7 | Emit JSON plus a self-contained, polished HTML/Markdown report with a human approval boundary. | Snapshot and browser-smoke tests. |
| R8 | Log representative, sanitized trajectories for every product agent, including prompts, tool observations, retries, usage, and human checkpoints. | Trajectory-schema and secret-scan tests. |
| R9 | Support deterministic offline replay from committed response fixtures and live execution through Gemini and OpenAI-compatible APIs. | Provider contract tests. |
| R10 | Evaluate baseline and workflow variants on frozen cases without exposing gold labels to agents. | Benchmark leakage tests. |

## Quality and safety requirements

| ID | Requirement | Verification |
|---|---|---|
| Q1 | Python 3.11+; locked dependencies; one-command setup, tests, demo, and evaluation. | Clean-container reproduction. |
| Q2 | No submitted credentials, private data, or provider secrets in prompts, logs, reports, caches, archives, or Git history. | Automated secret scan plus Git-history scan. |
| Q3 | Deterministic components use sorted inputs, stable hashes, bounded output, and explicit errors. | Repeatability tests. |
| Q4 | A malformed model response cannot bypass the evidence gate or produce an automatic approval. | Fuzz/property tests. |
| Q5 | Consequential actions remain simulated/read-only and require a qualified human decision. | Policy tests and report copy. |
| Q6 | Every reported metric is mechanically derived from committed raw results. | Independent metric recomputation. |

## Deliberate trade-offs

- MergeProof favors an auditable CLI and self-contained report over a large web application.
- Live model calls are optional for reproduction; submitted results use immutable replay fixtures.
- General arbitrary-code verification is impossible to make perfectly safe. Docker isolation is the preferred live boundary, and host execution is restricted to trusted fixtures with an explicit flag.
- The benchmark measures the stated synthetic failure distribution; it does not establish universal performance on all repositories.
