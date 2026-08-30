from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

from .models import AuditResult, CaseInput, Decision, EvidenceRecord, FindingStatus
from .utils import canonical_json, pretty_json, sha256_text, stable_evidence_id, write_json

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_FILES = (
    "request.json",
    "result.json",
    "evidence.jsonl",
    "agent-traces.json",
    "report.md",
    "report.html",
)
BUNDLE_ENTRY_NAMES = {*BUNDLE_FILES, "manifest.json"}


class BundleVerificationError(ValueError):
    """Raised when a review bundle fails integrity or schema verification."""


def decision_exit_code(decision: Decision) -> int:
    return {
        Decision.APPROVE: 0,
        Decision.REJECT: 10,
        Decision.HUMAN_REVIEW: 20,
    }[decision]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _evidence_jsonl(evidence: list[EvidenceRecord]) -> str:
    return "".join(canonical_json(item.model_dump(mode="json")) + "\n" for item in evidence)


def _markdown_report(case: CaseInput, result: AuditResult) -> str:
    verified = [item for item in result.findings if item.status == FindingStatus.VERIFIED]
    hypotheses = [item for item in result.findings if item.status == FindingStatus.HYPOTHESIS]
    lines = [
        f"# MergeProof review: {case.title}",
        "",
        f"**Decision:** `{result.decision.value}`  ",
        f"**Confidence:** `{result.confidence:.2f}`  ",
        f"**Mode:** `{result.mode}`  ",
        f"**Case:** `{result.case_id}`",
        "",
        "> **Human approval boundary:** MergeProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.",
        "",
        "## Summary",
        "",
        result.summary,
        "",
        "## Task",
        "",
        case.task,
        "",
    ]
    if result.contract is not None:
        lines.extend(["## Agent-compiled contract", ""])
        for heading, values in (
            ("Requirements", result.contract.requirements),
            ("Invariants", result.contract.invariants),
            ("Ambiguities", result.contract.ambiguities),
            ("Acceptance checks", result.contract.acceptance_checks),
        ):
            lines.append(f"### {heading}")
            lines.extend(f"- {value}" for value in values) if values else lines.append(
                "- None recorded"
            )
            lines.append("")

    lines.extend(["## Verified findings", ""])
    if verified:
        lines.append("| Severity | Category | Finding | Evidence |")
        lines.append("|---|---|---|---|")
        for finding in verified:
            evidence = "<br>".join(f"`{item}`" for item in finding.evidence_ids)
            lines.append(
                f"| {finding.severity.value} | `{finding.category.value}` | "
                f"**{finding.title}** — {finding.explanation} | {evidence} |"
            )
    else:
        lines.append("No verified blocker was found by the configured checks.")
    lines.append("")

    lines.extend(["## Evidence-bound hypotheses", ""])
    if hypotheses:
        for finding in hypotheses:
            refs = ", ".join(f"`{item}`" for item in finding.evidence_ids) or "none"
            lines.extend(
                [
                    f"### {finding.title}",
                    "",
                    f"- Severity: `{finding.severity.value}`",
                    f"- Category: `{finding.category.value}`",
                    f"- Evidence: {refs}",
                    f"- Rationale: {finding.explanation}",
                    "",
                ]
            )
    else:
        lines.extend(["No unresolved agent hypothesis was admitted.", ""])

    lines.extend(
        [
            "## Agent trajectories",
            "",
            "| Agent | Provider | Model | Request hash | Output hash | Gate violations |",
            "|---|---|---|---|---|---:|",
        ]
    )
    if result.agent_traces:
        for trace in result.agent_traces:
            lines.append(
                f"| `{trace.agent}` | `{trace.provider}` | `{trace.model}` | "
                f"`{trace.request_hash}` | `{trace.output_sha256}` | {len(trace.gate_violations)} |"
            )
    else:
        lines.append("| _No model agent invoked_ | — | — | — | — | 0 |")
    lines.append("")

    lines.extend(
        [
            "## Verification and provenance",
            "",
            f"- Evidence records: **{len(result.evidence)}**",
            f"- Evidence-reference validity: **{result.valid_evidence_rate:.3f}**",
            f"- Gate violations: **{len(result.gate_violations)}**",
            f"- Consequential action taken: **{str(result.consequential_action_taken).lower()}**",
            f"- Human approval required: **{str(result.human_approval_required).lower()}**",
            "",
            "The machine-readable source of truth is `result.json`; `evidence.jsonl` contains the complete evidence ledger and `manifest.json` binds every bundle file by SHA-256.",
            "",
        ]
    )
    return "\n".join(lines)


def _html_report(case: CaseInput, result: AuditResult) -> str:
    verified = [item for item in result.findings if item.status == FindingStatus.VERIFIED]
    hypotheses = [item for item in result.findings if item.status == FindingStatus.HYPOTHESIS]
    decision_class = result.decision.value.replace("_", "-")

    def finding_cards(items: list[Any]) -> str:
        if not items:
            return '<p class="empty">None.</p>'
        rendered: list[str] = []
        for finding in items:
            refs = "".join(f"<code>{html.escape(ref)}</code> " for ref in finding.evidence_ids)
            rendered.append(
                '<article class="finding">'
                f'<div class="finding-meta"><span>{html.escape(finding.severity.value)}</span>'
                f"<span>{html.escape(finding.category.value)}</span></div>"
                f"<h3>{html.escape(finding.title)}</h3>"
                f"<p>{html.escape(finding.explanation)}</p>"
                f'<div class="evidence">{refs or "No admitted evidence reference"}</div>'
                "</article>"
            )
        return "".join(rendered)

    trace_rows = (
        "".join(
            "<tr>"
            f"<td>{html.escape(trace.agent)}</td>"
            f"<td>{html.escape(trace.provider)}</td>"
            f"<td>{html.escape(trace.model)}</td>"
            f"<td><code>{html.escape(trace.request_hash)}</code></td>"
            f"<td>{len(trace.gate_violations)}</td>"
            "</tr>"
            for trace in result.agent_traces
        )
        or '<tr><td colspan="5">No model agent invoked.</td></tr>'
    )

    contract_html = ""
    if result.contract is not None:
        sections: list[str] = []
        for heading, values in (
            ("Requirements", result.contract.requirements),
            ("Invariants", result.contract.invariants),
            ("Ambiguities", result.contract.ambiguities),
            ("Acceptance checks", result.contract.acceptance_checks),
        ):
            items = (
                "".join(f"<li>{html.escape(value)}</li>" for value in values)
                or "<li>None recorded</li>"
            )
            sections.append(f"<h3>{heading}</h3><ul>{items}</ul>")
        contract_html = (
            "<section><h2>Agent-compiled contract</h2>" + "".join(sections) + "</section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MergeProof review — {html.escape(case.title)}</title>
<style>
:root {{ color-scheme: light dark; --bg:#0b1020; --panel:#151c31; --text:#eaf0ff; --muted:#aebbd8; --border:#34415f; --approve:#2ec27e; --reject:#ff6b6b; --review:#f6c85f; }}
* {{ box-sizing:border-box; }} body {{ margin:0; font-family:ui-sans-serif,system-ui,sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }}
main {{ width:min(1100px,94vw); margin:2rem auto 5rem; }} section, header {{ background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:1.25rem 1.5rem; margin:1rem 0; }}
h1,h2,h3 {{ line-height:1.2; }} .decision {{ display:inline-block; font-weight:800; letter-spacing:.04em; padding:.45rem .75rem; border-radius:999px; }}
.decision.approve {{ background:var(--approve); color:#07170f; }} .decision.reject {{ background:var(--reject); color:#220707; }} .decision.human-review {{ background:var(--review); color:#221904; }}
.boundary {{ border-left:5px solid var(--review); padding-left:1rem; color:var(--muted); }} .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:.75rem; }}
.metric {{ border:1px solid var(--border); border-radius:10px; padding:.8rem; }} .metric strong {{ display:block; font-size:1.35rem; }}
.finding {{ border:1px solid var(--border); border-radius:10px; padding:1rem; margin:.75rem 0; }} .finding-meta {{ display:flex; gap:.5rem; color:var(--muted); text-transform:uppercase; font-size:.75rem; }}
code {{ overflow-wrap:anywhere; background:#080c18; padding:.1rem .3rem; border-radius:4px; }} table {{ width:100%; border-collapse:collapse; }} th,td {{ text-align:left; border-bottom:1px solid var(--border); padding:.55rem; vertical-align:top; }}
.empty,.muted {{ color:var(--muted); }}
</style>
</head>
<body><main>
<header>
<p class="muted">Evidence-grounded review bundle</p>
<h1>{html.escape(case.title)}</h1>
<p><span class="decision {decision_class}">{html.escape(result.decision.value)}</span></p>
<p>{html.escape(result.summary)}</p>
<p class="boundary"><strong>Human approval required.</strong> MergeProof performed no merge, deployment, push, publication, or other consequential action.</p>
<div class="grid">
<div class="metric"><strong>{result.confidence:.2f}</strong>Confidence</div>
<div class="metric"><strong>{len(verified)}</strong>Verified findings</div>
<div class="metric"><strong>{len(hypotheses)}</strong>Hypotheses</div>
<div class="metric"><strong>{result.valid_evidence_rate:.3f}</strong>Evidence validity</div>
</div>
</header>
<section><h2>Task</h2><p>{html.escape(case.task)}</p></section>
{contract_html}
<section><h2>Verified findings</h2>{finding_cards(verified)}</section>
<section><h2>Evidence-bound hypotheses</h2>{finding_cards(hypotheses)}</section>
<section><h2>Agent trajectories</h2><table><thead><tr><th>Agent</th><th>Provider</th><th>Model</th><th>Request hash</th><th>Violations</th></tr></thead><tbody>{trace_rows}</tbody></table></section>
<section><h2>Provenance</h2><ul><li>Case: <code>{html.escape(result.case_id)}</code></li><li>Mode: <code>{html.escape(result.mode)}</code></li><li>Evidence records: {len(result.evidence)}</li><li>Gate violations: {len(result.gate_violations)}</li><li>Consequential action taken: false</li></ul><p class="muted">Verify this bundle with <code>mergeproof verify-bundle &lt;directory&gt;</code>.</p></section>
</main></body></html>\n"""


def _prepare_bundle_directory(output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise BundleVerificationError(f"bundle directory may not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        raise BundleVerificationError(f"bundle output must be a directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(
        item.name for item in output_dir.iterdir() if item.name not in BUNDLE_ENTRY_NAMES
    )
    if unexpected:
        raise BundleVerificationError(
            f"bundle directory contains unexpected entries: {unexpected}; use an empty or prior MergeProof bundle directory"
        )


def write_review_bundle(case: CaseInput, result: AuditResult, output_dir: Path) -> dict[str, Any]:
    _prepare_bundle_directory(output_dir)
    write_json(output_dir / "request.json", case.model_dump(mode="json"))
    write_json(output_dir / "result.json", result.model_dump(mode="json"))
    _atomic_write_text(output_dir / "evidence.jsonl", _evidence_jsonl(result.evidence))
    write_json(
        output_dir / "agent-traces.json",
        [trace.model_dump(mode="json") for trace in result.agent_traces],
    )
    _atomic_write_text(output_dir / "report.md", _markdown_report(case, result))
    _atomic_write_text(output_dir / "report.html", _html_report(case, result))

    files = {
        name: {
            "bytes": (output_dir / name).stat().st_size,
            "sha256": _sha256_file(output_dir / name),
        }
        for name in BUNDLE_FILES
    }
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "tool": "mergeproof",
        "case_id": case.id,
        "mode": result.mode,
        "decision": result.decision.value,
        "exit_code": decision_exit_code(result.decision),
        "human_approval_required": True,
        "consequential_action_taken": False,
        "files": files,
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def verify_review_bundle(output_dir: Path) -> dict[str, Any]:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise BundleVerificationError(f"bundle must be a regular directory: {output_dir}")
    actual_entries = {item.name for item in output_dir.iterdir()}
    if actual_entries != BUNDLE_ENTRY_NAMES:
        missing = sorted(BUNDLE_ENTRY_NAMES - actual_entries)
        unexpected = sorted(actual_entries - BUNDLE_ENTRY_NAMES)
        raise BundleVerificationError(
            f"bundle entry set mismatch; missing={missing}, unexpected={unexpected}"
        )
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise BundleVerificationError(f"missing bundle manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleVerificationError(f"invalid manifest JSON: {exc}") from exc
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleVerificationError("unsupported bundle schema version")
    expected_files = manifest.get("files")
    if not isinstance(expected_files, dict) or set(expected_files) != set(BUNDLE_FILES):
        raise BundleVerificationError("manifest file set is incomplete or unexpected")
    for name, metadata in expected_files.items():
        path = output_dir / name
        if not path.is_file() or path.is_symlink():
            raise BundleVerificationError(f"missing or unsafe bundle file: {name}")
        if not isinstance(metadata, dict):
            raise BundleVerificationError(f"invalid manifest entry for {name}")
        if path.stat().st_size != metadata.get("bytes"):
            raise BundleVerificationError(f"byte length mismatch for {name}")
        if _sha256_file(path) != metadata.get("sha256"):
            raise BundleVerificationError(f"SHA-256 mismatch for {name}")

    case = CaseInput.model_validate_json((output_dir / "request.json").read_text(encoding="utf-8"))
    result = AuditResult.model_validate_json(
        (output_dir / "result.json").read_text(encoding="utf-8")
    )
    if case.id != result.case_id or manifest.get("case_id") != case.id:
        raise BundleVerificationError("case identity mismatch")
    if manifest.get("decision") != result.decision.value:
        raise BundleVerificationError("decision mismatch")
    if manifest.get("mode") != result.mode:
        raise BundleVerificationError("mode mismatch")
    if manifest.get("human_approval_required") is not True:
        raise BundleVerificationError("manifest weakened the human approval boundary")
    if manifest.get("consequential_action_taken") is not False:
        raise BundleVerificationError("manifest claims a consequential action was taken")
    if manifest.get("exit_code") != decision_exit_code(result.decision):
        raise BundleVerificationError("exit-code mapping mismatch")
    if result.consequential_action_taken or not result.human_approval_required:
        raise BundleVerificationError("human approval boundary was weakened")

    evidence_lines = (output_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    evidence = [EvidenceRecord.model_validate_json(line) for line in evidence_lines if line]
    if [item.model_dump(mode="json") for item in evidence] != [
        item.model_dump(mode="json") for item in result.evidence
    ]:
        raise BundleVerificationError("evidence ledger does not match result.json")
    evidence_ids = [item.id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise BundleVerificationError("duplicate evidence ID")
    valid_ids = set(evidence_ids)
    for item in evidence:
        if item.sha256 != sha256_text(item.content):
            raise BundleVerificationError(f"evidence content hash mismatch: {item.id}")
        if item.id != stable_evidence_id(item.kind, item.source, item.content):
            raise BundleVerificationError(f"evidence identity mismatch: {item.id}")
    for finding in result.findings:
        unknown = sorted(set(finding.evidence_ids) - valid_ids)
        if unknown:
            raise BundleVerificationError(f"finding references unknown evidence IDs: {unknown}")
        if finding.status == FindingStatus.VERIFIED and not finding.evidence_ids:
            raise BundleVerificationError("verified finding has no evidence reference")

    traces = json.loads((output_dir / "agent-traces.json").read_text(encoding="utf-8"))
    if traces != [trace.model_dump(mode="json") for trace in result.agent_traces]:
        raise BundleVerificationError("agent trace file does not match result.json")
    for trace in result.agent_traces:
        if trace.output_sha256 != sha256_text(canonical_json(trace.accepted_output)):
            raise BundleVerificationError(f"agent output hash mismatch: {trace.agent}")
        unknown_inputs = sorted(set(trace.input_evidence_ids) - valid_ids)
        if unknown_inputs:
            raise BundleVerificationError(
                f"agent trace references unknown input evidence IDs: {unknown_inputs}"
            )
        if trace.request_hash != trace.usage.request_hash:
            raise BundleVerificationError(f"agent request hash mismatch: {trace.agent}")
        if (trace.provider, trace.model, trace.agent) != (
            trace.usage.provider,
            trace.usage.model,
            trace.usage.agent,
        ):
            raise BundleVerificationError(f"agent usage identity mismatch: {trace.agent}")

    return {
        "verified": True,
        "case_id": case.id,
        "decision": result.decision.value,
        "mode": result.mode,
        "exit_code": decision_exit_code(result.decision),
        "bundle_manifest_sha256": _sha256_file(manifest_path),
        "files": expected_files,
    }


def bundle_summary(output_dir: Path, manifest: dict[str, Any]) -> str:
    return pretty_json(
        {
            "bundle": str(output_dir),
            "case_id": manifest["case_id"],
            "decision": manifest["decision"],
            "exit_code": manifest["exit_code"],
            "manifest": str(output_dir / "manifest.json"),
            "human_report": str(output_dir / "report.html"),
            "machine_result": str(output_dir / "result.json"),
        }
    )
