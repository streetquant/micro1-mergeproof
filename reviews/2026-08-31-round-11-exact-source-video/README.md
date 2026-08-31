# Adversarial review round 11 — exact-source video and downloaded delivery

This round treated the submission as a time-constrained human judge and an unaffiliated machine consumer. The review attacked the documented renderer entry point, exact-commit provenance, dirty-worktree handling, tool portability, Make wrappers, release-root discoverability, archive placement, standalone verification, media decoding, narration audibility, and claim wording.

Eight material findings were repaired. The renderer now has a non-mutating machine readiness contract, no dirty-source bypass, exact-commit Make targets, portable speech/image tool selection, direct-script bootstrap, conservative hash-bound terminology, full media receipts, and release/standalone cross-binding. Generated video is copied to the release root and the full/evidence archives, but intentionally excluded from the source archive.

The real clean-checkpoint render completed from private `main` at `5615dd77badfc4b493b9b6c26d09d0e77d1e5cf9`: nine scenes, 241.154687 seconds, 1920x1080 H.264/yuv420p, AAC 48 kHz, complete decode, mean volume -17.0 dB. Subsequent Make-wrapper-only fixes were reconciled through `70a70e9f6d516e020d61c0ff4f11ef3cbd39d3f9`; the final release must be rendered again from its exact promoted commit.

Every video and release result remains advisory. Human approval is required, and no merge, deployment, publication, or submission action is authorized by the media receipt.
