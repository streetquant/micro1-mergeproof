# DriftDoctor upstream boundary

## Decision

The user selected `AaryaMody1301/DriftDoctor` as relevant prior art. It is a complete submission by another participant in the same micro1 hackathon, not an organizer starter repository and not work authored in this repository.

We will **not** rename, copy wholesale, or present DriftDoctor's implementation or measured results as our own. We use it only as a pinned, credited repair producer and benchmark substrate for an original independent verification product.

## Pinned source

- Repository: `https://github.com/AaryaMody1301/DriftDoctor`
- Commit: `0760ce3772678fdb7309b467f41f0371c1c10feb`
- Commit timestamp: `2026-08-29T16:07:47Z`
- License: MIT, copyright 2026 Aarya Mody
- Local inspection clone: `.cache/driftdoctor-upstream` (ignored, never submitted as our source)

The lock is machine-readable in `upstream/driftdoctor.lock.json`.

## What we may use

The MIT license permits use, modification, and redistribution with its copyright and permission notice. Our reproducibility tooling may fetch the exact pinned commit, and evaluation-only adapters may call its public fixture/evaluator interfaces.

## What remains upstream work

The following are explicitly credited to DriftDoctor and must never be described as our contributions:

- its dbt/DuckDB fixture factory and twelve-case benchmark;
- its external oracle and reference repairs;
- its repair skills, bounded ambiguity resolver, orchestrator, CLI, tests, documentation, evidence, and reported scores;
- its 12/12 Verified Resolution Rate and held-out ambiguity-agent trajectory.

## Original contribution in this entry

The new product is **DriftProof**, an independent adversarial release gate for agent-authored dbt repairs. It will:

1. accept a candidate repair and its agent trajectory from any repair producer, including DriftDoctor;
2. rerun the candidate in an isolated, clean environment;
3. compile visible business contracts into executable checks without importing the hidden evaluator;
4. generate deterministic negative controls and, where bounded ambiguity remains, schema-constrained agent-proposed probes;
5. detect green-but-semantically-wrong repairs, unsafe scope expansion, suppressed tests, hidden-state dependence, and unsupported claims;
6. issue a hash-bound approval certificate or require human escalation;
7. never merge, deploy, or modify the original project.

## Evaluation separation

- Runtime code must not import DriftDoctor's `benchmark.oracles` or `benchmark.reference_repairs`.
- Those modules are evaluation-only and score a frozen paired set of safe and deceptive-green candidate repairs after DriftProof has produced its verdict.
- Baseline and advanced systems receive identical candidate projects, incidents, visible business context, and declared trajectories.
- The primary comparison measures safe approval decisions, not DriftDoctor's upstream repair score.

## Submission disclosure

The final README, third-party notices, provenance report, video, and HackerEarth submission text must disclose this upstream dependency and distinguish every upstream artifact from every file written here. The project remains eligible only if the original contribution is independently useful and materially more than repackaging.
