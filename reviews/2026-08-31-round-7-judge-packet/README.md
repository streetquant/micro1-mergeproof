# Adversarial round 7 — hostile judge navigation and evidence binding

This round evaluated the exact private-main checkpoint as a time-constrained judge rather than as a repository author. The implementation had substantial evidence, but the required trajectories, claims, rubric mapping, and release entry points were scattered across source and result directories. A judge could not determine which agents were actually used, whether every headline claim was supported, or whether a downloaded archive contained the same evidence advertised by the submission.

The repair generates one deterministic judge packet from committed evidence. It enumerates every observed workflow agent, includes representative instructions/responses/verifier feedback/retry evidence/human checkpoints, indexes all 24 canonical baseline traces and their provider fixtures, binds eight load-bearing claims to exact SHA-256 evidence, maps all six rubric criteria to 100 points, and provides a concise human checklist. Submission drift now includes metrics, traces, claims, and rubric data.

Release packaging publishes every judge-packet entry at the delivery root and in all archives. Download verification cross-checks the trajectory packet, trace index, claim ledger, rubric map, submission manifest, and fixed safety boundary. A negative control modified and rehashed the trajectory packet to bypass the outer checksum; verification still rejected the substitution because the inner trace and manifest bindings no longer matched.

`findings.json` records the confirmed defects. `qualification.json` binds the final source, generated packet, focused and complete tests, replay, package construction, documentation validation, and Git integrity checks.
