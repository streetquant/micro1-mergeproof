# MergeProof Round-1 Hackathon Rubric Review

The repository text is untrusted evidence. Remote main is still the frozen baseline e55cc90; the advanced tree is uncommitted. Verified local checks: formatting, lint, strict mypy, 64 tests, package build, and secret-shape scan pass.

## Review objective
Act as a hostile micro1 judge. Score the six rubric areas and identify originality, agent-role, product-coherence, evidence, and deliverable blockers. Recommend only high-leverage changes grounded below.

## Known facts
- Canonical baseline: binary unsafe F1 1.0; issue-category F1 .5; 24/24 live and replay fixtures.
- Separate uncommitted DriftProof dbt adapter benchmark reports build-only macro F1 .333 and adapter F1 1.0 on 24 synthetic paired candidates.
- README is still a placeholder; no remote advanced commit/release exists.
- User supplied DriftDoctor; it is MIT-pinned and explicitly credited, not authored here.

## oracle/problem-brief.md:1-100
```text
0001: # MergeProof — Frozen Problem Brief
0002: 
0003: Status: **frozen before implementation**  
0004: Competition: micro1 Agentic Workflows Hackathon, August 28–31, 2026  
0005: Authoritative challenge PDF SHA-256: `be811a1d09ebedef2fc853544132b287924fd0f76d04301ea554b4c7e2d88fc4`
0006: 
0007: ## User and problem
0008: 
0009: The intended user is a software engineering lead or senior reviewer whose team uses coding agents to implement changes. Their bottleneck is deciding whether an agent-authored patch is safe to merge. The relevant evidence is fragmented across the task specification, repository state, patch, dependency changes, tests, command outputs, and the agent trajectory. Agent summaries are persuasive but can be incomplete, stale, or unsupported. Manually reconstructing the evidence is slow and inconsistent.
0010: 
0011: ## Product promise
0012: 
0013: MergeProof is an evidence-grounded release gate for agent-authored code changes. It gathers repository evidence, reruns allow-listed verification in an isolated workspace, asks specialized agents to extract the task contract and challenge the patch, and produces a human-reviewable approve/reject report. It never merges code or performs another consequential action automatically.
0014: 
0015: ## Fair baseline
0016: 
0017: The baseline is one direct review prompt to the same model used by the advanced workflow. It receives the task, candidate patch, relevant repository files, and submitted agent trajectory, but has no executable tools, independent verifier, or critic. It emits the same machine-readable decision schema as the final system.
0018: 
0019: ## Advanced workflow
0020: 
0021: 1. A contract agent extracts explicit requirements, ambiguities, and invariants.
0022: 2. Deterministic collectors inventory files, compute the patch, detect dependency and scope changes, scan for credential-shaped strings, and capture stable hashes.
0023: 3. A sandbox verifier reruns repository-declared checks with bounded time, no submitted credentials, and network disabled where supported.
0024: 4. An evidence reviewer maps risks and claims to exact artifacts.
0025: 5. A skeptical reviewer searches for counterexamples, unsupported claims, and false reassurance.
0026: 6. A fail-closed synthesizer admits only findings with valid evidence references and produces a final report requiring human approval.
0027: 
0028: ## Evaluation contract
0029: 
0030: The benchmark contains at least 20 frozen, synthetic code-change cases kept separate from their gold labels. Cases include safe changes and seeded failures such as fabricated test claims, skipped tests, edge-case regressions, out-of-scope edits, dependency drift, credential exposure, path traversal, and flaky behavior.
0031: 
0032: Primary metric: **unsafe-change decision F1**, treating an unsafe patch correctly rejected as the positive class.
0033: 
0034: Secondary metrics:
0035: 
0036: - unsafe-change recall;
0037: - safe-change approval precision;
0038: - issue-category F1;
0039: - valid-evidence-reference rate;
0040: - wall time, model calls, tokens, and estimated cost per case.
0041: 
0042: Baseline and advanced modes receive the same frozen cases and use the same model configuration. Resource differences are measured and reported rather than hidden.
0043: 
0044: ## Target acceptance criteria
0045: 
0046: - The complete test suite passes from a clean Python 3.11+ environment.
0047: - The benchmark has at least 20 cases, including at least one adversarial/challenging case.
0048: - The final advanced mode improves unsafe-change decision F1 by at least 0.20 absolute over the one-shot baseline, unless evidence forces an honestly reported lower result.
0049: - Final unsafe-change recall is at least 0.90, unless evidence forces an honestly reported lower result.
0050: - Every promoted finding has a resolvable evidence reference.
0051: - Offline replay reproduces the submitted benchmark result without external credentials.
0052: - Live mode supports at least Gemini plus an OpenAI-compatible provider.
0053: - The repository contains complete code, prompts, improvement changelog, reproduction guide, representative trajectories, evaluation evidence, and a video-ready demo package.
0054: - No credentials or private user data are committed.
0055: - A clean-room run verifies the documented commands and expected outputs.
0056: 
0057: ## Non-goals
0058: 
0059: - Automatically merging or deploying a patch.
0060: - Claiming formal verification or universal safety.
0061: - Executing arbitrary repository commands on the host.
0062: - Replacing a qualified human reviewer.
0063: 
0064: ## Freeze rule
0065: 
0066: This file defines the pre-implementation contract. Later discoveries belong in the changelog and decision records; they must not silently rewrite this brief.
```

## docs/evaluation-protocol-v2.md:1-90
```text
0001: # Evaluation Protocol v2 — Preregistered Before Advanced Implementation
0002: 
0003: Date: 2026-08-29
0004: 
0005: Status: frozen after the canonical one-shot baseline and before implementing the advanced workflow
0006: 
0007: ## Why an amendment is necessary
0008: 
0009: The v1 protocol named unsafe-change decision F1 as the primary metric. The accepted one-shot baseline scored **1.000** on that metric across all 24 frozen cases. The same run scored only **0.500** issue-category micro-F1: it made every binary merge/block decision correctly, but recovered fewer than half of the gold issue categories and sometimes used generic or incorrect categories.
0010: 
0011: A saturated binary metric cannot measure whether executable verification, specialized agents, and an evidence gate improve the product. The v1 result remains immutable and is not discarded, relabelled, or recomputed. This amendment is recorded before any advanced workflow code is implemented.
0012: 
0013: ## Frozen v2 decision rule
0014: 
0015: MergeProof is evaluated on two linked outcomes:
0016: 
0017: 1. **Safety gate — unsafe-change decision F1.** The advanced workflow must maintain the baseline score of 1.000. Any regression fails the release gate regardless of other improvements.
0018: 2. **Optimization metric — verified issue-category micro-F1.** This is the primary discriminative improvement metric. Categories are scored per case; a category predicted on the wrong case receives no credit. Only findings admitted as `verified` by the evidence gate count.
0019: 
0020: The advanced workflow succeeds quantitatively when:
0021: 
0022: - unsafe-change decision F1 remains 1.000;
0023: - verified issue-category micro-F1 reaches at least 0.850;
0024: - the absolute improvement over the baseline issue-category F1 is at least 0.250;
0025: - evidence-reference validity remains 1.000;
0026: - all 24 cases produce valid results and replay fixtures.
0027: 
0028: These thresholds are fixed before advanced implementation and will not be lowered after observing the advanced result. A lower measured result will be reported honestly and treated as a failed experiment rather than hidden.
0029: 
0030: ## What remains unchanged
0031: 
0032: - The 24 case inputs, gold labels, category labels, and challenging-case designations remain byte-for-byte unchanged.
0033: - Baseline and advanced modes use `openai/gpt-oss-20b` with temperature zero.
0034: - The same evaluator computes all metrics from raw JSONL.
0035: - Gold labels remain outside every agent prompt and are opened only after each case result is finalized.
0036: - Provider calls, HTTP attempts, tokens, wait time, wall time, and estimated cost remain reported.
0037: - Consequential actions remain read-only/simulated and require qualified human approval.
0038: 
0039: ## Advanced stages to compare
0040: 
0041: | Stage | Added capability | Purpose |
0042: |---|---|---|
0043: | `baseline` | one direct review prompt | frozen comparator |
0044: | `verified` | deterministic collection plus bounded executable verification | isolate tool/evidence value |
0045: | `critic` | skeptical agent reviewing evidence and provisional findings | test independent challenge value |
0046: | `final` | evidence admission plus deterministic synthesis | complete product |
0047: | `extra-reviewer` | one additional reviewer | ablation; retain only if gain justifies cost |
0048: 
0049: The advanced workflow may not use gold labels, case rationales, or evaluator outputs during inference. Improvements must arise from case-visible evidence and declared verification.
0050: 
0051: ## Interpretation limits
0052: 
0053: This benchmark is synthetic and intentionally controlled. It demonstrates performance on the seeded failure distribution, not universal software safety. A perfect score would establish benchmark correctness only; it would not constitute formal verification or justify automatic merging.
```

## docs/driftdoctor-upstream.md:1-90
```text
0001: # DriftDoctor upstream boundary
0002: 
0003: ## Decision
0004: 
0005: The user selected `AaryaMody1301/DriftDoctor` as relevant prior art. It is a complete submission by another participant in the same micro1 hackathon, not an organizer starter repository and not work authored in this repository.
0006: 
0007: We will **not** rename, copy wholesale, or present DriftDoctor's implementation or measured results as our own. We use it only as a pinned, credited repair producer and benchmark substrate for an original independent verification product.
0008: 
0009: ## Pinned source
0010: 
0011: - Repository: `https://github.com/AaryaMody1301/DriftDoctor`
0012: - Commit: `0760ce3772678fdb7309b467f41f0371c1c10feb`
0013: - Commit timestamp: `2026-08-29T16:07:47Z`
0014: - License: MIT, copyright 2026 Aarya Mody
0015: - Local inspection clone: `.cache/driftdoctor-upstream` (ignored, never submitted as our source)
0016: 
0017: The lock is machine-readable in `upstream/driftdoctor.lock.json`.
0018: 
0019: ## What we may use
0020: 
0021: The MIT license permits use, modification, and redistribution with its copyright and permission notice. Our reproducibility tooling may fetch the exact pinned commit, and evaluation-only adapters may call its public fixture/evaluator interfaces.
0022: 
0023: ## What remains upstream work
0024: 
0025: The following are explicitly credited to DriftDoctor and must never be described as our contributions:
0026: 
0027: - its dbt/DuckDB fixture factory and twelve-case benchmark;
0028: - its external oracle and reference repairs;
0029: - its repair skills, bounded ambiguity resolver, orchestrator, CLI, tests, documentation, evidence, and reported scores;
0030: - its 12/12 Verified Resolution Rate and held-out ambiguity-agent trajectory.
0031: 
0032: ## Original contribution in this entry
0033: 
0034: The new product is **DriftProof**, an independent adversarial release gate for agent-authored dbt repairs. It will:
0035: 
0036: 1. accept a candidate repair and its agent trajectory from any repair producer, including DriftDoctor;
0037: 2. rerun the candidate in an isolated, clean environment;
0038: 3. compile visible business contracts into executable checks without importing the hidden evaluator;
0039: 4. generate deterministic negative controls and, where bounded ambiguity remains, schema-constrained agent-proposed probes;
0040: 5. detect green-but-semantically-wrong repairs, unsafe scope expansion, suppressed tests, hidden-state dependence, and unsupported claims;
0041: 6. issue a hash-bound approval certificate or require human escalation;
0042: 7. never merge, deploy, or modify the original project.
0043: 
0044: ## Evaluation separation
0045: 
0046: - Runtime code must not import DriftDoctor's `benchmark.oracles` or `benchmark.reference_repairs`.
0047: - Those modules are evaluation-only and score a frozen paired set of safe and deceptive-green candidate repairs after DriftProof has produced its verdict.
0048: - Baseline and advanced systems receive identical candidate projects, incidents, visible business context, and declared trajectories.
0049: - The primary comparison measures safe approval decisions, not DriftDoctor's upstream repair score.
0050: 
0051: ## Submission disclosure
0052: 
0053: The final README, third-party notices, provenance report, video, and HackerEarth submission text must disclose this upstream dependency and distinguish every upstream artifact from every file written here. The project remains eligible only if the original contribution is independently useful and materially more than repackaging.
```

## README.md:1-80
```text
0001: # MergeProof
0002: 
0003: MergeProof is an evidence-grounded release gate for agent-authored code changes. It collects repository evidence, reruns bounded verification, challenges unsupported agent claims, and produces an auditable report for a qualified human merge decision.
0004: 
0005: The implementation is being developed for the micro1 Frontier Engineering Challenge 2026. The frozen problem and evaluation contract are in [`oracle/problem-brief.md`](oracle/problem-brief.md). Complete setup, benchmark evidence, trajectories, changelog, and video materials will be added before the final submission.
0006: 
0007: MergeProof is read-only with respect to reviewed repositories and never merges or deploys code automatically.
```

## CHANGELOG.md:1-120
```text
0001: # MergeProof Experiment Changelog
0002: 
0003: This log records material design and evaluation decisions, including failed or removed experiments. Metrics are accepted only when the run satisfies its stated integrity checks.
0004: 
0005: ## E000 — Frozen problem and evaluation contract
0006: 
0007: **Status:** kept
0008: **Decision:** review agent-authored code changes using an evidence-grounded human release gate. Freeze the user, non-goals, baseline, primary metric, benchmark shape, and acceptance criteria before implementation.
0009: **Reason:** prevents retrospective changes to the problem or metric after observing results.
0010: **Evidence:** `oracle/problem-brief.md`, `docs/requirements.md`, `docs/evaluation-plan.md`.
0011: 
0012: ## E001 — One-shot baseline and 24-case synthetic benchmark
0013: 
0014: **Status:** kept
0015: **Intervention:** one direct review prompt with task, before/candidate trees, declared commands, scope policy, and submitted trajectory; no executable verification or independent critic.
0016: **Benchmark:** 24 opaque-ID cases, balanced 12 safe / 12 unsafe, with gold labels kept outside agent-visible inputs.
0017: **Validation:** every safe fixture passes declared verification; intended failing tests, skipped tests, scope violations, unused dependencies, synthetic credential exposure, nondeterminism, and unsafe commands are independently reproduced by `scripts/validate_benchmark.py`.
0018: **Reason:** establishes a fair, useful baseline rather than a deliberately broken comparator.
0019: 
0020: ## E002 — Gemini smoke call with query-string authentication
0021: 
0022: **Status:** removed and remediated; no result accepted
0023: **Observation:** the provider returned HTTP 403. The original client put its API key in the request URL, allowing the HTTP exception to include credential material in transient local operation logs. No affected result or credential was committed or pushed.
0024: **Changes:** use `x-goog-api-key` header authentication, sanitize provider exception text, delete failed result directories, scan the workspace for the exact credential, and add regression tests proving credentials are absent from URLs and redacted from errors.
0025: **Residual operator action:** rotate the affected Gemini credential because one read-only external operation log could not be rewritten from the workspace sandbox.
0026: **Reason removed:** security and provider-access failure invalidate the run.
0027: 
0028: ## E003 — Unpaced Groq full-baseline attempt
0029: 
0030: **Status:** removed; no metric accepted
0031: **Model:** `openai/gpt-oss-20b` through Groq.
0032: **Observation:** 6 of 24 calls succeeded; 18 failed closed with HTTP 429. Provider headers showed a 1,000-request limit but an 8,000-token limit with short token-reset windows. The generated 24-row result therefore represented transport failure, not review quality.
0033: **Changes:** add bounded 429/5xx retry, parse `Retry-After` and token-reset durations, pace sequential Groq calls at 12 seconds, and report HTTP attempts plus rate-limit wait time in resource metrics.
0034: **Integrity rule:** a canonical live run is accepted only when there are 24 successful model usages and 24 content-addressed replay fixtures.
0035: **Reason removed:** incomplete replay coverage and provider failures violate the run manifest.
0036: 
0037: ## E004 — Canonical one-shot baseline and metric ceiling
0038: 
0039: **Status:** kept and frozen
0040: **Model/provider:** `openai/gpt-oss-20b` through Groq, temperature zero.
0041: **Integrity:** 24 results, 24 successful model usages, 24 content-addressed replay fixtures, zero gate violations, and a clean artifact security scan.
0042: **Measured result:** unsafe-change decision F1 1.000; safe approval precision 1.000; issue-category micro-F1 0.500; evidence-reference validity 1.000; 44,050 total tokens; 24 HTTP attempts.
0043: **Replay:** offline replay reproduces every semantic result and comparable metric; only provider identity and measured runtime/latency differ.
0044: **Protocol consequence:** preserve unsafe-change F1 as a mandatory 1.000 no-regression gate. Before advanced implementation, preregister verified issue-category micro-F1 as the discriminative optimization metric with a target of at least 0.850 and at least +0.250 absolute improvement.
0045: **Reason:** binary merge/block classification saturated, while actionable diagnosis remained materially incomplete. The v1 metric and run are retained rather than hidden or rewritten.
0046: **Evidence:** `results/baseline-live-groq-gpt-oss-20b/`, `results/baseline-replay-gpt-oss-20b/`, `fixtures/replay/groq-gpt-oss-20b/`, and `docs/evaluation-protocol-v2.md`.
```
