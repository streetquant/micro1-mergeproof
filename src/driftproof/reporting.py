from __future__ import annotations

import hashlib
import html
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mergeproof.utils import atomic_write_text, pretty_json

from .certificate import verify_certificate, write_bundle
from .models import ApprovalCertificate, GateReport, Verdict

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_FILES = (
    "gate-report.json",
    "approval-certificate.json",
    "report.md",
    "report.html",
)
BUNDLE_ENTRY_NAMES = {*BUNDLE_FILES, "manifest.json"}


class GateBundleError(ValueError):
    """Raised when a DriftProof bundle cannot be written or verified safely."""


def verdict_exit_code(verdict: Verdict) -> int:
    return {
        Verdict.APPROVE: 0,
        Verdict.REJECT: 10,
        Verdict.HUMAN_REVIEW: 20,
    }[verdict]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def prepare_gate_output(output_dir: Path, *, replace: bool = False) -> None:
    """Reserve a dedicated bundle path without reusing stale evidence implicitly."""

    if output_dir.is_symlink():
        raise GateBundleError(f"bundle directory may not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        raise GateBundleError(f"bundle output must be a directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if not output_dir.exists():
        return

    entries = {item.name for item in output_dir.iterdir()}
    unexpected = sorted(entries - BUNDLE_ENTRY_NAMES)
    if unexpected:
        raise GateBundleError(
            f"bundle directory contains unrelated entries: {unexpected}; choose a dedicated output path"
        )
    if not entries:
        output_dir.rmdir()
        return
    if not replace:
        raise GateBundleError(
            f"bundle output already exists: {output_dir}; choose a new path or pass --replace-output"
        )
    shutil.rmtree(output_dir)


def _markdown(report: GateReport, certificate: ApprovalCertificate) -> str:
    lines = [
        f"# DriftProof review: {report.candidate_id}",
        "",
        f"- **Verdict:** `{report.verdict.value}`",
        f"- **Build isolation:** `{report.build.isolation}`",
        f"- **Build return code:** `{report.build.returncode}`",
        f"- **Certificate:** `{certificate.self_sha256}`",
        "",
        "> **Human approval boundary:** DriftProof did not merge, deploy, push, publish, or otherwise execute a consequential action. A qualified human remains responsible for the final decision.",
        "",
        "## Summary",
        "",
        report.summary,
        "",
        "## Verification checks",
        "",
        "| Status | Check | Detail | Evidence |",
        "|---|---|---|---|",
    ]
    for check in report.checks:
        evidence = "<br>".join(f"`{item}`" for item in check.evidence) or "—"
        lines.append(
            f"| `{check.status.value}` | **{check.title}** (`{check.id}`) | "
            f"{check.detail} | {evidence} |"
        )
    lines.extend(["", "## Visible contract", ""])
    if report.contract.rules:
        lines.extend(
            [
                "| Kind | Rule ID | Source | Output / fields |",
                "|---|---|---|---|",
            ]
        )
        for rule in report.contract.rules:
            outputs = ", ".join([value for value in [rule.output, *rule.fields] if value]) or "—"
            lines.append(
                f"| `{rule.kind.value}` | `{rule.id}` | {rule.source_text} | `{outputs}` |"
            )
    else:
        lines.append("No machine-verifiable rule was admitted from the supplied context.")
    lines.extend(["", "### Unresolved context", ""])
    if report.contract.unknown_sentences:
        lines.extend(f"- {value}" for value in report.contract.unknown_sentences)
    else:
        lines.append("- None")

    lines.extend(["", "## Bounded agent trace", ""])
    if report.agent_trace is None:
        lines.append("No model agent was invoked.")
    else:
        lines.extend(
            [
                f"- Provider: `{report.agent_trace.provider}`",
                f"- Model: `{report.agent_trace.model}`",
                f"- Request hash: `{report.agent_trace.request_hash}`",
                f"- Accepted rules: `{len(report.agent_trace.accepted_rule_ids)}`",
                f"- Rejected proposals: `{len(report.agent_trace.rejected_proposals)}`",
                f"- Remaining unresolved sentences: `{len(report.agent_trace.unresolved_sentences)}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Integrity and execution boundary",
            "",
            f"- Candidate tree SHA-256: `{report.project_sha256}`",
            f"- Business context SHA-256: `{report.context_sha256}`",
            f"- Disposable worktree SHA-256: `{report.build.worktree_sha256}`",
            f"- Certificate SHA-256: `{certificate.self_sha256}`",
            f"- Human approval required: **{str(report.human_approval_required).lower()}**",
            f"- Consequential action taken: **{str(report.consequential_action_taken).lower()}**",
            "",
            "The machine-readable authority is `gate-report.json`. `approval-certificate.json` binds the report, candidate, context, build worktree, verdict, and check indexes. Verify the complete bundle with `driftproof verify-bundle <directory>`.",
            "",
        ]
    )
    return "\n".join(lines)


def _html(report: GateReport, certificate: ApprovalCertificate) -> str:
    verdict_class = report.verdict.value.replace("_", "-")
    check_rows = "".join(
        "<tr>"
        f'<td><span class="status {html.escape(check.status.value)}">{html.escape(check.status.value)}</span></td>'
        f"<td><strong>{html.escape(check.title)}</strong><br><code>{html.escape(check.id)}</code></td>"
        f"<td>{html.escape(check.detail)}</td>"
        f"<td>{''.join(f'<code>{html.escape(item)}</code> ' for item in check.evidence) or '—'}</td>"
        "</tr>"
        for check in report.checks
    )
    rules = (
        "".join(
            '<article class="rule">'
            f"<div><code>{html.escape(rule.kind.value)}</code> <code>{html.escape(rule.id)}</code></div>"
            f"<h3>{html.escape(rule.source_text)}</h3>"
            f"<p>Output: <code>{html.escape(rule.output or '—')}</code>; fields: "
            f"<code>{html.escape(', '.join(rule.fields) or '—')}</code></p>"
            "</article>"
            for rule in report.contract.rules
        )
        or '<p class="muted">No machine-verifiable rule was admitted.</p>'
    )
    unresolved = (
        "".join(f"<li>{html.escape(value)}</li>" for value in report.contract.unknown_sentences)
        or "<li>None</li>"
    )
    agent = '<p class="muted">No model agent was invoked.</p>'
    if report.agent_trace is not None:
        agent = (
            "<dl>"
            f"<dt>Provider / model</dt><dd><code>{html.escape(report.agent_trace.provider)}</code> / <code>{html.escape(report.agent_trace.model)}</code></dd>"
            f"<dt>Request hash</dt><dd><code>{html.escape(report.agent_trace.request_hash)}</code></dd>"
            f"<dt>Accepted rules</dt><dd>{len(report.agent_trace.accepted_rule_ids)}</dd>"
            f"<dt>Rejected proposals</dt><dd>{len(report.agent_trace.rejected_proposals)}</dd>"
            f"<dt>Unresolved sentences</dt><dd>{len(report.agent_trace.unresolved_sentences)}</dd>"
            "</dl>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DriftProof review — {html.escape(report.candidate_id)}</title>
<style>
:root {{ color-scheme:light dark; --bg:#08111d; --panel:#111f31; --text:#edf5ff; --muted:#a9bbd1; --border:#334b64; --pass:#2ec27e; --fail:#ff6b6b; --review:#f6c85f; }}
* {{ box-sizing:border-box; }} body {{ margin:0; font-family:ui-sans-serif,system-ui,sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }}
main {{ width:min(1180px,94vw); margin:2rem auto 5rem; }} header,section {{ background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:1.25rem 1.5rem; margin:1rem 0; }}
h1,h2,h3 {{ line-height:1.2; }} code {{ overflow-wrap:anywhere; background:#050a12; padding:.1rem .3rem; border-radius:4px; }}
.verdict {{ display:inline-block; font-weight:800; padding:.45rem .8rem; border-radius:999px; }} .verdict.approve {{ background:var(--pass); color:#06170e; }} .verdict.reject {{ background:var(--fail); color:#220606; }} .verdict.human-review {{ background:var(--review); color:#221904; }}
.boundary {{ border-left:5px solid var(--review); padding-left:1rem; color:var(--muted); }} .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:.75rem; }} .metric,.rule {{ border:1px solid var(--border); border-radius:10px; padding:.85rem; }} .metric strong {{ display:block; font-size:1.35rem; }}
table {{ width:100%; border-collapse:collapse; }} th,td {{ text-align:left; vertical-align:top; padding:.6rem; border-bottom:1px solid var(--border); }} .status {{ text-transform:uppercase; font-size:.75rem; font-weight:800; }} .status.pass {{ color:var(--pass); }} .status.fail {{ color:var(--fail); }} .status.inconclusive {{ color:var(--review); }} .muted {{ color:var(--muted); }} dt {{ color:var(--muted); margin-top:.5rem; }} dd {{ margin-left:0; }}
</style>
</head>
<body><main>
<header>
<p class="muted">Independent adversarial release gate for agent-authored dbt repairs</p>
<h1>{html.escape(report.candidate_id)}</h1>
<p><span class="verdict {verdict_class}">{html.escape(report.verdict.value)}</span></p>
<p>{html.escape(report.summary)}</p>
<p class="boundary"><strong>Human approval required.</strong> DriftProof performed no merge, deployment, push, publication, or other consequential action.</p>
<div class="grid">
<div class="metric"><strong>{len(report.checks)}</strong>Checks</div>
<div class="metric"><strong>{len(report.failed_check_ids)}</strong>Failed</div>
<div class="metric"><strong>{len(report.inconclusive_check_ids)}</strong>Inconclusive</div>
<div class="metric"><strong>{html.escape(report.build.isolation)}</strong>Isolation</div>
</div>
</header>
<section><h2>Verification checks</h2><table><thead><tr><th>Status</th><th>Check</th><th>Detail</th><th>Evidence</th></tr></thead><tbody>{check_rows}</tbody></table></section>
<section><h2>Visible contract</h2>{rules}<h3>Unresolved context</h3><ul>{unresolved}</ul></section>
<section><h2>Bounded agent trace</h2>{agent}</section>
<section><h2>Integrity</h2><ul><li>Candidate: <code>{html.escape(report.project_sha256)}</code></li><li>Context: <code>{html.escape(report.context_sha256)}</code></li><li>Worktree: <code>{html.escape(report.build.worktree_sha256)}</code></li><li>Certificate: <code>{html.escape(certificate.self_sha256)}</code></li></ul><p class="muted">Verify with <code>driftproof verify-bundle &lt;directory&gt;</code>.</p></section>
</main></body></html>\n"""


def _write_gate_bundle_files(
    output_dir: Path,
    report: GateReport,
    certificate: ApprovalCertificate,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    write_bundle(output_dir, report, certificate)
    _atomic_text(output_dir / "report.md", _markdown(report, certificate))
    _atomic_text(output_dir / "report.html", _html(report, certificate))
    files = {
        name: {
            "bytes": (output_dir / name).stat().st_size,
            "sha256": _sha256_file(output_dir / name),
        }
        for name in BUNDLE_FILES
    }
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "tool": "driftproof",
        "candidate_id": report.candidate_id,
        "verdict": report.verdict.value,
        "exit_code": verdict_exit_code(report.verdict),
        "certificate_sha256": certificate.self_sha256,
        "human_approval_required": True,
        "consequential_action_taken": False,
        "files": files,
    }
    _atomic_text(output_dir / "manifest.json", pretty_json(manifest) + "\n")
    return manifest


def write_gate_bundle(
    output_dir: Path,
    report: GateReport,
    certificate: ApprovalCertificate,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Build, verify, and publish a complete DriftProof bundle atomically."""

    prepare_gate_output(output_dir, replace=replace)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name or 'driftproof-bundle'}.",
            dir=output_dir.parent,
        )
    )
    temporary.rmdir()
    try:
        manifest = _write_gate_bundle_files(temporary, report, certificate)
        verification = verify_gate_bundle(temporary)
        if verification["verdict"] != report.verdict.value:
            raise GateBundleError("verified bundle verdict drifted from the report")
        if output_dir.exists():
            raise GateBundleError(f"bundle output appeared during publication: {output_dir}")
        temporary.replace(output_dir)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify_gate_bundle(output_dir: Path) -> dict[str, Any]:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise GateBundleError(f"bundle must be a regular directory: {output_dir}")
    entries = {item.name for item in output_dir.iterdir()}
    if entries != BUNDLE_ENTRY_NAMES:
        raise GateBundleError(
            f"bundle entry set mismatch; missing={sorted(BUNDLE_ENTRY_NAMES - entries)}, "
            f"unexpected={sorted(entries - BUNDLE_ENTRY_NAMES)}"
        )
    manifest_path = output_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateBundleError(f"invalid manifest JSON: {exc}") from exc
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise GateBundleError("unsupported bundle schema version")
    if manifest.get("tool") != "driftproof":
        raise GateBundleError("unexpected bundle tool identity")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(BUNDLE_FILES):
        raise GateBundleError("manifest file set is incomplete or unexpected")
    for name, metadata in files.items():
        path = output_dir / name
        if not path.is_file() or path.is_symlink() or not isinstance(metadata, dict):
            raise GateBundleError(f"missing or unsafe bundle file: {name}")
        if path.stat().st_size != metadata.get("bytes"):
            raise GateBundleError(f"byte length mismatch for {name}")
        if _sha256_file(path) != metadata.get("sha256"):
            raise GateBundleError(f"SHA-256 mismatch for {name}")

    report = GateReport.model_validate_json(
        (output_dir / "gate-report.json").read_text(encoding="utf-8")
    )
    certificate = ApprovalCertificate.model_validate_json(
        (output_dir / "approval-certificate.json").read_text(encoding="utf-8")
    )
    errors = verify_certificate(report, certificate)
    if errors:
        raise GateBundleError(f"certificate verification failed: {errors}")
    if manifest.get("candidate_id") != report.candidate_id:
        raise GateBundleError("candidate identity mismatch")
    if manifest.get("verdict") != report.verdict.value:
        raise GateBundleError("verdict mismatch")
    if manifest.get("exit_code") != verdict_exit_code(report.verdict):
        raise GateBundleError("exit-code mapping mismatch")
    if manifest.get("certificate_sha256") != certificate.self_sha256:
        raise GateBundleError("certificate identity mismatch")
    if manifest.get("human_approval_required") is not True:
        raise GateBundleError("manifest weakened the human approval boundary")
    if manifest.get("consequential_action_taken") is not False:
        raise GateBundleError("manifest claims a consequential action was taken")
    return {
        "verified": True,
        "candidate_id": report.candidate_id,
        "verdict": report.verdict.value,
        "exit_code": verdict_exit_code(report.verdict),
        "certificate_sha256": certificate.self_sha256,
        "bundle_manifest_sha256": _sha256_file(manifest_path),
        "files": files,
    }
