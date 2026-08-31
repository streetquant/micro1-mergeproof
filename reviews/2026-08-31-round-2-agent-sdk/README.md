# Adversarial round 2 — AI-agent identity, SDK and concurrency

This round treated autonomous integration as a protocol and concurrency problem rather than a documentation problem. It attacked pre-execution retry identity, malformed machine output, process/response disagreement, relative paths, source-only imports, and independent concurrent callers.

The repair adds a non-executing content fingerprint and a typed Python SDK. Default SDK callers receive disjoint control run IDs only when they have not selected output or run ID themselves. Two concurrent real reviews produced separate, independently verified bundles, and each navigation response preserved the exact pre-execution configuration hash despite its unique control run ID.

`agent-probe.json` records the actual concurrent and invalid-response probe. `installed-wheel-sdk.json` records the fresh-wheel boundary. `findings.json` and `qualification.json` account for the fixes and limits.
