# Adversarial round 3 — release security and submission delivery

This round treated the exact private-GitHub checkpoint as a hostile submission package rather than trusting source-test success. The audit inspected what a judge, a human reviewer, and an autonomous agent would actually receive in the evidence archive and release directory.

Confirmed defects included materially overstated README metrics, an evidence selector that omitted every adversarial-review file, a committed private host path that prevented packaging, a stale impossible required-evidence path, metadata-only ZIP verification, extraction of only one of three archives, and no self-verifying human/browser/machine entry point at the release root.

The repairs bind judge-facing claims to `results/driftproof-comparison/comparison.json`, generate `submission/START_HERE.md`, `submission/START_HERE.html`, and `submission/manifest.json`, include all review evidence in the evidence archive, fully read and CRC-verify every archive, safely extract all three, refuse mixed output directories, and independently verify downloaded release sets through `scripts/verify_release.py`.

`findings.json` is the issue ledger. `qualification.json` is written only after the complete source suite, hostile archive tests, deterministic double packaging, exact-remote packaging, and downloaded-directory verification pass.
