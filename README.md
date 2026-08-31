# DriftProof

**A green `dbt build` is not proof that an agent-authored repair preserves the business contract.**

DriftProof is an independent release gate for data engineers reviewing agent-authored dbt repairs. It rebuilds the candidate in an isolated disposable worktree, converts visible business requirements into executable checks, separates deterministic evidence from model hypotheses, and publishes a hash-bound approval report for a qualified human. It never merges or deploys code.

## Result on the frozen benchmark

<!-- DRIFTPROOF-METRICS:START -->
The frozen, balanced benchmark contains 24 project-authored candidates: 12 externally safe and 12 green-but-semantically-wrong. Both workflows receive the same candidate, visible business context, trajectory, and `dbt build` command; gold labels are opened only after predictions are written.

| Metric | Build-only baseline | DriftProof | Change |
|---|---:|---:|---:|
| Safe-approval macro-F1 | 0.333 | **0.681** | **+0.348** |
| Accuracy | 50.0% | **70.8%** | **+20.8 pp** |
| Unsafe-repair escape rate | 100.0% | **0.0%** | **-100.0 pp** |
| Safe candidates automatically approved | 12/12 | **5/12** | -7 |
| Unsafe candidates blocked from automatic approval | 0/12 | **12/12** | **+12** |
| Qualified-human escalations | 0/24 | **7/24** | +7 |

The measured trade-off is deliberate and visible: DriftProof reduced unsafe escapes from 100.0% to 0.0%, while automatically approving 5 of 12 safe candidates and escalating 7 cases to a qualified human. It does not claim universal correctness or formal verification.

The authoritative comparison, raw predictions, candidate bundles, and exact metric inputs are in [`results/driftproof-comparison/`](results/driftproof-comparison/).
<!-- DRIFTPROOF-METRICS:END -->

## Submission entry point

Start with [`submission/START_HERE.md`](submission/START_HERE.md), open the self-contained [`submission/START_HERE.html`](submission/START_HERE.html), or consume the exact machine contract in [`submission/manifest.json`](submission/manifest.json). The generated judge packet adds a concise [`submission/JUDGE_CHECKLIST.md`](submission/JUDGE_CHECKLIST.md), an evidence-bound [`submission/CLAIM_LEDGER.json`](submission/CLAIM_LEDGER.json), a complete [`submission/RUBRIC_MAP.json`](submission/RUBRIC_MAP.json), and content-addressed [`submission/AGENT_TRAJECTORIES.json`](submission/AGENT_TRAJECTORIES.json) plus [`submission/TRACE_INDEX.json`](submission/TRACE_INDEX.json). These files and the README metric table are regenerated from committed evidence; `make submission-check` rejects metric, trace, claim, or rubric drift.

## Credential-free judge demonstration

After installing the locked environment, one installed command demonstrates the central failure mode on two transparent fixtures:

```bash
uv sync --locked --extra dbt
uv run driftproof demo
```

The same command works from an installed wheel as `driftproof demo`; it does not depend on repository example files. Both fixtures pass the same build-only `dbt build` baseline. DriftProof then approves the contract-preserving repair and rejects the green-but-wrong repair. It independently verifies both bundles and prints their HTML report paths plus a typed `demo-receipt.json`. It uses no API key, external model, hidden label, merge, or deployment action. This paired demonstration is intentionally smaller than the frozen 24-case benchmark.

## Human workflow: onboard → edit contract → preflight → review

Prerequisites are Linux, Python 3.11 or later, [`uv`](https://docs.astral.sh/uv/), dbt, and a working rootless bubblewrap installation.

```bash
uv sync --locked --extra dbt
uv run driftproof --version
uv run driftproof doctor --json

# Plan only: no candidate execution and no file creation.
uv run driftproof onboard /absolute/path/to/dbt-project --run-id reviewer-1 --json

# Create only a missing BUSINESS_CONTEXT.md; never replace existing content.
uv run driftproof onboard /absolute/path/to/dbt-project \
  --run-id reviewer-1 \
  --apply \
  --json

# Edit the generated examples to state the real visible contract.
uv run driftproof preflight /absolute/path/to/dbt-project --json
uv run driftproof review /absolute/path/to/dbt-project --run-id reviewer-1
```

`onboard` returns exact argument vectors for context creation, preflight, review, and readiness checks. Planning is non-mutating; `--apply` atomically creates only a missing context file and never overwrites human-authored content. `preflight` snapshots the project and reports compiled typed rules plus unresolved statements, also without executing dbt. The review then chooses collision-resistant report/work paths from the absolute project identity and optional run ID, outside the candidate.

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
uv run driftproof schema response-verification
uv run driftproof doctor --json
```

Compute content identity before any candidate execution:

```bash
uv run driftproof fingerprint /absolute/path/to/dbt-project
```

The response distinguishes the configuration-only request hash from a content fingerprint that binds the candidate tree, visible context, installed DriftProof version, and review configuration. Control destinations and run IDs do not change that content identity. For the same semantic request, `configuration_request_sha256` must equal the later valid review response's `request_sha256`, even when the SDK assigns a unique control run ID. External provider responses are explicitly not claimed to be bound.

Python agents can avoid subprocess and JSON plumbing in their own code:

```python
from pathlib import Path

from driftproof.sdk import (
    ReviewRequest,
    fingerprint_for_agent,
    review_and_verify_for_agent,
)

request = ReviewRequest(
    project="candidate-project",
    context="candidate-project/BUSINESS_CONTEXT.md",
)
identity = fingerprint_for_agent(request, base_dir=Path.cwd())
response, verification = review_and_verify_for_agent(request, base_dir=Path.cwd())
assert verification.request_identity_verified
assert verification.request_sha256 == identity.configuration_request_sha256
assert verification.bundle_verified == verification.review_result_trusted
print(identity.content_fingerprint_sha256)
print(verification.model_dump_json(indent=2))
raise SystemExit(response.exit_code)
```

`review_and_verify_for_agent` first binds the semantic request identity, then rejects any response whose bundle path, verdict, hashes, check indexes, report paths, or verification command disagree with the independently verified bundle. It also reports which response fields are bundle-bound and which remain metadata-only. When neither `output` nor `run_id` is supplied, the SDK assigns a unique control run ID so independent concurrent callers receive disjoint bundles. For an intentional content-bound retry, use `request_with_stable_run_id`; changing candidate or context bytes changes that run ID. Existing outputs are still never replaced implicitly.

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

A valid response carries the tool version, request SHA-256, run ID, absolute project/context paths, candidate/context/build hashes, bundle and report paths, certificate and manifest hashes, exact failed/inconclusive check IDs, and a safe `verify_argv` vector. `driftproof verify-response` independently authenticates every bundle-backed field; request identity is additionally authenticated only when an independently computed expected request hash is supplied. The verification receipt explicitly lists metadata-only fields that the bundle cannot prove. Invalid responses include `partial_result_trusted: false` and never establish a trusted review result.

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
make submission-check
make release
make release-verify
```

Release packaging fails closed unless the worktree is clean, local `HEAD` equals private `origin/main`, required evidence and every qualified adversarial review exist in the commit, tracked objects are ordinary files, and the output directory contains no unrelated entries. It fully reads and CRC-verifies all three deterministic archives, safely extracts each one, verifies the embedded Git bundle, and confirms that the human/browser/machine submission entry points are byte-identical at the release root and inside every archive. `make release-verify` independently repeats those checks for a downloaded delivery.

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
