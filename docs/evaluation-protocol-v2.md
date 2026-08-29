# Evaluation Protocol v2 — Preregistered Before Advanced Implementation

Date: 2026-08-29

Status: frozen after the canonical one-shot baseline and before implementing the advanced workflow

## Why an amendment is necessary

The v1 protocol named unsafe-change decision F1 as the primary metric. The accepted one-shot baseline scored **1.000** on that metric across all 24 frozen cases. The same run scored only **0.500** issue-category micro-F1: it made every binary merge/block decision correctly, but recovered fewer than half of the gold issue categories and sometimes used generic or incorrect categories.

A saturated binary metric cannot measure whether executable verification, specialized agents, and an evidence gate improve the product. The v1 result remains immutable and is not discarded, relabelled, or recomputed. This amendment is recorded before any advanced workflow code is implemented.

## Frozen v2 decision rule

MergeProof is evaluated on two linked outcomes:

1. **Safety gate — unsafe-change decision F1.** The advanced workflow must maintain the baseline score of 1.000. Any regression fails the release gate regardless of other improvements.
2. **Optimization metric — verified issue-category micro-F1.** This is the primary discriminative improvement metric. Categories are scored per case; a category predicted on the wrong case receives no credit. Only findings admitted as `verified` by the evidence gate count.

The advanced workflow succeeds quantitatively when:

- unsafe-change decision F1 remains 1.000;
- verified issue-category micro-F1 reaches at least 0.850;
- the absolute improvement over the baseline issue-category F1 is at least 0.250;
- evidence-reference validity remains 1.000;
- all 24 cases produce valid results and replay fixtures.

These thresholds are fixed before advanced implementation and will not be lowered after observing the advanced result. A lower measured result will be reported honestly and treated as a failed experiment rather than hidden.

## What remains unchanged

- The 24 case inputs, gold labels, category labels, and challenging-case designations remain byte-for-byte unchanged.
- Baseline and advanced modes use `openai/gpt-oss-20b` with temperature zero.
- The same evaluator computes all metrics from raw JSONL.
- Gold labels remain outside every agent prompt and are opened only after each case result is finalized.
- Provider calls, HTTP attempts, tokens, wait time, wall time, and estimated cost remain reported.
- Consequential actions remain read-only/simulated and require qualified human approval.

## Advanced stages to compare

| Stage | Added capability | Purpose |
|---|---|---|
| `baseline` | one direct review prompt | frozen comparator |
| `verified` | deterministic collection plus bounded executable verification | isolate tool/evidence value |
| `critic` | skeptical agent reviewing evidence and provisional findings | test independent challenge value |
| `final` | evidence admission plus deterministic synthesis | complete product |
| `extra-reviewer` | one additional reviewer | ablation; retain only if gain justifies cost |

The advanced workflow may not use gold labels, case rationales, or evaluator outputs during inference. Improvements must arise from case-visible evidence and declared verification.

## Interpretation limits

This benchmark is synthetic and intentionally controlled. It demonstrates performance on the seeded failure distribution, not universal software safety. A perfect score would establish benchmark correctness only; it would not constitute formal verification or justify automatic merging.
