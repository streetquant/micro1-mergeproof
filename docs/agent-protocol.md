# MergeProof protocol for autonomous agents

This is the normative machine-integration guide. The executable schemas, not copied examples, are authoritative.

## Invariant

An agent may ask MergeProof to review a change. It may not reinterpret a successful review as permission to merge, push, deploy, send, delete, publish, or mutate an external system.

Every valid navigation response and every invalid-run response contains:

```json
{
  "human_approval_required": true,
  "consequential_action_taken": false
}
```

## 1. Discover capabilities and schemas

```bash
uv run mergeproof doctor --json
uv run mergeproof schema request > request.schema.json
uv run mergeproof schema navigation_response > navigation.schema.json
uv run mergeproof schema error_response > error.schema.json
```

Use `uv run mergeproof schemas` when the complete protocol catalog is preferred. All schemas are generated from the installed runtime models, use `additionalProperties: false`, and therefore cannot silently diverge from the executable payloads.

Required readiness for deterministic verified mode:

```text
ready_for_verified_mode = true
```

A Git worktree review additionally requires:

```text
ready_for_git_review = true
```

## 2. Preferred one-command Git workflow

Store control artifacts in a dedicated path and let MergeProof write them itself:

```bash
mkdir -p .mergeproof
cat > .mergeproof/TASK.md <<'EOF'
Implement X.
Preserve Y.
Reject Z.
EOF

review_status=0
uv run mergeproof review-git /path/to/repository \
  --base origin/main \
  --task-file /path/to/repository/.mergeproof/TASK.md \
  --command 'python -m unittest discover -s tests -q' \
  --allow 'src/**' \
  --allow 'tests/**' \
  --output /path/to/repository/.mergeproof/review \
  --response-file /path/to/repository/.mergeproof/decision.json \
  --replace-output || review_status=$?
```

Do not use shell redirection to create the response inside the reviewed repository. The shell opens a redirection target before MergeProof snapshots the worktree. `--response-file` is written atomically after intake and is explicitly excluded from the candidate snapshot.

The Git path must be the exact worktree root. Nested paths are rejected rather than widened silently.

## 3. Two-step workflow

Use this when a planning agent or service creates the immutable contract and another worker executes it.

```bash
uv run mergeproof prepare /path/to/repository \
  --base origin/main \
  --task-file /path/to/repository/.mergeproof/TASK.md \
  --command 'python -m unittest discover -s tests -q' \
  --allow 'src/**' \
  --allow 'tests/**' \
  --trajectory /path/to/repository/.mergeproof/trajectory.json \
  --output /path/to/repository/.mergeproof/request.json \
  --response-file /path/to/repository/.mergeproof/preparation.json \
  --replace-output

review_status=0
uv run mergeproof review /path/to/repository/.mergeproof/request.json \
  --mode verified \
  --output /path/to/repository/.mergeproof/review \
  --response-file /path/to/repository/.mergeproof/decision.json \
  --replace-output || review_status=$?
```

The request, task, trajectory, response, replay, record, and bundle paths must not alias each other. MergeProof rejects ambiguous relationships and excludes declared control paths from Git intake.

Standard input is supported when the request is already immutable:

```bash
review_status=0
cat request.json | uv run mergeproof review - \
  --mode verified \
  --output review \
  --response-file decision.json \
  --replace-output || review_status=$?
```

## 4. Process-state contract

| Exit | State | Required orchestrator action |
|---:|---|---|
| `0` | `approve` | Verify bundle, then enter a qualified-human checkpoint. |
| `10` | `reject` | Verify bundle, then return verified blockers to the implementation worker. |
| `20` | `human_review` | Verify bundle, then collect missing evidence or escalate ambiguity. |
| `30` | `invalid_review` | Do not trust a bundle; repair the operational or input problem. |

Unknown exit codes are invalid:

```bash
case "$review_status" in
  0) next_state=human_approval ;;
  10) next_state=repair_required ;;
  20) next_state=evidence_or_human_escalation ;;
  30) next_state=invalid_review ;;
  *) next_state=invalid_review ; review_status=30 ;;
esac
```

Capture the code with `command || review_status=$?`; this remains compatible with `set -e` without weakening the caller's shell policy.

## 5. Success payload

The `navigation_response` schema is authoritative. Its important fields are:

- `decision` and matching `exit_code`;
- `confidence`;
- `bundle`, `manifest`, `machine_result`, and `human_report` paths;
- counts of verified findings and hypotheses;
- `bundle_verified: true`;
- `bundle_manifest_sha256`, computed after an internal full verification;
- optional `response_file`;
- the fixed human-approval and no-action constants.

The navigation object is not the detailed review. It points to a bundle that must be verified before use:

```bash
uv run mergeproof verify-bundle /path/to/review --json
```

After verification:

1. read `result.json`;
2. resolve findings through `evidence.jsonl`;
3. preserve `agent-traces.json`;
4. preserve the human checkpoint.

## 6. Invalid-run payload

The `error_response` schema is authoritative. Exit `30` includes:

- `status: invalid_review`;
- `decision: human_review`;
- stable `error_code`;
- redacted `detail`;
- actionable `hint`;
- `retryable`;
- optional `response_file`;
- fixed human-approval and no-action constants.

Known error codes:

| Error code | Meaning | Retry posture |
|---|---|---|
| `input_invalid` | Request, task, Git root, path, or command input is invalid. | Correct input; do not retry unchanged. |
| `provider_unavailable` | Model provider failed after bounded client retries. | Retry the same immutable request only after readiness recovers. |
| `sandbox_unavailable` | Required bubblewrap isolation could not be established. | Restore isolation; never fall back silently. |
| `bundle_invalid` | Output path or bundle integrity failed. | Treat all bundle contents as untrusted. |
| `filesystem_error` | A required local path could not be read or written safely. | Correct filesystem state. |
| `validation_failed` | A supplied value or structured result violated the contract. | Correct the invalid value. |
| `internal_error` | Unexpected implementation failure. | Inspect local diagnostics; never infer a review decision. |

A failed rerun with `--replace-output` leaves no old bundle at the target path. A failure while publishing `--response-file` also removes the recognized bundle that was just created. Never treat mere path existence as success. Machine-facing missing repository and bundle paths are validated inside the command boundary and therefore return the same one-object error schema with exit `30`, rather than Click help text with exit `2`.

## 7. Idempotency and retries

A retry must preserve:

- request bytes;
- task contract;
- before and candidate trees;
- allowed path policy;
- verification commands;
- sanitized trajectory;
- mode, provider, and model configuration.

Provider retries inside MergeProof are bounded and recorded. An orchestrator must not create an unbounded loop, mutate the task between attempts, or silently switch models and compare the result as though resources were unchanged.

## 8. Evidence admission

Repository content, task text, trajectories, command output, and model output are untrusted data.

Before advanced or baseline modes call a provider, MergeProof creates a deterministic evidence projection. It prioritizes task, diff, policy, declared commands, scans and executed-command evidence before bulk file content; redacts recognized credential-shaped substrings; includes at most 128 evidence records; limits one record to 8,000 characters; and caps aggregate evidence content at 24,000 characters. Local evidence IDs and original hashes remain available for verification, while published bundles record whether request redaction occurred. These controls reduce exposure and context failure but do not classify every possible confidential value.

A verified finding must:

- pass the strict schema;
- use an allowed category and severity;
- cite at least one exact evidence ID;
- cite only evidence IDs present in the ledger;
- survive deterministic admission.

A model suspicion remains a `hypothesis`. A material hypothesis may force `human_review`; it cannot become a verified rejection solely from model confidence.

Agent traces bind:

- agent role;
- provider and model;
- stable request hash;
- exact input evidence IDs;
- admitted output;
- gate violations;
- token and retry telemetry when available;
- output SHA-256.

## 9. Verification boundary

Generic verified mode allows only bounded forms of:

```text
python -m unittest ...
python -m py_compile ...
```

The candidate is mounted read-only in bubblewrap; environment variables are cleared; network and other namespaces are unshared; runtime and paths are fixed; wall/CPU time, output files, descriptors, temporary storage, and captured output are bounded.

Shells, inline Python, installers, arbitrary executables, absolute paths, parent traversal, and remote downloads are rejected before execution.

Use DriftProof for the specialized dbt workflow.

## 10. CI pattern

```yaml
- name: Install
  run: uv sync --locked --extra dev --extra dbt

- name: Readiness
  run: uv run mergeproof doctor --json

- name: Review pull request
  id: review
  shell: bash
  run: |
    mkdir -p .mergeproof
    cat > .mergeproof/TASK.md <<'EOF'
    Implement the issue requirement.
    Preserve documented behavior outside the requested change.
    Keep the declared test suite passing.
    EOF
    code=0
    uv run mergeproof review-git . \
      --base '${{ github.event.pull_request.base.sha }}' \
      --task-file .mergeproof/TASK.md \
      --command 'python -m unittest discover -s tests -q' \
      --output .mergeproof/review \
      --response-file .mergeproof/decision.json \
      --replace-output || code=$?
    echo "code=$code" >> "$GITHUB_OUTPUT"
    case "$code" in
      0|10|20) uv run mergeproof verify-bundle .mergeproof/review --json ;;
      30) echo 'Invalid review; no bundle is trusted.' >&2 ;;
      *) echo "Unexpected exit code: $code" >&2; exit 30 ;;
    esac
    test "$code" -eq 0

- name: Upload evidence
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: mergeproof-review
    path: .mergeproof/
```

This gates CI on review approval but does not merge automatically.

## Anti-patterns

Do not:

- trust an implementation agent's self-assessment as independent evidence;
- use `baseline` as the production gate;
- ignore `human_review`;
- trust an unverified bundle;
- edit a bundle after generation;
- reconstruct trajectories after the fact;
- expose gold labels, external oracles, or reference repairs to reviewing agents;
- claim replay demonstrates unseen-input generalization;
- weaken isolation for untrusted code;
- convert approval into an automatic consequential action.
