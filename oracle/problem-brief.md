# MergeProof — Frozen Problem Brief

Status: **frozen before implementation**  
Competition: micro1 Agentic Workflows Hackathon, August 28–31, 2026  
Authoritative challenge PDF SHA-256: `be811a1d09ebedef2fc853544132b287924fd0f76d04301ea554b4c7e2d88fc4`

## User and problem

The intended user is a software engineering lead or senior reviewer whose team uses coding agents to implement changes. Their bottleneck is deciding whether an agent-authored patch is safe to merge. The relevant evidence is fragmented across the task specification, repository state, patch, dependency changes, tests, command outputs, and the agent trajectory. Agent summaries are persuasive but can be incomplete, stale, or unsupported. Manually reconstructing the evidence is slow and inconsistent.

## Product promise

MergeProof is an evidence-grounded release gate for agent-authored code changes. It gathers repository evidence, reruns allow-listed verification in an isolated workspace, asks specialized agents to extract the task contract and challenge the patch, and produces a human-reviewable approve/reject report. It never merges code or performs another consequential action automatically.

## Fair baseline

The baseline is one direct review prompt to the same model used by the advanced workflow. It receives the task, candidate patch, relevant repository files, and submitted agent trajectory, but has no executable tools, independent verifier, or critic. It emits the same machine-readable decision schema as the final system.

## Advanced workflow

1. A contract agent extracts explicit requirements, ambiguities, and invariants.
2. Deterministic collectors inventory files, compute the patch, detect dependency and scope changes, scan for credential-shaped strings, and capture stable hashes.
3. A sandbox verifier reruns repository-declared checks with bounded time, no submitted credentials, and network disabled where supported.
4. An evidence reviewer maps risks and claims to exact artifacts.
5. A skeptical reviewer searches for counterexamples, unsupported claims, and false reassurance.
6. A fail-closed synthesizer admits only findings with valid evidence references and produces a final report requiring human approval.

## Evaluation contract

The benchmark contains at least 20 frozen, synthetic code-change cases kept separate from their gold labels. Cases include safe changes and seeded failures such as fabricated test claims, skipped tests, edge-case regressions, out-of-scope edits, dependency drift, credential exposure, path traversal, and flaky behavior.

Primary metric: **unsafe-change decision F1**, treating an unsafe patch correctly rejected as the positive class.

Secondary metrics:

- unsafe-change recall;
- safe-change approval precision;
- issue-category F1;
- valid-evidence-reference rate;
- wall time, model calls, tokens, and estimated cost per case.

Baseline and advanced modes receive the same frozen cases and use the same model configuration. Resource differences are measured and reported rather than hidden.

## Target acceptance criteria

- The complete test suite passes from a clean Python 3.11+ environment.
- The benchmark has at least 20 cases, including at least one adversarial/challenging case.
- The final advanced mode improves unsafe-change decision F1 by at least 0.20 absolute over the one-shot baseline, unless evidence forces an honestly reported lower result.
- Final unsafe-change recall is at least 0.90, unless evidence forces an honestly reported lower result.
- Every promoted finding has a resolvable evidence reference.
- Offline replay reproduces the submitted benchmark result without external credentials.
- Live mode supports at least Gemini plus an OpenAI-compatible provider.
- The repository contains complete code, prompts, improvement changelog, reproduction guide, representative trajectories, evaluation evidence, and a video-ready demo package.
- No credentials or private user data are committed.
- A clean-room run verifies the documented commands and expected outputs.

## Non-goals

- Automatically merging or deploying a patch.
- Claiming formal verification or universal safety.
- Executing arbitrary repository commands on the host.
- Replacing a qualified human reviewer.

## Freeze rule

This file defines the pre-implementation contract. Later discoveries belong in the changelog and decision records; they must not silently rewrite this brief.
