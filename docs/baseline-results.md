# Canonical Baseline Result

## Run identity

- Mode: `baseline`
- Provider: Groq
- Model: `openai/gpt-oss-20b`
- Temperature: 0
- Cases: 24 (12 safe, 12 unsafe)
- Live raw-results SHA-256: `edd9be617235ce4bf79ccbad86eaf3d2ede4592c69bcbe319997b3adf1fe1302`
- Live metrics SHA-256: `406aa14a04fe84faf2fca794fbaa5ad38f422a3a8325c066be1b8e0335e3b8b1`
- Replay fixture-directory SHA-256: `a99fc38725b2897ff8aedc218d9b49cd1f2a46a394e24b5259d366bbdac8bedc`

## Integrity checks

| Check | Result |
|---|---:|
| Raw case results | 24 / 24 |
| Successful model usages | 24 / 24 |
| Replay fixtures | 24 / 24 |
| Gate violations | 0 |
| Valid evidence-reference rate | 1.000 |
| Offline semantic replay | Pass |
| Credential-format artifact scan | Pass |

## Measured performance

| Metric | Baseline |
|---|---:|
| Unsafe-change decision F1 | 1.000 |
| Unsafe-change precision | 1.000 |
| Unsafe-change recall | 1.000 |
| Safe approval precision | 1.000 |
| Issue-category micro-F1 | 0.500 |
| Issue-category precision | 0.643 |
| Issue-category recall | 0.409 |
| Evidence-reference validity | 1.000 |

The baseline made every binary merge/block decision correctly, but it recovered only 9 of 22 gold issue-category instances, with five extra category predictions. The missing and generic diagnoses establish the measurable opportunity for executable verification and evidence-grounded synthesis.

## Resource footprint

| Resource | Value |
|---|---:|
| Model calls | 24 |
| HTTP attempts | 24 |
| Input tokens | 33,348 |
| Output tokens | 10,702 |
| Total tokens | 44,050 |
| Provider wait time | 232.365 seconds |
| Total measured live wall time | 277.991 seconds |
| Median case wall time | 11.779 seconds |
| p95 case wall time | 13.692 seconds |

The provider wait is reported rather than omitted because the intervention must disclose its actual resource cost. The submitted offline replay reproduces the semantic outputs and comparable metrics without credentials or network access.

## Protocol consequence

Unsafe-change decision F1 is retained as a mandatory no-regression safety gate at 1.000. Because it is saturated, `docs/evaluation-protocol-v2.md` preregisters verified issue-category micro-F1 as the discriminative optimization metric before advanced implementation. No case input, gold label, or category label is changed.
