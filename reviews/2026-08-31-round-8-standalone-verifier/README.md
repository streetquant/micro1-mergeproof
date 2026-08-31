# Adversarial round 8 — standalone downloaded-release verification

This round treated the delivery as a judge would receive it: an extracted directory with no source checkout, no DriftProof installation, no virtual-environment activation, and no trusted repository-relative imports.

The prior source verifier was strong but not genuinely portable because it imported repository modules. The release also did not ship a self-contained verifier, and the verifier bytes were not independently bound to the verifier source embedded in the Git-backed source/full archives.

The repair adds a deterministic single-member `verify-release.pyz`. It uses only Python's standard library and Git, runs from an unrelated working directory with `PYTHONPATH` cleared, emits exactly one JSON object, and returns exit `30` on any invalid delivery. It verifies the exact release file set, SHA-256 manifest, archive names, member limits, CRCs, release/attestation identities, all judge-packet cross-bindings, all archive/root delivery copies, every required review qualification, the verifier zipapp structure, the verifier source in both source and full archives, and the embedded Git bundle's `main` commit.

Two hostile controls updated the outer checksum after substitution. A modified trajectory packet was rejected by its inner trace/manifest bindings. A modified verifier zipapp plus matching release-manifest and checksum updates was rejected because the source/full archives still contained the Git-bound verifier source.

`findings.json` records the confirmed defects. `qualification.json` binds the source, tests, complete project qualification, and hostile receipts.
