# DriftProof judge checklist

This is the shortest evidence-first evaluation path. No command below merges or deploys code.

## 1. Understand the problem and measured trade-off

- Open [`START_HERE.md`](START_HERE.md).
- Check [`CLAIM_LEDGER.json`](CLAIM_LEDGER.json) before relying on a headline claim.
- Confirm the frozen result: macro-F1 `0.333` → `0.681`, accuracy `50.0%` → `70.8%`, unsafe escapes `100.0%` → `0.0%`.
- Note the conservative cost: only `5/12` safe candidates were auto-approved and `7/24` cases escalated.

## 2. Run the installed end-to-end demonstration

```bash
uv sync --locked --extra dbt
uv run driftproof demo
```

Both fixtures must build green. DriftProof must approve the contract-preserving fixture and reject the green-but-wrong fixture. Open both printed HTML reports.

## 3. Inspect every workflow-agent trace

- Open [`AGENT_TRAJECTORIES.json`](AGENT_TRAJECTORIES.json).
- Confirm coverage for `baseline_reviewer` and `contract_clarifier`.
- Confirm instructions, response, retry/usage evidence, deterministic verifier feedback, and human checkpoints are present.
- Use [`TRACE_INDEX.json`](TRACE_INDEX.json) to verify the canonical source hashes.

## 4. Map evidence to the scoring rubric

- Open [`RUBRIC_MAP.json`](RUBRIC_MAP.json).
- Confirm its six criteria total 100 points.
- Follow the argument vectors; do not paste untrusted fields into a shell.

## 5. Run qualification when deeper verification is needed

```bash
make check
bash scripts/reproduce.sh
```

`make check` covers formatting, linting, strict typing, schema drift, submission drift, protocol smoke tests, the complete test suite, frozen replay, and package construction. The full reproduction additionally regenerates and externally validates the benchmark.

## 6. Preserve the authority boundary

- `human_approval_required` must remain `true`.
- `consequential_action_taken` must remain `false`.
- An approval is decision support, never automatic authorization to merge or deploy.

## Scope limitations

- The benchmark is balanced, synthetic, and project-authored.
- The two-case demo is not the 24-case benchmark.
- Replay does not establish unseen-input generalization.
- Safe execution currently requires Linux and rootless bubblewrap.
