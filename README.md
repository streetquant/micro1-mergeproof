# MergeProof

MergeProof is an evidence-grounded release gate for agent-authored code changes. It collects repository evidence, reruns bounded verification, challenges unsupported agent claims, and produces an auditable report for a qualified human merge decision.

The implementation is being developed for the micro1 Frontier Engineering Challenge 2026. The frozen problem and evaluation contract are in [`oracle/problem-brief.md`](oracle/problem-brief.md). Complete setup, benchmark evidence, trajectories, changelog, and video materials will be added before the final submission.

MergeProof is read-only with respect to reviewed repositories and never merges or deploys code automatically.
