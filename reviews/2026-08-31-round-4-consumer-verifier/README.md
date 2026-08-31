# Adversarial round 4 — downloaded-release consumer verification

This round used the exact private-GitHub `main` checkpoint as a recipient would: build the release, then invoke the advertised independent verifier rather than calling its internal Python function.

The archives, checksums, manifests, entry points, review qualifications, and embedded Git bundle were internally valid. However, the standalone wrapper failed under `python scripts/verify_release.py` because its import assumed repository-package semantics, and the Make target initially prefixed the JSON receipt with an echoed command. Both defects affected humans and autonomous consumers even though lower-level tests passed.

The repair makes the wrapper executable both as a module and as a script, suppresses Make command echo so `make release-verify` emits exactly one JSON object, and executes the real wrapper against a complete generated release in the packaging regression suite.

`findings.json` records the two confirmed defects. `qualification.json` binds the repaired wrapper, focused consumer test, complete suite, and exact receipt.
