from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from mergeproof.utils import canonical_json, sha256_text

from .agent import ContractClarifier
from .certificate import build_certificate, verify_certificate
from .checks import verify_contract
from .contracts import compile_contract
from .models import (
    ApprovalCertificate,
    CheckResult,
    CheckStatus,
    GateReport,
    Verdict,
)
from .project import ProjectValidationError, snapshot_project
from .reporting import write_gate_bundle
from .runner import BuildExecutionError, run_dbt_build


class GateExecutionError(RuntimeError):
    pass


def _candidate_id(project: Path, project_sha256: str) -> str:
    marker = project / ".driftproof-candidate"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8", errors="replace").strip()
        if value:
            return value[:128]
    return f"DP-{project_sha256[:12].upper()}"


def _check(
    title: str,
    status: CheckStatus,
    detail: str,
    *,
    evidence: list[str] | None = None,
) -> CheckResult:
    identity = canonical_json({"title": title, "detail": detail})
    return CheckResult(
        id=f"C-{sha256_text(identity)[:12].upper()}",
        status=status,
        title=title,
        detail=detail,
        evidence=evidence or [],
    )


def _trajectory_hash(project: Path) -> str | None:
    path = project / "agent-trajectory.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        value = path.read_text(encoding="utf-8", errors="replace")
    return sha256_text(canonical_json(value) if not isinstance(value, str) else value)


def _decide(build_passed: bool, checks: list[CheckResult]) -> Verdict:
    if not build_passed or any(check.status == CheckStatus.FAIL for check in checks):
        return Verdict.REJECT
    if any(check.status == CheckStatus.INCONCLUSIVE for check in checks):
        return Verdict.HUMAN_REVIEW
    return Verdict.APPROVE


def _summary(verdict: Verdict, failed: list[str], inconclusive: list[str]) -> str:
    if verdict == Verdict.APPROVE:
        return (
            "Candidate built successfully and every compiled visible contract check passed. "
            "A qualified human must still authorize any merge or deployment."
        )
    if verdict == Verdict.REJECT:
        return f"Candidate is not approval-ready; failed checks: {', '.join(failed)}."
    return (
        "Candidate requires qualified human review because the visible contract could not be "
        f"verified conclusively: {', '.join(inconclusive)}."
    )


def review_project(
    project: Path,
    *,
    context_path: Path | None = None,
    work_root: Path,
    output_dir: Path | None = None,
    timeout_seconds: int = 120,
    isolation: Literal["auto", "disposable_copy", "bubblewrap"] = "auto",
    allow_unconfined: bool = False,
    clarifier: ContractClarifier | None = None,
    replace_output: bool = False,
) -> tuple[GateReport, ApprovalCertificate]:
    project = project.resolve()
    context_path = (context_path or project / "BUSINESS_CONTEXT.md").resolve()
    if not context_path.is_file():
        raise GateExecutionError(f"business context file does not exist: {context_path}")
    context = context_path.read_text(encoding="utf-8", errors="replace")

    try:
        before = snapshot_project(project)
        build = run_dbt_build(
            project,
            work_root=work_root,
            candidate_id=_candidate_id(project, before.tree_sha256),
            timeout_seconds=timeout_seconds,
            isolation=isolation,
            allow_unconfined=allow_unconfined,
        )
        after = snapshot_project(project)
    except (ProjectValidationError, BuildExecutionError) as exc:
        raise GateExecutionError(str(exc)) from exc

    candidate_id = _candidate_id(project, before.tree_sha256)
    contract = compile_contract(context)
    agent_trace = None
    if not contract.rules and clarifier is not None:
        contract, agent_trace = clarifier.clarify(contract, before)
    checks = [
        _check(
            "Candidate builds from the disposable review worktree",
            CheckStatus.PASS if build.passed else CheckStatus.FAIL,
            f"dbt returned {build.returncode} under {build.isolation} isolation.",
            evidence=["dbt build", build.worktree_sha256],
        ),
        _check(
            "Original candidate project remained unchanged",
            CheckStatus.PASS if before.tree_sha256 == after.tree_sha256 else CheckStatus.FAIL,
            "The source tree hash is unchanged."
            if before.tree_sha256 == after.tree_sha256
            else "The source tree changed during review.",
            evidence=[before.tree_sha256, after.tree_sha256],
        ),
    ]
    if agent_trace is not None:
        clarifier_complete = (
            bool(agent_trace.accepted_rule_ids) and not agent_trace.unresolved_sentences
        )
        checks.append(
            _check(
                "Bounded Contract Clarifier produced only admitted typed rules",
                CheckStatus.PASS if clarifier_complete else CheckStatus.INCONCLUSIVE,
                f"Accepted {len(agent_trace.accepted_rule_ids)} typed rules; "
                f"rejected {len(agent_trace.rejected_proposals)} proposals; "
                f"left {len(agent_trace.unresolved_sentences)} sentences unresolved.",
                evidence=[agent_trace.request_hash, *agent_trace.accepted_rule_ids],
            )
        )
    if not contract.rules:
        checks.append(
            _check(
                "At least one visible business contract was compiled",
                CheckStatus.INCONCLUSIVE,
                "No supported machine-verifiable rule could be derived from the supplied context.",
                evidence=[context_path.name],
            )
        )
    else:
        checks.append(
            _check(
                "Visible business context compiled into executable checks",
                CheckStatus.PASS,
                f"Compiled {len(contract.rules)} rules without using an external benchmark oracle.",
                evidence=[context_path.name, contract.context_sha256],
            )
        )
        checks.extend(verify_contract(before, contract))
    if contract.unknown_sentences:
        checks.append(
            _check(
                "Every visible business statement was resolved or verified",
                CheckStatus.INCONCLUSIVE,
                f"{len(contract.unknown_sentences)} visible business statements remain unresolved; approval is not permitted.",
                evidence=[context_path.name, contract.context_sha256],
            )
        )

    failed = [check.id for check in checks if check.status == CheckStatus.FAIL]
    inconclusive = [check.id for check in checks if check.status == CheckStatus.INCONCLUSIVE]
    verdict = _decide(build.passed, checks)
    report = GateReport(
        candidate_id=candidate_id,
        verdict=verdict,
        summary=_summary(verdict, failed, inconclusive),
        project_sha256=before.tree_sha256,
        context_sha256=contract.context_sha256,
        trajectory_sha256=_trajectory_hash(project),
        build=build,
        contract=contract,
        agent_trace=agent_trace,
        checks=checks,
        failed_check_ids=failed,
        inconclusive_check_ids=inconclusive,
    )
    certificate = build_certificate(report)
    report = report.model_copy(update={"certificate_sha256": certificate.self_sha256})
    certificate_errors = verify_certificate(report, certificate)
    if certificate_errors:
        raise GateExecutionError(f"certificate verification failed: {certificate_errors}")
    if output_dir is not None:
        write_gate_bundle(output_dir, report, certificate, replace=replace_output)
    return report, certificate


def baseline_green_gate(
    project: Path,
    *,
    work_root: Path,
    timeout_seconds: int = 120,
    isolation: Literal["auto", "disposable_copy", "bubblewrap"] = "auto",
    allow_unconfined: bool = False,
) -> Verdict:
    project = project.resolve()
    try:
        snapshot = snapshot_project(project)
        build = run_dbt_build(
            project,
            work_root=work_root,
            candidate_id=f"baseline-{snapshot.tree_sha256[:12]}",
            timeout_seconds=timeout_seconds,
            isolation=isolation,
            allow_unconfined=allow_unconfined,
        )
    except (ProjectValidationError, BuildExecutionError):
        return Verdict.HUMAN_REVIEW
    return Verdict.APPROVE if build.passed else Verdict.REJECT
