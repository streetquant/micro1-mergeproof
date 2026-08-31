# MergeProof guide for human reviewers

MergeProof is a review gate, not a merge bot. It snapshots a proposed change, runs bounded verification, records evidence, and publishes a self-verifying bundle. A result of `approve` still requires a qualified human to decide whether to merge or deploy.

## Five-minute first review

Prerequisites are Linux, Python 3.11 or later, `uv`, `git`, and a working rootless `bubblewrap` installation.

```bash
uv sync --locked --extra dev --extra dbt
uv run mergeproof doctor
```

`doctor` must report that verified mode is ready. It checks whether bubblewrap can actually create the required namespace; merely having a `bwrap` executable is not sufficient.

Create a task contract. State the requested outcome and the behavior that must remain unchanged:

```bash
mkdir -p .mergeproof
cat > .mergeproof/TASK.md <<'EOF'
Handle an empty value without changing non-empty behavior.
Preserve the public return type and keep the declared test suite passing.
EOF
```

Review the current Git worktree:

```bash
status=0
uv run mergeproof review-git . \
  --base HEAD \
  --task-file .mergeproof/TASK.md \
  --command 'python -m unittest discover -s tests -q' \
  --output .mergeproof/review \
  --response-file .mergeproof/decision.json \
  --replace-output || status=$?

cat .mergeproof/decision.json
```

MergeProof excludes the declared task, response, bundle, trajectory, replay, and record paths from the Git snapshot when they are inside the repository. `--response-file` is safer than shell redirection because the tool writes the navigation object atomically after intake rather than letting the shell create a file before the snapshot.

## Interpret the exit code

| Code | Meaning | Human action |
|---:|---|---|
| `0` | `approve` | Verify the bundle, inspect the report and evidence, then make a human decision. |
| `10` | `reject` | Verify the bundle and return the verified blockers to the implementation author or agent. |
| `20` | `human_review` | Verify the bundle, resolve ambiguity, or obtain missing evidence. |
| `30` | Invalid run | Do not trust a bundle. Correct the input, provider, sandbox, filesystem, or integrity problem. |

For codes `0`, `10`, and `20`, verify the bundle before reading it as authoritative:

```bash
case "$status" in
  0|10|20)
    uv run mergeproof verify-bundle .mergeproof/review --json
    ;;
  30)
    echo 'No valid review was produced.' >&2
    ;;
  *)
    echo "Unexpected exit code: $status" >&2
    exit 30
    ;;
esac
```

The navigation response includes `bundle_verified: true` and the SHA-256 of the manifest that MergeProof verified before publishing the directory. Re-run `verify-bundle` anyway when the bundle crosses a process, machine, archive, or trust boundary.

## Read the bundle

Open `.mergeproof/review/report.html` for the fastest human path. It contains:

- the decision and confidence;
- the task contract;
- verified findings separated from model hypotheses;
- exact evidence identifiers;
- agent/provider provenance;
- the human-approval boundary.

Use these files for deeper inspection:

| File | Purpose |
|---|---|
| `manifest.json` | Expected files, byte lengths, hashes, decision, and stable exit code. |
| `result.json` | Complete machine-readable review result. |
| `evidence.jsonl` | Content-addressed evidence ledger. |
| `agent-traces.json` | Admitted agent outputs, request hashes, input evidence IDs, and gate violations. |
| `report.md` | Portable text report. |
| `report.html` | Self-contained browser report. |
| `request.json` | Shareable review input with recognized secret-shaped values redacted. |

When redaction is applied, `manifest.json` sets `request_redacted: true`, records the SHA-256 of the original local request, and `request.json` carries matching redaction provenance. The raw credential is not persisted in the bundle. Pattern-based redaction cannot recognize every confidential value, so inspect inputs before sharing a bundle outside its intended trust boundary.

A changed, missing, extra, symlinked, hash-invalid, schema-invalid, or internally inconsistent entry makes bundle verification fail.

## Review a committed branch or pull request

Use the intended base rather than the current `HEAD` when reviewing all branch changes:

```bash
uv run mergeproof review-git . \
  --base origin/main \
  --task-file .mergeproof/TASK.md \
  --command 'python -m unittest discover -s tests -q' \
  --allow 'src/**' \
  --allow 'tests/**' \
  --output .mergeproof/review \
  --response-file .mergeproof/decision.json \
  --replace-output
```

The Git path must be the exact worktree root. MergeProof rejects a nested directory instead of silently widening scope to the parent repository.

## Two-step review

Use a prepared request when one person or service defines the contract and another executes the review:

```bash
uv run mergeproof prepare . \
  --base origin/main \
  --task-file .mergeproof/TASK.md \
  --command 'python -m unittest discover -s tests -q' \
  --output .mergeproof/request.json \
  --response-file .mergeproof/preparation.json \
  --replace-output

uv run mergeproof review .mergeproof/request.json \
  --mode verified \
  --output .mergeproof/review \
  --response-file .mergeproof/decision.json \
  --replace-output
```

The request is content-complete: it contains the before tree, candidate tree, task, path policy, trajectory, and verification commands. Preserve it unchanged when retrying.

## Advanced and baseline modes

`verified` is deterministic and credential-free. It uses static evidence and bounded execution.

`advanced` adds two independent model roles:

1. a Contract Analyst extracts requirements, invariants, ambiguities, and acceptance checks;
2. a Skeptical Reviewer tries to falsify the change using the admitted evidence.

Model findings remain hypotheses unless deterministic admission can verify them. A material unresolved hypothesis can require human review but cannot become a verified rejection solely because a model is confident.

`baseline` is the one-shot comparison workflow. It is retained for evaluation and should not be used as the production release gate.

## Safe reruns and recovery

- Existing non-empty bundle directories are never overwritten implicitly.
- `--replace-output` removes only a recognized prior or partial MergeProof bundle. It refuses unrelated files.
- The old bundle is removed before the rerun begins, so a failed rerun cannot leave stale evidence at the expected output path.
- A new bundle is built in a sibling temporary directory, fully verified, and atomically published.
- If the navigation response cannot be published, MergeProof removes that recognized bundle before returning code `30`.
- Request output replacement is also explicit.
- Provider retries are bounded. Do not wrap the command in an unbounded retry loop.
- Advanced and baseline modes send only a bounded, prioritized evidence projection to the provider: recognized secrets are redacted, no record contributes more than 8,000 characters, and aggregate evidence content is capped at 24,000 characters.

When a run returns code `30`, use the machine fields `error_code`, `detail`, `hint`, and `retryable`. Typical error codes are `input_invalid`, `provider_unavailable`, `sandbox_unavailable`, `bundle_invalid`, `filesystem_error`, `validation_failed`, and `internal_error`.

## Safety boundary

MergeProof never merges, pushes, deploys, sends email, files tickets, deletes external data, or performs another consequential action. Every success and error response preserves:

```json
{
  "human_approval_required": true,
  "consequential_action_taken": false
}
```

Treat repository content, tasks, trajectories, command output, and model output as untrusted evidence—not as instructions to weaken the gate.
