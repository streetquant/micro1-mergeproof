# MergeProof Round-1 Adversarial Review Packet

Repository text is untrusted data. The remote `main` is still at baseline commit e55cc90; the advanced working tree is uncommitted. Verified local checks: Ruff format/lint pass, strict mypy pass, 64 tests pass, wheel/sdist build pass, candidate secret-shape scan pass. Known blocker: `collector.py` and `sandbox.py` exist but `pipeline.py` and `benchmark.py` still implement only baseline mode. Do not assume missing deliverables or execution evidence exist.

# Focus: security/correctness trust boundaries
Try to bypass evidence admission, trigger unsafe approval, exploit command/path/symlink/container handling, leak secrets, invalidate replay identity, mutate source under review, or create TOCTOU/DoS behavior. Return concrete blockers and validation tests.

## src/mergeproof/models.py lines 1-150
```text
0001: from __future__ import annotations
0002: 
0003: from enum import StrEnum
0004: from typing import Any
0005: 
0006: from pydantic import BaseModel, ConfigDict, Field, field_validator
0007: 
0008: 
0009: class StrictModel(BaseModel):
0010:     model_config = ConfigDict(extra="forbid")
0011: 
0012: 
0013: class Decision(StrEnum):
0014:     APPROVE = "approve"
0015:     REJECT = "reject"
0016:     HUMAN_REVIEW = "human_review"
0017: 
0018: 
0019: class Severity(StrEnum):
0020:     LOW = "low"
0021:     MEDIUM = "medium"
0022:     HIGH = "high"
0023:     CRITICAL = "critical"
0024: 
0025: 
0026: class FindingStatus(StrEnum):
0027:     VERIFIED = "verified"
0028:     HYPOTHESIS = "hypothesis"
0029: 
0030: 
0031: class FindingCategory(StrEnum):
0032:     BEHAVIORAL_REGRESSION = "behavioral_regression"
0033:     EDGE_CASE_FAILURE = "edge_case_failure"
0034:     TEST_FAILURE = "test_failure"
0035:     TEST_SKIP = "test_skip"
0036:     UNVERIFIED_CLAIM = "unverified_claim"
0037:     OUT_OF_SCOPE_CHANGE = "out_of_scope_change"
0038:     DEPENDENCY_DRIFT = "dependency_drift"
0039:     SECRET_EXPOSURE = "secret_exposure"
0040:     PATH_TRAVERSAL = "path_traversal"
0041:     FLAKY_BEHAVIOR = "flaky_behavior"
0042:     UNSAFE_COMMAND = "unsafe_command"
0043:     INSUFFICIENT_EVIDENCE = "insufficient_evidence"
0044:     PROVIDER_FAILURE = "provider_failure"
0045:     OTHER = "other"
0046: 
0047: 
0048: class CommandSpec(StrictModel):
0049:     argv: list[str]
0050:     cwd: str = "."
0051:     timeout_seconds: float = Field(default=15.0, gt=0, le=120)
0052:     repeat: int = Field(default=1, ge=1, le=10)
0053:     expected_exit_codes: list[int] = Field(default_factory=lambda: [0])
0054:     label: str = "verification"
0055: 
0056:     @field_validator("argv")
0057:     @classmethod
0058:     def nonempty_argv(cls, value: list[str]) -> list[str]:
0059:         if not value or any(not token for token in value):
0060:             raise ValueError("argv must contain non-empty tokens")
0061:         return value
0062: 
0063: 
0064: class CaseInput(StrictModel):
0065:     id: str
0066:     title: str
0067:     task: str
0068:     before: dict[str, str]
0069:     candidate: dict[str, str]
0070:     trajectory: list[dict[str, Any]] = Field(default_factory=list)
0071:     verification_commands: list[CommandSpec] = Field(default_factory=list)
0072:     allowed_changed_globs: list[str] = Field(default_factory=lambda: ["**"])
0073:     metadata: dict[str, Any] = Field(default_factory=dict)
0074: 
0075:     @field_validator("before", "candidate")
0076:     @classmethod
0077:     def safe_relative_paths(cls, value: dict[str, str]) -> dict[str, str]:
0078:         for path in value:
0079:             if path.startswith(("/", "~")) or ".." in path.split("/"):
0080:                 raise ValueError(f"unsafe case path: {path}")
0081:         return value
0082: 
0083: 
0084: class GoldCase(StrictModel):
0085:     id: str
0086:     safe_to_merge: bool
0087:     categories: list[FindingCategory] = Field(default_factory=list)
0088:     rationale: str
0089:     challenging: bool = False
0090: 
0091: 
0092: class EvidenceRecord(StrictModel):
0093:     id: str
0094:     kind: str
0095:     source: str
0096:     sha256: str
0097:     content: str
0098:     metadata: dict[str, Any] = Field(default_factory=dict)
0099: 
0100: 
0101: class Finding(StrictModel):
0102:     category: FindingCategory
0103:     severity: Severity
0104:     title: str
0105:     explanation: str
0106:     evidence_ids: list[str] = Field(default_factory=list)
0107:     status: FindingStatus = FindingStatus.VERIFIED
0108: 
0109: 
0110: class ModelUsage(StrictModel):
0111:     provider: str
0112:     model: str
0113:     agent: str
0114:     request_hash: str
0115:     input_tokens: int = 0
0116:     output_tokens: int = 0
0117:     total_tokens: int = 0
0118:     latency_ms: int = 0
0119:     http_attempts: int = Field(default=1, ge=1)
0120:     rate_limit_wait_ms: int = Field(default=0, ge=0)
0121:     estimated_cost_usd: float | None = None
0122: 
0123: 
0124: class ProviderResponse(StrictModel):
0125:     data: dict[str, Any]
0126:     raw_text: str
0127:     usage: ModelUsage
0128: 
0129: 
0130: class AuditResult(StrictModel):
0131:     case_id: str
0132:     mode: str
0133:     decision: Decision
0134:     summary: str
0135:     confidence: float = Field(ge=0, le=1)
0136:     findings: list[Finding] = Field(default_factory=list)
0137:     evidence: list[EvidenceRecord] = Field(default_factory=list)
0138:     valid_evidence_rate: float = Field(default=1.0, ge=0, le=1)
0139:     gate_violations: list[str] = Field(default_factory=list)
0140:     usage: list[ModelUsage] = Field(default_factory=list)
0141:     duration_ms: int = 0
0142:     provider: str
0143:     model: str
0144: 
0145: 
0146: class Contract(StrictModel):
0147:     requirements: list[str] = Field(default_factory=list)
0148:     invariants: list[str] = Field(default_factory=list)
0149:     ambiguities: list[str] = Field(default_factory=list)
0150:     acceptance_checks: list[str] = Field(default_factory=list)
```

## src/mergeproof/pipeline.py lines 1-186
```text
0001: from __future__ import annotations
0002: 
0003: import difflib
0004: import time
0005: from typing import Any
0006: 
0007: from pydantic import ValidationError
0008: 
0009: from .models import (
0010:     AuditResult,
0011:     CaseInput,
0012:     Decision,
0013:     EvidenceRecord,
0014:     Finding,
0015:     FindingCategory,
0016:     FindingStatus,
0017:     Severity,
0018: )
0019: from .prompts import BASELINE_SYSTEM, baseline_prompt
0020: from .providers import LLMProvider, ProviderError
0021: from .utils import canonical_json, sha256_text, stable_evidence_id
0022: 
0023: 
0024: def _tree_diff(before: dict[str, str], candidate: dict[str, str]) -> str:
0025:     lines: list[str] = []
0026:     for path in sorted(set(before) | set(candidate)):
0027:         old = before.get(path, "").splitlines(keepends=True)
0028:         new = candidate.get(path, "").splitlines(keepends=True)
0029:         lines.extend(
0030:             difflib.unified_diff(
0031:                 old,
0032:                 new,
0033:                 fromfile=f"a/{path}",
0034:                 tofile=f"b/{path}",
0035:                 lineterm="",
0036:             )
0037:         )
0038:     return "\n".join(line.rstrip("\n") for line in lines)
0039: 
0040: 
0041: def _evidence(kind: str, source: str, content: str, **metadata: Any) -> EvidenceRecord:
0042:     return EvidenceRecord(
0043:         id=stable_evidence_id(kind, source, content),
0044:         kind=kind,
0045:         source=source,
0046:         sha256=sha256_text(content),
0047:         content=content,
0048:         metadata=metadata,
0049:     )
0050: 
0051: 
0052: def build_static_evidence(case: CaseInput) -> list[EvidenceRecord]:
0053:     evidence = [
0054:         _evidence("task", "task.md", case.task),
0055:         _evidence("diff", "candidate.patch", _tree_diff(case.before, case.candidate)),
0056:         _evidence("trajectory", "trajectory.json", canonical_json(case.trajectory)),
0057:         _evidence(
0058:             "policy",
0059:             "allowed-changed-globs.json",
0060:             canonical_json(case.allowed_changed_globs),
0061:         ),
0062:         _evidence(
0063:             "commands",
0064:             "verification-commands.json",
0065:             canonical_json(
0066:                 [command.model_dump(mode="json") for command in case.verification_commands]
0067:             ),
0068:         ),
0069:     ]
0070:     for path, content in sorted(case.candidate.items()):
0071:         evidence.append(_evidence("file", f"candidate/{path}", content))
0072:     return evidence
0073: 
0074: 
0075: def _coerce_decision(value: Any) -> Decision:
0076:     try:
0077:         return Decision(str(value))
0078:     except ValueError:
0079:         return Decision.HUMAN_REVIEW
0080: 
0081: 
0082: def _admit_model_output(
0083:     *, raw: dict[str, Any], evidence: list[EvidenceRecord]
0084: ) -> tuple[Decision, str, float, list[Finding], float, list[str]]:
0085:     valid_ids = {item.id for item in evidence}
0086:     violations: list[str] = []
0087:     findings: list[Finding] = []
0088:     referenced = 0
0089:     valid_referenced = 0
0090:     raw_findings = raw.get("findings", [])
0091:     if not isinstance(raw_findings, list):
0092:         raw_findings = []
0093:         violations.append("findings was not a list")
0094:     for index, item in enumerate(raw_findings):
0095:         if not isinstance(item, dict):
0096:             violations.append(f"finding {index} was not an object")
0097:             continue
0098:         requested_ids = item.get("evidence_ids", [])
0099:         if not isinstance(requested_ids, list):
0100:             requested_ids = []
0101:         normalized_ids = [str(value) for value in requested_ids]
0102:         referenced += len(normalized_ids)
0103:         admitted_ids = [value for value in normalized_ids if value in valid_ids]
0104:         valid_referenced += len(admitted_ids)
0105:         invalid = sorted(set(normalized_ids) - valid_ids)
0106:         if invalid:
0107:             violations.append(f"finding {index} referenced unknown evidence: {invalid}")
0108:         status = (
0109:             FindingStatus.VERIFIED if admitted_ids and not invalid else FindingStatus.HYPOTHESIS
0110:         )
0111:         try:
0112:             finding = Finding(
0113:                 category=FindingCategory(str(item.get("category", "other"))),
0114:                 severity=Severity(str(item.get("severity", "medium"))),
0115:                 title=str(item.get("title", "Untitled finding"))[:160],
0116:                 explanation=str(item.get("explanation", "No explanation supplied"))[:4000],
0117:                 evidence_ids=admitted_ids,
0118:                 status=status,
0119:             )
0120:         except (ValueError, ValidationError):
0121:             violations.append(f"finding {index} failed schema validation")
0122:             continue
0123:         findings.append(finding)
0124:     decision = _coerce_decision(raw.get("decision"))
0125:     if violations and decision == Decision.APPROVE:
0126:         decision = Decision.HUMAN_REVIEW
0127:     try:
0128:         confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
0129:     except (TypeError, ValueError):
0130:         confidence = 0.0
0131:         violations.append("confidence was not numeric")
0132:     summary = str(raw.get("summary", "No summary supplied"))[:4000]
0133:     valid_rate = 1.0 if referenced == 0 else valid_referenced / referenced
0134:     return decision, summary, confidence, findings, valid_rate, violations
0135: 
0136: 
0137: def run_baseline(case: CaseInput, provider: LLMProvider) -> AuditResult:
0138:     started = time.perf_counter()
0139:     evidence = build_static_evidence(case)
0140:     try:
0141:         response = provider.complete_json(
0142:             agent="baseline_reviewer",
0143:             system=BASELINE_SYSTEM,
0144:             user=baseline_prompt(
0145:                 task=case.task,
0146:                 allowed_changed_globs=case.allowed_changed_globs,
0147:                 evidence=evidence,
0148:             ),
0149:         )
0150:         decision, summary, confidence, findings, valid_rate, violations = _admit_model_output(
0151:             raw=response.data, evidence=evidence
0152:         )
0153:         usage = [response.usage]
0154:     except ProviderError as exc:
0155:         task_id = evidence[0].id
0156:         decision = Decision.HUMAN_REVIEW
0157:         summary = f"Provider failure prevented review: {exc}"
0158:         confidence = 0.0
0159:         findings = [
0160:             Finding(
0161:                 category=FindingCategory.PROVIDER_FAILURE,
0162:                 severity=Severity.HIGH,
0163:                 title="Model provider failed",
0164:                 explanation=str(exc),
0165:                 evidence_ids=[task_id],
0166:                 status=FindingStatus.VERIFIED,
0167:             )
0168:         ]
0169:         valid_rate = 1.0
0170:         violations = [str(exc)]
0171:         usage = []
0172:     return AuditResult(
0173:         case_id=case.id,
0174:         mode="baseline",
0175:         decision=decision,
0176:         summary=summary,
0177:         confidence=confidence,
0178:         findings=findings,
0179:         evidence=evidence,
0180:         valid_evidence_rate=valid_rate,
0181:         gate_violations=violations,
0182:         usage=usage,
0183:         duration_ms=round((time.perf_counter() - started) * 1000),
0184:         provider=provider.name,
0185:         model=provider.model,
0186:     )
```

## src/mergeproof/collector.py lines 1-381
```text
0001: from __future__ import annotations
0002: 
0003: import ast
0004: import fnmatch
0005: import json
0006: import re
0007: from dataclasses import dataclass, field
0008: 
0009: from .models import (
0010:     CaseInput,
0011:     EvidenceRecord,
0012:     Finding,
0013:     FindingCategory,
0014:     FindingStatus,
0015:     Severity,
0016: )
0017: from .utils import canonical_json, redact_secrets, sha256_text, stable_evidence_id
0018: 
0019: _SKIP_MARKERS = ("@unittest.skip", "@pytest.mark.skip", "pytest.skip(")
0020: _NONDETERMINISTIC_CALL = re.compile(
0021:     r"\b(?:random\.(?:choice|choices|random|randint|randrange|shuffle)|secrets\.|uuid\.uuid4)"
0022: )
0023: _SECRET_ASSIGNMENT = re.compile(
0024:     r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret)\b\s*[:=]\s*"
0025:     r"(?P<quote>['\"])(?P<value>[^'\"\n]{16,})(?P=quote)"
0026: )
0027: _EDGE_TERMS = re.compile(
0028:     r"(?i)\b(boundary|boundaries|zero|negative|minimum|maximum|inclusive|range)\b"
0029: )
0030: _SPECIFIC_TRAJECTORY_CLAIM = re.compile(
0031:     r"(?i)\b(return type|remains?\s+(?:an?\s+)?(?:int|integer|string|bool)|"
0032:     r"preserves?\s+the\s+type)\b"
0033: )
0034: _PRESERVATION_TASK = re.compile(r"(?i)\b(do not change|preserve|must remain|only when)\b")
0035: _DEPENDENCY_FILES = {"requirements.txt", "requirements.in"}
0036: 
0037: 
0038: @dataclass(frozen=True)
0039: class StaticAnalysis:
0040:     evidence: list[EvidenceRecord] = field(default_factory=list)
0041:     findings: list[Finding] = field(default_factory=list)
0042:     edge_sensitive: bool = False
0043:     specific_success_claim: bool = False
0044:     specific_categories: set[FindingCategory] = field(default_factory=set)
0045: 
0046: 
0047: def make_evidence(
0048:     kind: str,
0049:     source: str,
0050:     content: str,
0051:     **metadata: object,
0052: ) -> EvidenceRecord:
0053:     return EvidenceRecord(
0054:         id=stable_evidence_id(kind, source, content),
0055:         kind=kind,
0056:         source=source,
0057:         sha256=sha256_text(content),
0058:         content=content,
0059:         metadata=dict(metadata),
0060:     )
0061: 
0062: 
0063: def changed_paths(case: CaseInput) -> list[str]:
0064:     return sorted(
0065:         path
0066:         for path in set(case.before) | set(case.candidate)
0067:         if case.before.get(path) != case.candidate.get(path)
0068:     )
0069: 
0070: 
0071: def path_is_allowed(path: str, patterns: list[str]) -> bool:
0072:     return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
0073: 
0074: 
0075: def _finding(
0076:     *,
0077:     category: FindingCategory,
0078:     severity: Severity,
0079:     title: str,
0080:     explanation: str,
0081:     evidence_ids: list[str],
0082: ) -> Finding:
0083:     return Finding(
0084:         category=category,
0085:         severity=severity,
0086:         title=title,
0087:         explanation=explanation,
0088:         evidence_ids=sorted(set(evidence_ids)),
0089:         status=FindingStatus.VERIFIED,
0090:     )
0091: 
0092: 
0093: def _imported_modules(candidate: dict[str, str]) -> set[str]:
0094:     imported: set[str] = set()
0095:     for path, content in sorted(candidate.items()):
0096:         if not path.endswith(".py"):
0097:             continue
0098:         try:
0099:             tree = ast.parse(content, filename=path)
0100:         except SyntaxError:
0101:             continue
0102:         for node in ast.walk(tree):
0103:             if isinstance(node, ast.Import):
0104:                 imported.update(
0105:                     alias.name.split(".", 1)[0].replace("-", "_") for alias in node.names
0106:                 )
0107:             elif isinstance(node, ast.ImportFrom) and node.module:
0108:                 imported.add(node.module.split(".", 1)[0].replace("-", "_"))
0109:     return imported
0110: 
0111: 
0112: def _requirements(content: str) -> set[str]:
0113:     names: set[str] = set()
0114:     for raw in content.splitlines():
0115:         line = raw.strip()
0116:         if not line or line.startswith("#"):
0117:             continue
0118:         name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0]
0119:         if name:
0120:             names.add(name.replace("-", "_").lower())
0121:     return names
0122: 
0123: 
0124: def _trajectory_text(case: CaseInput) -> str:
0125:     return "\n".join(
0126:         str(event.get("content", "")) for event in case.trajectory if isinstance(event, dict)
0127:     )
0128: 
0129: 
0130: def collect_static_analysis(
0131:     case: CaseInput,
0132:     base_evidence: list[EvidenceRecord],
0133: ) -> StaticAnalysis:
0134:     evidence: list[EvidenceRecord] = []
0135:     findings: list[Finding] = []
0136:     specific_categories: set[FindingCategory] = set()
0137:     changed = changed_paths(case)
0138:     task_ids = [item.id for item in base_evidence if item.kind == "task"]
0139:     diff_ids = [item.id for item in base_evidence if item.kind == "diff"]
0140: 
0141:     changed_record = make_evidence(
0142:         "scan",
0143:         "changed-paths.json",
0144:         canonical_json({"changed_paths": changed}),
0145:         detector="changed_paths",
0146:     )
0147:     evidence.append(changed_record)
0148: 
0149:     outside = [path for path in changed if not path_is_allowed(path, case.allowed_changed_globs)]
0150:     if outside:
0151:         record = make_evidence(
0152:             "scan",
0153:             "out-of-scope-paths.json",
0154:             canonical_json(
0155:                 {
0156:                     "allowed_globs": case.allowed_changed_globs,
0157:                     "out_of_scope_paths": outside,
0158:                 }
0159:             ),
0160:             detector="scope_policy",
0161:             suggested_categories=[FindingCategory.OUT_OF_SCOPE_CHANGE.value],
0162:         )
0163:         evidence.append(record)
0164:         findings.append(
0165:             _finding(
0166:                 category=FindingCategory.OUT_OF_SCOPE_CHANGE,
0167:                 severity=Severity.HIGH,
0168:                 title="Candidate changes files outside the declared scope",
0169:                 explanation=f"Changed paths outside the allowed globs: {', '.join(outside)}.",
0170:                 evidence_ids=[record.id, *diff_ids],
0171:             )
0172:         )
0173:         specific_categories.add(FindingCategory.OUT_OF_SCOPE_CHANGE)
0174:         if _PRESERVATION_TASK.search(case.task):
0175:             findings.append(
0176:                 _finding(
0177:                     category=FindingCategory.BEHAVIORAL_REGRESSION,
0178:                     severity=Severity.HIGH,
0179:                     title="Out-of-scope edit violates an explicit preservation requirement",
0180:                     explanation="The task explicitly requires existing behavior to remain unchanged, but the candidate edits a protected path.",
0181:                     evidence_ids=[record.id, *task_ids, *diff_ids],
0182:                 )
0183:             )
0184:             specific_categories.add(FindingCategory.BEHAVIORAL_REGRESSION)
0185: 
0186:     imported = _imported_modules(case.candidate)
0187:     for path in sorted(_DEPENDENCY_FILES & set(case.candidate)):
0188:         before_requirements = _requirements(case.before.get(path, ""))
0189:         candidate_requirements = _requirements(case.candidate[path])
0190:         added = sorted(candidate_requirements - before_requirements)
0191:         unused = sorted(name for name in added if name not in imported)
0192:         if unused:
0193:             record = make_evidence(
0194:                 "scan",
0195:                 f"dependency-drift-{path}.json",
0196:                 canonical_json(
0197:                     {
0198:                         "dependency_file": path,
0199:                         "added_dependencies": added,
0200:                         "unused_added_dependencies": unused,
0201:                         "imported_modules": sorted(imported),
0202:                     }
0203:                 ),
0204:                 detector="dependency_drift",
0205:                 suggested_categories=[FindingCategory.DEPENDENCY_DRIFT.value],
0206:             )
0207:             evidence.append(record)
0208:             findings.append(
0209:                 _finding(
0210:                     category=FindingCategory.DEPENDENCY_DRIFT,
0211:                     severity=Severity.MEDIUM,
0212:                     title="Unused runtime dependency was added",
0213:                     explanation=f"New dependency entries are not imported by candidate Python code: {', '.join(unused)}.",
0214:                     evidence_ids=[record.id, *diff_ids],
0215:                 )
0216:             )
0217:             specific_categories.add(FindingCategory.DEPENDENCY_DRIFT)
0218: 
0219:     for path, content in sorted(case.candidate.items()):
0220:         for line_number, line in enumerate(content.splitlines(), start=1):
0221:             secret_match = _SECRET_ASSIGNMENT.search(line)
0222:             if secret_match:
0223:                 redacted_line = redact_secrets(line)
0224:                 record = make_evidence(
0225:                     "scan",
0226:                     f"credential-literal-{path}-{line_number}.json",
0227:                     canonical_json(
0228:                         {
0229:                             "path": path,
0230:                             "line": line_number,
0231:                             "redacted_source": redacted_line,
0232:                             "value_length": len(secret_match.group("value")),
0233:                         }
0234:                     ),
0235:                     detector="credential_literal",
0236:                     suggested_categories=[FindingCategory.SECRET_EXPOSURE.value],
0237:                 )
0238:                 evidence.append(record)
0239:                 findings.append(
0240:                     
...[EXCERPT TRUNCATED]...
```

## src/mergeproof/sandbox.py lines 1-423
```text
0001: from __future__ import annotations
0002: 
0003: import json
0004: import os
0005: import re
0006: import shutil
0007: import signal
0008: import subprocess
0009: import tempfile
0010: import time
0011: from dataclasses import dataclass, field
0012: from pathlib import Path, PurePosixPath
0013: 
0014: from .collector import make_evidence
0015: from .models import (
0016:     CaseInput,
0017:     CommandSpec,
0018:     EvidenceRecord,
0019:     Finding,
0020:     FindingCategory,
0021:     FindingStatus,
0022:     Severity,
0023: )
0024: from .utils import canonical_json, redact_secrets, sha256_text
0025: 
0026: DEFAULT_SANDBOX_IMAGE = (
0027:     "python@sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0"
0028: )
0029: _ALLOWED_PYTHON_MODULES = {"py_compile", "unittest"}
0030: _TEST_SKIP_OUTPUT = re.compile(r"(?i)(?:skipped\s*=\s*[1-9]\d*|skipped\s+[1-9]\d*)")
0031: _TEST_TIMING = re.compile(r"Ran (\d+) tests? in [0-9.]+s")
0032: _TMP_PATH = re.compile(r"/tmp/(?:tmp|pytest-of-)[A-Za-z0-9_.-]+")
0033: 
0034: 
0035: class SandboxUnavailable(RuntimeError):
0036:     """Raised when the configured isolation boundary cannot be established."""
0037: 
0038: 
0039: @dataclass(frozen=True)
0040: class VerificationAnalysis:
0041:     evidence: list[EvidenceRecord] = field(default_factory=list)
0042:     findings: list[Finding] = field(default_factory=list)
0043:     denied: bool = False
0044:     failed: bool = False
0045:     skipped: bool = False
0046:     timed_out: bool = False
0047:     specific_categories: set[FindingCategory] = field(default_factory=set)
0048: 
0049: 
0050: def _finding(
0051:     *,
0052:     category: FindingCategory,
0053:     severity: Severity,
0054:     title: str,
0055:     explanation: str,
0056:     evidence_ids: list[str],
0057: ) -> Finding:
0058:     return Finding(
0059:         category=category,
0060:         severity=severity,
0061:         title=title,
0062:         explanation=explanation,
0063:         evidence_ids=sorted(set(evidence_ids)),
0064:         status=FindingStatus.VERIFIED,
0065:     )
0066: 
0067: 
0068: def command_policy(spec: CommandSpec) -> tuple[bool, str]:
0069:     argv = spec.argv
0070:     if argv[0] != "python":
0071:         return False, f"executable is not allow-listed: {argv[0]}"
0072:     if len(argv) < 3 or argv[1] != "-m":
0073:         return False, "verification must invoke an allow-listed Python module with python -m"
0074:     module = argv[2]
0075:     if module not in _ALLOWED_PYTHON_MODULES:
0076:         return False, f"Python module is not allow-listed: {module}"
0077:     if any(token in {"-c", "--command"} for token in argv[3:]):
0078:         return False, "inline code execution is not allowed"
0079:     if module == "py_compile":
0080:         targets = argv[3:]
0081:         if not targets:
0082:             return False, "py_compile requires at least one candidate-relative target"
0083:         for target in targets:
0084:             path = PurePosixPath(target)
0085:             if path.is_absolute() or ".." in path.parts or not target.endswith(".py"):
0086:                 return False, f"unsafe py_compile target: {target}"
0087:     cwd = PurePosixPath(spec.cwd)
0088:     if cwd.is_absolute() or ".." in cwd.parts:
0089:         return False, f"unsafe verification cwd: {spec.cwd}"
0090:     return True, "allow-listed Python verification"
0091: 
0092: 
0093: def _normalize_output(value: str, *, host_root: Path, container_name: str) -> str:
0094:     text = value.replace(str(host_root), "<HOST_WORKSPACE>")
0095:     text = text.replace("/workspace", "<WORKSPACE>")
0096:     text = text.replace(container_name, "<CONTAINER>")
0097:     text = _TMP_PATH.sub("<TMP>", text)
0098:     text = _TEST_TIMING.sub(r"Ran \1 tests in <TIME>s", text)
0099:     return redact_secrets(text[-8_000:])
0100: 
0101: 
0102: def _materialize(tree: dict[str, str], root: Path) -> None:
0103:     root.chmod(0o755)
0104:     directories: set[Path] = {root}
0105:     for relative, content in sorted(tree.items()):
0106:         target = root / relative
0107:         target.parent.mkdir(parents=True, exist_ok=True)
0108:         directories.update(path for path in target.parents if path == root or root in path.parents)
0109:         target.write_text(content, encoding="utf-8")
0110:         target.chmod(0o644)
0111:     for directory in directories:
0112:         directory.chmod(0o755)
0113: 
0114: 
0115: def _container_name(case_id: str, spec: CommandSpec, attempt: int) -> str:
0116:     nonce = f"{os.getpid()}:{time.monotonic_ns()}"
0117:     digest = sha256_text(
0118:         f"{case_id}\0{canonical_json(spec.model_dump(mode='json'))}\0{attempt}\0{nonce}"
0119:     )[:16]
0120:     return f"mergeproof-{digest}"
0121: 
0122: 
0123: def _docker_prefix(
0124:     *,
0125:     root: Path,
0126:     spec: CommandSpec,
0127:     image: str,
0128:     container_name: str,
0129: ) -> list[str]:
0130:     workdir = "/workspace"
0131:     if spec.cwd not in {"", "."}:
0132:         workdir = f"/workspace/{PurePosixPath(spec.cwd).as_posix()}"
0133:     return [
0134:         "docker",
0135:         "run",
0136:         "--rm",
0137:         "--name",
0138:         container_name,
0139:         "--network",
0140:         "none",
0141:         "--read-only",
0142:         "--cap-drop",
0143:         "ALL",
0144:         "--security-opt",
0145:         "no-new-privileges",
0146:         "--pids-limit",
0147:         "64",
0148:         "--memory",
0149:         "256m",
0150:         "--memory-swap",
0151:         "256m",
0152:         "--cpus",
0153:         "1",
0154:         "--ulimit",
0155:         "core=0",
0156:         "--ulimit",
0157:         "nofile=128:128",
0158:         "--tmpfs",
0159:         "/tmp:rw,noexec,nosuid,nodev,size=32m",
0160:         "--mount",
0161:         f"type=bind,src={root},dst=/workspace,readonly",
0162:         "--workdir",
0163:         workdir,
0164:         "--user",
0165:         "65534:65534",
0166:         "--env",
0167:         "HOME=/tmp",
0168:         "--env",
0169:         "LANG=C.UTF-8",
0170:         "--env",
0171:         "LC_ALL=C.UTF-8",
0172:         "--env",
0173:         "PYTHONHASHSEED=0",
0174:         "--env",
0175:         "PYTHONDONTWRITEBYTECODE=1",
0176:         "--env",
0177:         "PYTHONSAFEPATH=1",
0178:         image,
0179:         *spec.argv,
0180:     ]
0181: 
0182: 
0183: def _docker_image_available(image: str) -> bool:
0184:     if shutil.which("docker") is None:
0185:         return False
0186:     completed = subprocess.run(
0187:         ["docker", "image", "inspect", image],
0188:         stdout=subprocess.DEVNULL,
0189:         stderr=subprocess.DEVNULL,
0190:         timeout=15,
0191:         check=False,
0192:     )
0193:     return completed.returncode == 0
0194: 
0195: 
0196: def _force_remove_container(container_name: str) -> None:
0197:     subprocess.run(
0198:         ["docker", "rm", "-f", container_name],
0199:         stdout=subprocess.DEVNULL,
0200:         stderr=subprocess.DEVNULL,
0201:         timeout=15,
0202:         check=False,
0203:     )
0204: 
0205: 
0206: def _execute_once(
0207:     *,
0208:     case_id: str,
0209:     spec: CommandSpec,
0210:     attempt: int,
0211:     root: Path,
0212:     image: str,
0213: ) -> dict[str, object]:
0214:     container_name = _container_name(case_id, spec, attempt)
0215:     command = _docker_prefix(
0216:         root=root,
0217:         spec=spec,
0218:         image=image,
0219:         container_name=container_name,
0220:     )
0221:     process = subprocess.Popen(
0222:         command,
0223:         stdout=subprocess.PIPE,
0224:         stderr=subprocess.PIPE,
0225:         text=True,
0226:         start_new_session=True,
0227:         env={
0228:             "PATH": os.environ.get("PATH", ""),
0229:             "DOCKER_HOST": os.environ.get("DOCKER_HOST", ""),
0230:             "HOME": os.environ.get("HOME", ""),
0231:             "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
0232:         },
0233:     )
0234:     timed_out = False
0235:     try:
0236:         stdout, stderr = process.communicate(timeout=spec.timeout_seconds)
0237:     except subprocess.TimeoutExpired:
0238:         timed_out = True
0239:         os.killpg(process.pid, signal.SIGKILL)
0240:         stdout, stderr = process.communicate()
0241:         _force_remove_container(container_name)
0242:     returncode = None if timed_out else process.returncode
0243:     normalized_stdout = _normalize_output(
0244:         stdout,
0245:         host_root=root,
0246:         container_name=container_name,
0247:     )
0248:     normalized_stderr = _normalize_output(
0249:         stderr,
0250:         host_root=root,
0251:         container_name=container_name,
0252:     )
0253:     combined = f"{normalized_stdout}\n{normalized_stderr}"
0254:     skipped = bool(_TEST_SKIP_OUTPUT.search(combined))
0255:     passed = not timed_out and returncode in spec.expected_exit_codes
0256:     return {
0257:         "argv": spec.argv,
0258:         "attempt": attempt,
0259:         "expected_exit_codes": spec.expected_exit_codes,
0260:         "returncode": returncode,
0261:         "passed": passed,
0262:         "skipped": skipped,
0263:         "timed_out": timed_out,
0264:         "stdout": normalized_stdout,
0265:         "stderr": normalized_stderr,
0266:     }
0267: 
0268: 
0269: def verify_case(
0270:     case: CaseInput,
0271:     *,
0272:     image: str = DEFAULT_SANDBOX_IMAGE,
0273: ) -> VerificationAnalysis:
0274:     if not _docker_image_available(image):
0275:         raise SandboxUnavailable(
0276:             f"digest-pinned sandbox image is unavailable: {image}; run the documented setup command"
0277:         )
0278: 
0279:     evidence: list[EvidenceRecord] = []
0280:     findings: list[Finding] = []
0281:     denied = failed = skipped = timed_out = False
0282:     specific_categories: set[FindingCategory] = set()
0283: 
0284:     policy_record = make_evidence(
0285:         "sandbox",
0286:         "sandbox-policy.json",
0287:         canonical_json(
0288:             {
0289:                 "allowlisted_python_modules": sorted(_ALLOWED_PYTHON_MODULES),
0290:                 "capabilities": "dropped-all",
0291:                 "image": image,
0292:                 "memory": "256m",
0293:                 "network": "none",
0294:                 "no_new_privileges": True,
0295:                 "pids_limit": 64,
0296:                 "read_only_root": True,
0297:                 "repository_mount": "read-only",
0298:                 "runtime_user": "65534:65534",
0299:             }
0300:         ),
0301:         isolation="docker",
0302:     )
0303:     evidence.append(policy_record)
0304: 
0305:     with tempfile.TemporaryDirectory(prefix=f"mergeproof-{case.id}-") as raw_root:
0306:         root = Path(raw_root)
0307:         _materialize(case.candidate, root)
0308:         for command_index, spec in enumerate(case.verification_commands, start=1):
0309:             allowed, reason = command_policy(spec)
0310:             if not allowed:
0311:                 denied = True
0312:                 specific_categories.add(FindingCategory.UNSAFE_COMMAND)
0313:                 record = make_evidence(
0314:                     "command",
0315:                     f"command-{command_index}-denied.json",
0316:                     canonical_json(
0317:                         {
0318:                             "allowed": False,
0319:                             "argv": spec.argv,
0320:                             "label": spec.label,
0321:                             "reason": reason,
0322:                         }
0323:                     ),
0324:                     suggested_categories=[FindingCategory.UNSAFE_COMMAND.value],
0325:                 )
0326:                 evidence.append(record)
0327:                 findings.append(
0328:                     _finding(
0329:                         
...[EXCERPT TRUNCATED]...
```

## src/mergeproof/providers.py lines 1-311
```text
0001: from __future__ import annotations
0002: 
0003: import os
0004: import re
0005: import time
0006: from abc import ABC, abstractmethod
0007: from pathlib import Path
0008: 
0009: import httpx
0010: 
0011: from .models import ModelUsage, ProviderResponse
0012: from .utils import extract_json_object, redact_secrets, stable_request_hash, write_json
0013: 
0014: _DURATION_COMPONENT = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|m)")
0015: 
0016: 
0017: def _parse_duration_seconds(value: str) -> float | None:
0018:     stripped = value.strip().lower()
0019:     try:
0020:         return max(0.0, float(stripped))
0021:     except ValueError:
0022:         pass
0023:     matches = list(_DURATION_COMPONENT.finditer(stripped))
0024:     if not matches or "".join(match.group(0) for match in matches) != stripped:
0025:         return None
0026:     factors = {"ms": 0.001, "s": 1.0, "m": 60.0}
0027:     return sum(float(match.group("value")) * factors[match.group("unit")] for match in matches)
0028: 
0029: 
0030: def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
0031:     candidates: list[float] = []
0032:     for header in ("retry-after", "x-ratelimit-reset-tokens"):
0033:         raw = response.headers.get(header)
0034:         if raw is None:
0035:             continue
0036:         parsed = _parse_duration_seconds(raw)
0037:         if parsed is not None:
0038:             candidates.append(parsed)
0039:     delay = max(candidates, default=min(2.0**attempt, 30.0))
0040:     return min(max(delay + 0.5, 0.5), 60.0)
0041: 
0042: 
0043: class ProviderError(RuntimeError):
0044:     pass
0045: 
0046: 
0047: class LLMProvider(ABC):
0048:     def __init__(self, *, model: str, record_dir: Path | None = None) -> None:
0049:         self.model = model
0050:         self.record_dir = record_dir
0051: 
0052:     @property
0053:     @abstractmethod
0054:     def name(self) -> str:
0055:         raise NotImplementedError
0056: 
0057:     @abstractmethod
0058:     def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
0059:         raise NotImplementedError
0060: 
0061:     def complete_json(self, *, agent: str, system: str, user: str) -> ProviderResponse:
0062:         request_hash = stable_request_hash(agent, self.model, system, user)
0063:         started = time.perf_counter()
0064:         try:
0065:             raw_text, token_usage = self._request(system=system, user=user)
0066:             data = extract_json_object(raw_text)
0067:         except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
0068:             raise ProviderError(f"{self.name} request failed: {redact_secrets(str(exc))}") from exc
0069:         latency_ms = round((time.perf_counter() - started) * 1000)
0070:         usage = ModelUsage(
0071:             provider=self.name,
0072:             model=self.model,
0073:             agent=agent,
0074:             request_hash=request_hash,
0075:             input_tokens=int(token_usage.get("input_tokens", 0)),
0076:             output_tokens=int(token_usage.get("output_tokens", 0)),
0077:             total_tokens=int(token_usage.get("total_tokens", 0)),
0078:             latency_ms=latency_ms,
0079:             http_attempts=max(1, int(token_usage.get("http_attempts", 1))),
0080:             rate_limit_wait_ms=max(0, int(token_usage.get("rate_limit_wait_ms", 0))),
0081:         )
0082:         response = ProviderResponse(data=data, raw_text=raw_text, usage=usage)
0083:         if self.record_dir is not None:
0084:             self._record(agent=agent, system=system, user=user, response=response)
0085:         return response
0086: 
0087:     def _record(self, *, agent: str, system: str, user: str, response: ProviderResponse) -> None:
0088:         assert self.record_dir is not None
0089:         payload = {
0090:             "schema_version": 1,
0091:             "request_hash": response.usage.request_hash,
0092:             "provider": self.name,
0093:             "model": self.model,
0094:             "agent": agent,
0095:             "request": {
0096:                 "system_sha256": stable_request_hash("system", self.model, system, ""),
0097:                 "user_sha256": stable_request_hash("user", self.model, "", user),
0098:                 "system_preview": redact_secrets(system[:1000]),
0099:                 "user_preview": redact_secrets(user[:2000]),
0100:             },
0101:             "response": {
0102:                 "data": response.data,
0103:                 "raw_text": redact_secrets(response.raw_text),
0104:                 "usage": response.usage.model_dump(mode="json"),
0105:             },
0106:         }
0107:         write_json(self.record_dir / f"{response.usage.request_hash}.json", payload)
0108: 
0109: 
0110: class GeminiProvider(LLMProvider):
0111:     name = "gemini"
0112: 
0113:     def __init__(
0114:         self,
0115:         *,
0116:         model: str,
0117:         api_key: str | None = None,
0118:         record_dir: Path | None = None,
0119:         timeout_seconds: float = 90,
0120:     ) -> None:
0121:         super().__init__(model=model.removeprefix("models/"), record_dir=record_dir)
0122:         resolved_api_key = api_key or os.getenv("GEMINI_API_KEY")
0123:         if not resolved_api_key:
0124:             raise ProviderError("GEMINI_API_KEY is required for the Gemini provider")
0125:         self.api_key: str = resolved_api_key
0126:         self.timeout_seconds = timeout_seconds
0127: 
0128:     def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
0129:         url = (
0130:             f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
0131:         )
0132:         headers = {"x-goog-api-key": self.api_key}
0133:         payload = {
0134:             "systemInstruction": {"parts": [{"text": system}]},
0135:             "contents": [{"role": "user", "parts": [{"text": user}]}],
0136:             "generationConfig": {
0137:                 "temperature": 0,
0138:                 "topP": 1,
0139:                 "responseMimeType": "application/json",
0140:             },
0141:         }
0142:         with httpx.Client(timeout=self.timeout_seconds) as client:
0143:             response = client.post(url, headers=headers, json=payload)
0144:             response.raise_for_status()
0145:             body = response.json()
0146:         raw_text = "".join(
0147:             str(part.get("text", "")) for part in body["candidates"][0]["content"]["parts"]
0148:         )
0149:         usage = body.get("usageMetadata", {})
0150:         return raw_text, {
0151:             "input_tokens": int(usage.get("promptTokenCount", 0)),
0152:             "output_tokens": int(usage.get("candidatesTokenCount", 0)),
0153:             "total_tokens": int(usage.get("totalTokenCount", 0)),
0154:         }
0155: 
0156: 
0157: class OpenAICompatibleProvider(LLMProvider):
0158:     def __init__(
0159:         self,
0160:         *,
0161:         provider_name: str,
0162:         model: str,
0163:         base_url: str,
0164:         api_key: str,
0165:         record_dir: Path | None = None,
0166:         timeout_seconds: float = 90,
0167:         minimum_interval_seconds: float = 0,
0168:         max_attempts: int = 4,
0169:     ) -> None:
0170:         super().__init__(model=model, record_dir=record_dir)
0171:         self._name = provider_name
0172:         self.base_url = base_url.rstrip("/")
0173:         self.api_key = api_key
0174:         self.timeout_seconds = timeout_seconds
0175:         self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
0176:         self.max_attempts = max(1, max_attempts)
0177:         self._last_request_started_at: float | None = None
0178: 
0179:     @property
0180:     def name(self) -> str:
0181:         return self._name
0182: 
0183:     def _pace(self) -> int:
0184:         waited = 0.0
0185:         if self._last_request_started_at is not None:
01
...[EXCERPT TRUNCATED]...
```
