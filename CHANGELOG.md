# MergeProof Experiment Changelog

This log records material design and evaluation decisions, including failed or removed experiments. Metrics are accepted only when the run satisfies its stated integrity checks.

## E000 — Frozen problem and evaluation contract

**Status:** kept
**Decision:** review agent-authored code changes using an evidence-grounded human release gate. Freeze the user, non-goals, baseline, primary metric, benchmark shape, and acceptance criteria before implementation.
**Reason:** prevents retrospective changes to the problem or metric after observing results.
**Evidence:** `oracle/problem-brief.md`, `docs/requirements.md`, `docs/evaluation-plan.md`.

## E001 — One-shot baseline and 24-case synthetic benchmark

**Status:** kept
**Intervention:** one direct review prompt with task, before/candidate trees, declared commands, scope policy, and submitted trajectory; no executable verification or independent critic.
**Benchmark:** 24 opaque-ID cases, balanced 12 safe / 12 unsafe, with gold labels kept outside agent-visible inputs.
**Validation:** every safe fixture passes declared verification; intended failing tests, skipped tests, scope violations, unused dependencies, synthetic credential exposure, nondeterminism, and unsafe commands are independently reproduced by `scripts/validate_benchmark.py`.
**Reason:** establishes a fair, useful baseline rather than a deliberately broken comparator.

## E002 — Gemini smoke call with query-string authentication

**Status:** removed and remediated; no result accepted
**Observation:** the provider returned HTTP 403. The original client put its API key in the request URL, allowing the HTTP exception to include credential material in transient local operation logs. No affected result or credential was committed or pushed.
**Changes:** use `x-goog-api-key` header authentication, sanitize provider exception text, delete failed result directories, scan the workspace for the exact credential, and add regression tests proving credentials are absent from URLs and redacted from errors.
**Residual operator action:** rotate the affected Gemini credential because one read-only external operation log could not be rewritten from the workspace sandbox.
**Reason removed:** security and provider-access failure invalidate the run.

## E003 — Unpaced Groq full-baseline attempt

**Status:** removed; no metric accepted
**Model:** `openai/gpt-oss-20b` through Groq.
**Observation:** 6 of 24 calls succeeded; 18 failed closed with HTTP 429. Provider headers showed a 1,000-request limit but an 8,000-token limit with short token-reset windows. The generated 24-row result therefore represented transport failure, not review quality.
**Changes:** add bounded 429/5xx retry, parse `Retry-After` and token-reset durations, pace sequential Groq calls at 12 seconds, and report HTTP attempts plus rate-limit wait time in resource metrics.
**Integrity rule:** a canonical live run is accepted only when there are 24 successful model usages and 24 content-addressed replay fixtures.
**Reason removed:** incomplete replay coverage and provider failures violate the run manifest.

## E004 — Canonical one-shot baseline and metric ceiling

**Status:** kept and frozen
**Model/provider:** `openai/gpt-oss-20b` through Groq, temperature zero.
**Integrity:** 24 results, 24 successful model usages, 24 content-addressed replay fixtures, zero gate violations, and a clean artifact security scan.
**Measured result:** unsafe-change decision F1 1.000; safe approval precision 1.000; issue-category micro-F1 0.500; evidence-reference validity 1.000; 44,050 total tokens; 24 HTTP attempts.
**Replay:** offline replay reproduces every semantic result and comparable metric; only provider identity and measured runtime/latency differ.
**Protocol consequence:** preserve unsafe-change F1 as a mandatory 1.000 no-regression gate. Before advanced implementation, preregister verified issue-category micro-F1 as the discriminative optimization metric with a target of at least 0.850 and at least +0.250 absolute improvement.
**Reason:** binary merge/block classification saturated, while actionable diagnosis remained materially incomplete. The v1 metric and run are retained rather than hidden or rewritten.
**Evidence:** `results/baseline-live-groq-gpt-oss-20b/`, `results/baseline-replay-gpt-oss-20b/`, `fixtures/replay/groq-gpt-oss-20b/`, and `docs/evaluation-protocol-v2.md`.
