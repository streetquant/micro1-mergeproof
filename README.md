# DriftProof

**A green `dbt build` is not proof that an agent-authored repair preserves the business contract.**

DriftProof is an independent, evidence-grounded release gate for agent-authored dbt repairs. It rebuilds a candidate inside an isolated workspace, compiles visible business requirements into executable checks, challenges unsupported claims, and emits a hash-bound report and approval certificate for a qualified human. It never merges or deploys code.

This repository has two deliberately separated layers:

| Layer | Purpose | Command |
|---|---|---|
| **DriftProof** | The scored dbt-specific product and benchmarked semantic release gate. | `driftproof` |
| **MergeProof protocol** | A reusable human/AI-agent intake, sandbox, reporting, and self-verifying bundle interface used by DriftProof and generic Python patch reviews. | `mergeproof` |

MergeProof is infrastructure inside the submission, not a competing product. The hackathon problem is the unsafe approval of agent-authored data-pipeline repairs.

## Verified result

The frozen DriftProof benchmark contains 24 paired candidates: 12 externally correct repairs and 12 candidates that pass their own `dbt build` but violate a visible business contract. Both systems receive the same candidate, context, trajectory, and dbt command; gold labels are opened only after predictions are written.

| Metric | Build-only baseline | DriftProof |
|---|---:|---:|
| Safe-approval macro-F1 | 0.333 | **1.000** |
| Accuracy | 50% | **100%** |
| Unsafe-repair escape rate | 100% | **0%** |
| Safe candidates correctly approved | 12/12 | **12/12** |
| Unsafe candidates correctly rejected | 0/12 | **12/12** |

The raw predictions, per-candidate bundles, manifests, and aggregate comparison are in [`results/driftproof-comparison/`](results/driftproof-comparison/). This is a project-authored synthetic benchmark, not a claim of universal correctness or formal verification.

## Install and diagnose

Prerequisites:

- Linux with Python 3.11 or later;
- [`uv`](https://docs.astral.sh/uv/);
- `git`;
- `bubblewrap` (`bwrap`) for verified execution;
- Docker is **not** required.

```bash
uv sync --locked --extra dev --extra dbt
uv run mergeproof doctor --json
```

`doctor` returns a machine-readable readiness report and never prints provider credentials.

## 30-second review of a Git working tree

Use this for a local Python patch. The task should state both the requested behavior and anything that must remain unchanged.

```bash
set +e
uv run mergeproof review-git . \
  --base HEAD \
  --task 'Handle an empty value without changing non-empty behavior.' \
  --command 'python -m unittest discover -s tests -q' \
  --output mergeproof-review \
  --json > mergeproof-decision.json
status=$?
set -e

cat mergeproof-decision.json
uv run mergeproof verify-bundle mergeproof-review --json
exit "$status"
```

For a committed pull-request branch, use a base such as `--base origin/main`. Repeat `--command` or `--allow` for multiple verification commands and allowed path globs.

### Stable process exit codes

| Code | Meaning | Recommended automation behavior |
|---:|---|---|
| `0` | `approve` | Continue to the required human checkpoint. Do not auto-merge. |
| `10` | `reject` | Stop and return the verified blockers to the implementation agent. |
| `20` | `human_review` | Stop; evidence is incomplete or ambiguous. |
| `30` | Tool, input, readiness, or bundle-integrity error | Treat the review as invalid and investigate. |

## Review bundle

A successful invocation writes one self-contained directory:

```text
mergeproof-review/
├── request.json          # immutable review input
├── result.json           # complete machine result
├── evidence.jsonl        # content-addressed evidence ledger
├── agent-traces.json     # admitted agent calls and gate feedback
├── report.md             # concise human report
├── report.html           # self-contained browser report
└── manifest.json         # byte lengths and SHA-256 for every artifact
```

Verify it independently:

```bash
uv run mergeproof verify-bundle mergeproof-review --json
```

Any changed, missing, extra, or hash-invalid artifact causes exit code `30`.

## AI-agent integration contract

The interface is designed to be consumed without scraping prose.

### 1. Discover schemas

```bash
uv run mergeproof schemas > mergeproof-schemas.json
```

The output contains JSON Schemas for the review request, result, evidence record, finding, and agent trace. A complete copyable request is available at [`examples/mergeproof-request.json`](examples/mergeproof-request.json).

### 2. Prepare a versioned request

```bash
uv run mergeproof prepare . \
  --base origin/main \
  --task 'Exact requirement plus preservation constraints.' \
  --command 'python -m unittest discover -s tests -q' \
  --allow 'src/**' \
  --allow 'tests/**' \
  --output mergeproof-request.json \
  --json
```

### 3. Review from a file or standard input

```bash
uv run mergeproof review mergeproof-request.json \
  --mode verified \
  --output mergeproof-review \
  --json

# Equivalent streaming form:
cat mergeproof-request.json | \
  uv run mergeproof review - --mode verified --output mergeproof-review --json
```

In `--json` mode, stdout is one JSON object. Human diagnostics go to stderr. Agents should use the exit code as the control signal and `result.json` as the authoritative detailed result.

### 4. Verify before trusting or forwarding

```bash
uv run mergeproof verify-bundle mergeproof-review --json
```

An agent must not report success merely because a review directory exists. It must verify the bundle and preserve the human-approval boundary.

More detailed protocol guidance is in [`docs/agent-integration.md`](docs/agent-integration.md).

## DriftProof dbt review

A dbt candidate should include its `dbt_project.yml`, local `profiles.yml`, and visible business context, normally `BUSINESS_CONTEXT.md`.

```bash
uv run driftproof review ./candidate-project \
  --context ./candidate-project/BUSINESS_CONTEXT.md \
  --isolation bubblewrap \
  --output results/driftproof-review
```

Outputs include:

- `gate-report.json`;
- `approval-certificate.json`;
- `report.md`;
- `report.html`;
- an integrity manifest.

Verify the certificate/report pair:

```bash
uv run driftproof verify-bundle \
  results/driftproof-review/gate-report.json \
  results/driftproof-review/approval-certificate.json
```

`bubblewrap` is the default trust boundary. A trusted local fixture can use the weaker runner only with an explicit acknowledgment:

```bash
uv run driftproof review ./trusted-fixture \
  --isolation disposable_copy \
  --allow-unconfined
```

Never use `--allow-unconfined` for untrusted candidate code.

## Trust boundary

The verified MergeProof runner:

- materializes only validated relative paths;
- mounts the candidate read-only at `/workspace`;
- clears the parent environment and rebuilds a minimal non-secret environment;
- fixes `PYTHONPATH=/workspace` rather than inheriting it;
- unshares network, process, mount, IPC, UTS, cgroup, and user namespaces through bubblewrap;
- invokes a fixed `/usr/bin/python` runtime;
- permits only `python -m unittest` and `python -m py_compile` with path-safe arguments;
- bounds wall/CPU time, output file size, open descriptors, temporary storage, and captured output;
- records normalized stdout, stderr, command policy, and evidence hashes;
- never invokes merge, push, deploy, email, or ticket mutations.

DriftProof additionally runs dbt in an isolated disposable worktree and verifies that the original candidate tree remains unchanged. A provider error, malformed model output, missing evidence, failed verification, or broken evidence reference cannot produce automatic approval.

## Reproduce the evidence

Fast source and replay qualification:

```bash
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src/driftproof src/mergeproof
uv run pytest -q
uv run python scripts/verify_replay.py
```

One-command qualification is available through:

```bash
bash scripts/reproduce.sh
```

The full reproduction fetches and verifies the pinned DriftDoctor upstream, regenerates the paired candidate set in `.work/`, runs the external oracle, reruns the DriftProof comparison under bubblewrap, and mechanically compares the safety metrics with the committed result.

## Evidence map

| Claim | Evidence |
|---|---|
| Frozen problem and user | [`oracle/problem-brief.md`](oracle/problem-brief.md) |
| Requirements and threat model | [`docs/requirements.md`](docs/requirements.md) |
| Architecture and trust boundary | [`docs/architecture.md`](docs/architecture.md) |
| Baseline live run and replay | [`docs/baseline-results.md`](docs/baseline-results.md), [`results/baseline-live-groq-gpt-oss-20b/`](results/baseline-live-groq-gpt-oss-20b/) |
| DriftProof comparison | [`results/driftproof-comparison/comparison.json`](results/driftproof-comparison/comparison.json) |
| Frozen dbt benchmark | [`benchmark_dbt/manifest.json`](benchmark_dbt/manifest.json) |
| Pinned upstream boundary | [`docs/driftdoctor-upstream.md`](docs/driftdoctor-upstream.md), [`upstream/driftdoctor.lock.json`](upstream/driftdoctor.lock.json) |
| Failed and removed experiments | [`CHANGELOG.md`](CHANGELOG.md) |
| Adversarial reviews and dispositions | [`reviews/`](reviews/) |

## Upstream provenance

The user-selected [`AaryaMody1301/DriftDoctor`](https://github.com/AaryaMody1301/DriftDoctor) repository is pinned, cryptographically verified, MIT-licensed prior work by another hackathon participant. Its fixture factory, oracle, reference repairs, repair workflow, and reported results are not claimed as work from this repository.

DriftProof’s original contribution is the independent release gate: visible-contract compilation, semantic checks, isolated execution, immutable-source verification, fail-closed evidence admission, reports, and hash-bound certificates. The exact boundary is documented in [`docs/driftdoctor-upstream.md`](docs/driftdoctor-upstream.md).

## Limitations

- The scored dataset is synthetic and project-authored; it does not establish universal safety.
- Generic MergeProof verification currently allow-lists Python standard-library test and compile modules. DriftProof provides the specialized dbt path.
- Verified execution currently requires Linux bubblewrap.
- Replay verifies deterministic processing of recorded model responses; it is not an unseen-input generalization test.
- A valid certificate informs a qualified human decision. It is not permission to merge automatically.
