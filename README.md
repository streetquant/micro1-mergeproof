# DriftProof

**A green `dbt build` is not proof that an agent-authored repair preserves the business contract.**

DriftProof is an independent release gate for data engineers reviewing agent-authored dbt repairs. It rebuilds the candidate in an isolated disposable worktree, converts visible business requirements into executable checks, separates deterministic evidence from model hypotheses, and publishes a hash-bound approval report for a qualified human. It never merges or deploys code.

## Result on the frozen benchmark

The project-authored benchmark contains 24 paired candidates: 12 externally correct repairs and 12 green-but-semantically-wrong repairs. Both the build-only baseline and DriftProof receive the same candidate, business context, trajectory, and `dbt build` command. Gold labels are opened only after predictions are written.

| Metric | Build-only baseline | DriftProof | Change |
|---|---:|---:|---:|
| Safe-approval macro-F1 | 0.333 | **1.000** | **+0.667** |
| Accuracy | 50% | **100%** | **+50 pp** |
| Unsafe-repair escape rate | 100% | **0%** | **−100 pp** |
| Safe repairs approved | 12/12 | **12/12** | — |
| Unsafe repairs rejected | 0/12 | **12/12** | **+12** |

The raw predictions, per-candidate evidence, manifests, and exact metric computation are in [`results/driftproof-comparison/`](results/driftproof-comparison/). This is a balanced synthetic benchmark authored for this project; it is not evidence of universal correctness or formal verification.

## Human workflow: template → preflight → review

Prerequisites are Linux, Python 3.11 or later, [`uv`](https://docs.astral.sh/uv/), dbt, and a working rootless bubblewrap installation.

```bash
uv sync --locked --extra dbt
uv run driftproof --version
uv run driftproof doctor --json

uv run driftproof context-template \
  --output /absolute/path/to/dbt-project/BUSINESS_CONTEXT.md

# Edit the generated examples to state the real visible contract.
uv run driftproof preflight /absolute/path/to/dbt-project --json
uv run driftproof review /absolute/path/to/dbt-project --run-id reviewer-1
```

`context-template` writes a compilable starting point without executing candidate code. `preflight` snapshots the project and reports compiled typed rules plus unresolved statements, also without executing dbt. The review then chooses collision-resistant report/work paths from the absolute project identity and optional run ID, outside the candidate.

The human command prints the verdict, next state, HTML and machine report paths, certificate SHA-256, request identity, and the reminder that a qualified human still owns the final action.

Verify before trusting or sharing a result:

```bash
uv run driftproof verify-report /path/to/bundle
uv run driftproof inspect /path/to/bundle --json
```

For an intentional rerun at the same destination, add `--replace-output`. Replacement is allowed only for a recognizable prior or partial DriftProof bundle; unrelated files are never recursively removed. Use distinct `--run-id` values for independent concurrent reviews.

See [`docs/driftproof-human-guide.md`](docs/driftproof-human-guide.md) and the generated [`examples/BUSINESS_CONTEXT.template.md`](examples/BUSINESS_CONTEXT.template.md).

## AI-agent workflow: one versioned request object

Discover installed capabilities and schemas rather than scraping prose:

```bash
uv run driftproof capabilities
uv run driftproof schema request
uv run driftproof schema agent-response
uv run driftproof doctor --json
```

Start from [`examples/driftproof-request.json`](examples/driftproof-request.json), validate it against [`schemas/driftproof/request.schema.json`](schemas/driftproof/request.schema.json), then invoke:

```bash
status=0
uv run driftproof agent /absolute/path/to/driftproof-request.json || status=$?
```

A request file uses protocol `driftproof.request.v1`. Relative paths resolve from the request file’s parent. Standard input is also supported; its relative paths resolve from the caller’s current directory:

```bash
status=0
cat request.json | uv run driftproof agent - || status=$?
```

`driftproof agent` writes exactly one versioned JSON object to stdout. When `response_file` is present in the request, the same serialization is atomically written there. Request files are strict and may not be combined with conflicting review flags. Control-only response, replacement, and run-ID overrides are permitted without changing the bound review identity.

The four stable process states are:

| Exit | State | Required orchestrator action |
|---:|---|---|
| `0` | `approve` | Verify bundle, then enter a qualified-human checkpoint. Never auto-merge. |
| `10` | `reject` | Verify bundle and return exact failed checks to the implementation owner. |
| `20` | `human_review` | Verify bundle and escalate missing or ambiguous evidence. |
| `30` | `invalid_review` | Trust no partial result; repair input, isolation, provider, filesystem, or integrity failure. |

A valid response binds the tool version, request SHA-256, run ID, absolute project/context paths, candidate/context/build hashes, bundle and report paths, certificate and manifest hashes, exact failed/inconclusive check IDs, and a safe `verify_argv` vector. Invalid responses include `partial_result_trusted: false`.

Committed runtime-derived schemas live in [`schemas/`](schemas/); `python scripts/export_schemas.py --check` fails if they drift from executable models. The normative state machine and anti-patterns are in [`docs/driftproof-agent-protocol.md`](docs/driftproof-agent-protocol.md).

## Review bundle

A successful review atomically publishes exactly this directory:

```text
driftproof-review/
├── gate-report.json             # complete machine result and typed checks
├── approval-certificate.json    # self-hashed approval/rejection certificate
├── report.md                    # portable human report
├── report.html                  # self-contained browser report
└── manifest.json                # exact entry set, byte lengths, and SHA-256
```

`driftproof verify-report` rejects a missing, extra, symlinked, hash-invalid, schema-invalid, or internally inconsistent entry. A bundle is built in a sibling temporary directory, completely verified, and only then published with an atomic rename. If machine-response publication fails, the newly published recognizable bundle is removed before the process returns exit `30`.

## How DriftProof works

```text
candidate dbt project + visible business context + optional sanitized trajectory
                               │
                               ▼
                    immutable source snapshot
                               │
                               ▼
          networkless disposable dbt build under bubblewrap
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     deterministic contract compiler     optional bounded clarifier
              │                          (only with explicit consent)
              └────────────────┬────────────────┘
                               ▼
          semantic checks + source-unchanged verification
                               │
                               ▼
             deterministic verdict and hash-bound bundle
                               │
                               ▼
                    qualified-human checkpoint
```

The optional Contract Clarifier is used only when deterministic compilation cannot resolve visible business text. Proposals are constrained to typed rules and remain fail-closed. Selecting an external clarifier provider requires `--allow-external-provider`; without that explicit acknowledgment, no business context is sent to the provider.

## Trust boundary

For an untrusted candidate, DriftProof defaults to bubblewrap and:

- copies the candidate to a disposable worktree;
- leaves the original project tree unchanged and verifies its hash afterward;
- mounts system runtime dependencies read-only;
- clears and rebuilds the process environment;
- places home, cache, target, and temporary paths inside the isolated workspace;
- disables network access and dbt anonymous usage reporting;
- bounds wall time and normalizes durable command evidence;
- rejects bundle, work, response, record, and replay paths that overlap each other or the candidate;
- never invokes merge, push, deploy, email, ticket, or external mutation operations.

A trusted local fixture may explicitly request the weaker disposable-copy runner:

```bash
uv run driftproof review /path/to/trusted-fixture \
  --isolation disposable_copy \
  --allow-unconfined
```

Never use `--allow-unconfined` for untrusted candidate code.

## MergeProof protocol layer

This repository also contains `mergeproof`, the reusable human/AI-agent protocol used for generic Python patch review and shared reporting infrastructure. It provides safe Git intake, a narrow `unittest`/`py_compile` bubblewrap verifier, bounded and redacted provider-facing evidence, deterministic replay, content-addressed evidence, and self-verifying bundles.

```bash
uv run mergeproof capabilities
uv run mergeproof schema agent-response
uv run mergeproof review-git /path/to/repository \
  --task-file /safe/control/path/TASK.md \
  --command 'python -m unittest discover -s tests -q'
```

DriftProof is the scored product. MergeProof is supporting infrastructure, not a second hackathon problem. Human guidance is in [`docs/human-guide.md`](docs/human-guide.md); the autonomous protocol is in [`docs/agent-protocol.md`](docs/agent-protocol.md).

## Reproduce the evidence

Fast source qualification:

```bash
uv sync --locked --extra dev --extra dbt
make check
```

`make check` runs formatting, linting, strict typing, committed-schema drift verification, installed protocol smoke tests, the complete test suite, frozen replay verification, and package construction. The individual commands remain visible in the [`Makefile`](Makefile).

Complete qualification:

```bash
bash scripts/reproduce.sh
```

The complete reproduction verifies the pinned DriftDoctor boundary, regenerates and externally validates the paired dbt candidates, reruns the build-only baseline and DriftProof under the same dbt command, and mechanically compares the safety-metric projection with the committed comparison. Model replay is separately identified by request and fixture hashes; replay demonstrates deterministic processing of recorded responses, not unseen-input generalization.

After the final qualified commit is pushed to private `main`, deterministic source, evidence, and full archives are built with:

```bash
make release
```

Release packaging fails closed unless the worktree is clean, local `HEAD` equals private `origin/main`, required evidence exists in the commit, tracked objects are ordinary files, archive members are bounded and traversal-safe, and the embedded Git bundle verifies.

## Evidence map

| Claim | Authoritative artifact |
|---|---|
| Problem, user, and bottleneck | [`oracle/problem-brief.md`](oracle/problem-brief.md) |
| Requirements and threat model | [`docs/requirements.md`](docs/requirements.md) |
| Architecture and trust boundary | [`docs/architecture.md`](docs/architecture.md) |
| Human reviewer workflow | [`docs/driftproof-human-guide.md`](docs/driftproof-human-guide.md) |
| Autonomous-agent state machine | [`docs/driftproof-agent-protocol.md`](docs/driftproof-agent-protocol.md) |
| Runtime-derived machine contracts | [`schemas/manifest.json`](schemas/manifest.json) |
| Copy-ready request and context examples | [`examples/driftproof-request.json`](examples/driftproof-request.json), [`examples/BUSINESS_CONTEXT.template.md`](examples/BUSINESS_CONTEXT.template.md) |
| Frozen dbt cases and labels | [`benchmark_dbt/manifest.json`](benchmark_dbt/manifest.json) |
| Baseline versus DriftProof result | [`results/driftproof-comparison/comparison.json`](results/driftproof-comparison/comparison.json) |
| Raw baseline predictions | [`results/driftproof-comparison/baseline-raw.jsonl`](results/driftproof-comparison/baseline-raw.jsonl) |
| Raw DriftProof predictions | [`results/driftproof-comparison/advanced-raw.jsonl`](results/driftproof-comparison/advanced-raw.jsonl) |
| Pinned upstream boundary | [`docs/driftdoctor-upstream.md`](docs/driftdoctor-upstream.md) and [`upstream/driftdoctor.lock.json`](upstream/driftdoctor.lock.json) |
| Failed and removed experiments | [`CHANGELOG.md`](CHANGELOG.md) |
| Adversarial reviews and adjudications | [`reviews/`](reviews/) |

## Upstream provenance

The user-selected [`AaryaMody1301/DriftDoctor`](https://github.com/AaryaMody1301/DriftDoctor) repository is pinned, hash-verified, and credited as MIT-licensed prior work by another hackathon participant. Its fixture factory, oracle, reference repairs, repair workflow, and reported results are not claimed as original work here.

DriftProof’s contribution is the independent release gate: visible-contract compilation, semantic verification, isolated execution, immutable-source checking, bounded agent clarification, fail-closed evidence admission, human/agent protocols, and hash-bound reports and certificates. The exact boundary is documented in [`docs/driftdoctor-upstream.md`](docs/driftdoctor-upstream.md).

## Limitations

- The scored benchmark is synthetic and project-authored.
- Supported business rules are intentionally typed and finite; unresolved text results in `human_review`.
- Bubblewrap verification currently requires Linux.
- Pattern-based redaction reduces recognized secret exposure but cannot classify every confidential value.
- The optional clarifier consumes more resources than deterministic review; provider/model differences must be disclosed in comparisons.
- Replay proves processing reproducibility for recorded responses, not model determinism or unseen-project generalization.
- An approval certificate is decision support for a qualified human, never permission to merge or deploy automatically.
