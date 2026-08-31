# DriftProof guide for human reviewers

DriftProof is an independent release gate for agent-authored dbt repairs. It verifies a visible business contract and produces evidence for a qualified human; it never merges or deploys code.

## Fastest safe workflow

### 0. See the failure mode on transparent fixtures

```bash
uv sync --locked --extra dev --extra dbt
make judge-demo
```

The credential-free demonstration proves that both fixtures pass build-only review, while DriftProof approves the contract-preserving repair and rejects the green-but-wrong repair. It prints independently verified report paths and a content-addressed receipt.

### 1. Install and diagnose

```bash
uv sync --locked --extra dbt
uv run driftproof --version
uv run driftproof doctor --json
```

A production review requires Linux, dbt, and a working rootless bubblewrap namespace. Provider credentials are not required for deterministic review. Doctor output identifies missing requirements, remediation, and the next argument vector.

### 2. Onboard the real project

Plan setup without executing candidate code or creating files:

```bash
uv run driftproof onboard /absolute/path/to/dbt-project --run-id reviewer-1 --json
```

Create only a missing context template:

```bash
uv run driftproof onboard /absolute/path/to/dbt-project \
  --run-id reviewer-1 \
  --apply \
  --json
```

`--apply` is no-clobber: it never replaces an existing context file, including one created concurrently. Edit the generated file so it states the real contract. Remove example rules that do not apply. Do not copy hidden labels, expected benchmark answers, secrets, or reference repairs into the context.

### 3. Preflight without executing dbt

```bash
uv run driftproof preflight /absolute/path/to/dbt-project --json
```

Preflight validates the project snapshot and context, then reports:

- project and context SHA-256;
- SQL/YAML/model/reference counts;
- compiled rule count and kinds;
- unresolved business statements;
- `run_review` or `clarify_business_context` as the next action.

Preflight does **not** execute dbt or candidate code. An unresolved statement is not silently ignored; deterministic review will escalate it to `human_review` unless an explicitly authorized clarifier resolves it into an admitted typed rule.

### 4. Run the isolated review

```bash
uv run driftproof review /absolute/path/to/dbt-project \
  --run-id reviewer-1
```

DriftProof derives collision-resistant report and work paths from the absolute project identity plus the optional run ID. The paths are outside the candidate project. Supply `--output` and `--work-root` only when another system requires explicit destinations.

### 5. Verify before trusting the result

The human command prints the bundle path. Verify and inspect it independently:

```bash
uv run driftproof verify-report /path/to/bundle
uv run driftproof inspect /path/to/bundle --json
xdg-open /path/to/bundle/report.html
```

A valid bundle still ends at a qualified-human checkpoint.

## Supported visible-contract patterns

DriftProof intentionally accepts a finite typed contract rather than pretending to understand arbitrary prose.

| Rule kind | Example intent |
|---|---|
| `public_contract` | Required public output columns |
| `required_identifier` | A key field must be non-null |
| `derived_concat` | A field is a trimmed concatenation of source fields |
| `numeric_null_policy` | Invalid numeric values become null with explicit decimal handling |
| `latest_record` | One latest row per business key |
| `categorical_mapping` | Explicit source-to-public value mapping |
| `timezone_date` | Convert from one timezone before date casting |
| `subtraction_formula` | An output equals one field minus another |
| `source_alias` | A named source maps to a declared relation |
| `dependency_exists` | A required dbt dependency/reference exists |
| `preserve_field` | A field must remain present |
| `macro_keyword` | A macro call preserves a required keyword argument |

Run `driftproof context-template` for compilable examples and `driftproof compile-contract BUSINESS_CONTEXT.md` to see the admitted rules.

## Interpret process states

| Exit | Verdict/state | Human action |
|---:|---|---|
| `0` | `approve` | Verify the bundle, inspect evidence, then make a human release decision. |
| `10` | `reject` | Verify the bundle and return the failed checks to the implementation owner. |
| `20` | `human_review` | Verify the bundle, then resolve ambiguity or obtain missing evidence. |
| `30` | `invalid_review` | Trust no partial result. Repair the input, runtime, isolation, provider, path, or integrity failure. |

An approval is not permission for automatic merge or deployment.

## Read the bundle

A valid bundle contains exactly:

```text
bundle/
├── gate-report.json
├── approval-certificate.json
├── report.md
├── report.html
└── manifest.json
```

Use `report.html` for the fastest path. It shows the verdict, next action, build isolation, visible contract, pass/fail/inconclusive checks, evidence references, optional clarifier trace, integrity hashes, and fixed human-approval boundary.

Use the machine artifacts when investigating:

- `gate-report.json`: authoritative check results and source/build hashes;
- `approval-certificate.json`: self-hashed binding of candidate, context, build, verdict, and check indexes;
- `manifest.json`: exact entry set, sizes, and file hashes.

`verify-report` rejects missing, extra, symlinked, hash-invalid, schema-invalid, or internally inconsistent entries.

## Safe reruns and concurrency

A prior bundle is never overwritten implicitly.

```bash
uv run driftproof review /absolute/path/to/project \
  --run-id reviewer-1 \
  --replace-output
```

`--replace-output` removes only a recognizable prior or partial DriftProof bundle. It refuses unrelated files.

For concurrent or independently attributable reviews, assign distinct run IDs:

```bash
uv run driftproof review /absolute/path/to/project --run-id security-review
uv run driftproof review /absolute/path/to/project --run-id release-review
```

The default project-path hash prevents two projects with the same directory name from sharing a destination. The run ID separates multiple reviews of the same project.

## Optional Contract Clarifier

Deterministic review is credential-free. An optional bounded clarifier may propose typed rules for unresolved visible text. External providers require explicit authorization:

```bash
uv run driftproof review /absolute/path/to/project \
  --agent-provider groq \
  --allow-external-provider
```

Without `--allow-external-provider`, DriftProof fails before transmitting context. Provider proposals do not gain merge authority and unresolved text still fails closed.

Use replay fixtures for offline reproduction:

```bash
uv run driftproof review /absolute/path/to/project \
  --agent-provider replay \
  --agent-replay-dir /safe/read-only/fixtures
```

Record, replay, response, bundle, work, and candidate paths must be disjoint.

## Trusted-fixture mode

Bubblewrap is the required boundary for untrusted candidate code. A known local fixture may explicitly use the weaker disposable-copy runner:

```bash
uv run driftproof review /absolute/path/to/trusted-fixture \
  --isolation disposable_copy \
  --allow-unconfined
```

Never use this mode for an untrusted contribution.

## Troubleshooting

Start with the machine error object:

```bash
uv run driftproof agent /absolute/path/to/project
```

Exit `30` includes a stable `error_code`, redacted detail, actionable hint, retry posture, `partial_result_trusted: false`, and the no-action constants.

Common repairs:

- `review_execution_failed`: correct project/context/path/isolation/dbt input;
- `external_provider_consent_required`: obtain owner authorization or remove the external provider;
- `provider_unavailable`: restore provider readiness and retry the same immutable request;
- `bundle_invalid`: discard the bundle and use a dedicated safe output path;
- `filesystem_error`: correct permissions or unsafe file types;
- `validation_failed`: correct the versioned request or option value;
- `internal_error`: inspect local diagnostics and infer no verdict.

## Limitations

DriftProof verifies only supported visible rules and executable evidence. It does not prove universal semantic correctness, replace domain expertise, classify every confidential value, establish model generalization from replay, or authorize consequential actions.
