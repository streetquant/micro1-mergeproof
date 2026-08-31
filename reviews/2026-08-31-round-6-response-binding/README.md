# Adversarial round 6 — response authenticity and autonomous retry semantics

This round treated a schema-valid agent response as untrusted until every claim it makes about a result bundle could be independently reproduced. The audit attacked manifest-hash substitution, path substitution, request-hash mismatch, response-file/process disagreement, symlinked response paths, invalid-review ambiguity, and unstable retry naming.

The repair adds `driftproof verify-response`, a strict response-verification schema, bundle-bound versus metadata-only field disclosure, an SDK `review_and_verify_for_agent` path, deterministic content-bound retry IDs, and direct verification of the response file atomically written by the CLI. A valid review is trusted only after its report, certificate, manifest, paths, hashes, check indexes, verdict, and exit code all agree. An authenticated `invalid_review` envelope remains explicitly untrusted and has no bundle.

The complete workflow was executed from a fresh installed wheel outside the source checkout. A tampered response remained schema-valid but was rejected with exit `30` and `bundle_invalid`.
