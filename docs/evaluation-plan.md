# Evaluation Plan

## Frozen benchmark

The committed benchmark generator creates at least 20 small Python repositories from deterministic case definitions. Agent-visible inputs live separately from `benchmark/gold/`. Agents receive only the task, before/candidate trees, trajectory, and collected evidence. Gold files are opened only after a result is finalized.

Planned unsafe categories:

1. fabricated passing-test claim;
2. failing declared test;
3. skipped test concealed as success;
4. boundary-value regression;
5. out-of-scope edit;
6. dependency drift;
7. credential-shaped secret committed to source;
8. path-traversal vulnerability;
9. nondeterministic/flaky behavior;
10. unsafe or undeclared verification command.

Matched safe controls ensure the system cannot score well by rejecting everything.

## Compared stages

| Stage | Purpose |
|---|---|
| `baseline` | One direct prompt, no tools. |
| `contract` | Structured task/requirement extraction before review. |
| `verified` | Adds deterministic collection and sandbox verification. |
| `final` | Adds skeptic plus evidence admission. |
| `ensemble_experiment` | Tests an additional reviewer; retained only if it improves the preregistered metric enough to justify cost. |

All stages use the same model, temperature, cases, output labels, and gold evaluator. Model calls, tokens, latency, and estimated cost are reported per stage.

## Metrics

For unsafe patch as positive class:

```text
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
F1        = 2 * precision * recall / (precision + recall)
```

Also report:

- case accuracy and confusion matrix;
- issue-category micro/macro F1;
- evidence-reference validity (`valid referenced IDs / all referenced IDs`);
- safe approval precision;
- median and p95 wall time;
- calls, input/output tokens, and estimated cost.

Metrics are recomputed by a standalone script from raw JSONL. The report generator consumes that output; it does not calculate a second, divergent version.

## Fairness and leakage controls

- Gold labels are outside every agent-visible directory.
- Prompts and response fixtures are content-hashed.
- Cases are sorted and evaluation is deterministic except for live model calls.
- Live results are frozen into replay fixtures before final reporting.
- Baseline and advanced modes receive the same raw task and candidate content.
- Resource differences are explicit because purposeful tools are the intervention being measured.
- Failed/invalid model outputs count as failures rather than being silently retried indefinitely.

## Clean reproduction

The final release is rerun in a fresh container with no provider credentials using replay mode. The clean run must regenerate the benchmark metrics and representative reports byte-for-byte, except for explicitly normalized timestamps and environment metadata.
