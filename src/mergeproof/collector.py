from __future__ import annotations

import ast
import fnmatch
import json
import re
from dataclasses import dataclass, field

from .models import (
    CaseInput,
    EvidenceRecord,
    Finding,
    FindingCategory,
    FindingStatus,
    Severity,
)
from .utils import canonical_json, redact_secrets, sha256_text, stable_evidence_id

_SKIP_MARKERS = ("@unittest.skip", "@pytest.mark.skip", "pytest.skip(")
_NONDETERMINISTIC_CALL = re.compile(
    r"\b(?:random\.(?:choice|choices|random|randint|randrange|shuffle)|secrets\.|uuid\.uuid4)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret)\b\s*[:=]\s*"
    r"(?P<quote>['\"])(?P<value>[^'\"\n]{16,})(?P=quote)"
)
_EDGE_TERMS = re.compile(
    r"(?i)\b(boundary|boundaries|zero|negative|minimum|maximum|inclusive|range)\b"
)
_SPECIFIC_TRAJECTORY_CLAIM = re.compile(
    r"(?i)\b(return type|remains?\s+(?:an?\s+)?(?:int|integer|string|bool)|"
    r"preserves?\s+the\s+type)\b"
)
_PRESERVATION_TASK = re.compile(r"(?i)\b(do not change|preserve|must remain|only when)\b")
_DEPENDENCY_FILES = {"requirements.txt", "requirements.in"}


@dataclass(frozen=True)
class StaticAnalysis:
    evidence: list[EvidenceRecord] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    edge_sensitive: bool = False
    specific_success_claim: bool = False
    specific_categories: set[FindingCategory] = field(default_factory=set)


def make_evidence(
    kind: str,
    source: str,
    content: str,
    **metadata: object,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=stable_evidence_id(kind, source, content),
        kind=kind,
        source=source,
        sha256=sha256_text(content),
        content=content,
        metadata=dict(metadata),
    )


def changed_paths(case: CaseInput) -> list[str]:
    return sorted(
        path
        for path in set(case.before) | set(case.candidate)
        if case.before.get(path) != case.candidate.get(path)
    )


def path_is_allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _finding(
    *,
    category: FindingCategory,
    severity: Severity,
    title: str,
    explanation: str,
    evidence_ids: list[str],
) -> Finding:
    return Finding(
        category=category,
        severity=severity,
        title=title,
        explanation=explanation,
        evidence_ids=sorted(set(evidence_ids)),
        status=FindingStatus.VERIFIED,
    )


def _imported_modules(candidate: dict[str, str]) -> set[str]:
    imported: set[str] = set()
    for path, content in sorted(candidate.items()):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".", 1)[0].replace("-", "_") for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0].replace("-", "_"))
    return imported


def _requirements(content: str) -> set[str]:
    names: set[str] = set()
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0]
        if name:
            names.add(name.replace("-", "_").lower())
    return names


def _trajectory_text(case: CaseInput) -> str:
    return "\n".join(
        str(event.get("content", "")) for event in case.trajectory if isinstance(event, dict)
    )


def collect_static_analysis(
    case: CaseInput,
    base_evidence: list[EvidenceRecord],
) -> StaticAnalysis:
    evidence: list[EvidenceRecord] = []
    findings: list[Finding] = []
    specific_categories: set[FindingCategory] = set()
    changed = changed_paths(case)
    task_ids = [item.id for item in base_evidence if item.kind == "task"]
    diff_ids = [item.id for item in base_evidence if item.kind == "diff"]

    changed_record = make_evidence(
        "scan",
        "changed-paths.json",
        canonical_json({"changed_paths": changed}),
        detector="changed_paths",
    )
    evidence.append(changed_record)

    outside = [path for path in changed if not path_is_allowed(path, case.allowed_changed_globs)]
    if outside:
        record = make_evidence(
            "scan",
            "out-of-scope-paths.json",
            canonical_json(
                {
                    "allowed_globs": case.allowed_changed_globs,
                    "out_of_scope_paths": outside,
                }
            ),
            detector="scope_policy",
            suggested_categories=[FindingCategory.OUT_OF_SCOPE_CHANGE.value],
        )
        evidence.append(record)
        findings.append(
            _finding(
                category=FindingCategory.OUT_OF_SCOPE_CHANGE,
                severity=Severity.HIGH,
                title="Candidate changes files outside the declared scope",
                explanation=f"Changed paths outside the allowed globs: {', '.join(outside)}.",
                evidence_ids=[record.id, *diff_ids],
            )
        )
        specific_categories.add(FindingCategory.OUT_OF_SCOPE_CHANGE)
        if _PRESERVATION_TASK.search(case.task):
            findings.append(
                _finding(
                    category=FindingCategory.BEHAVIORAL_REGRESSION,
                    severity=Severity.HIGH,
                    title="Out-of-scope edit violates an explicit preservation requirement",
                    explanation="The task explicitly requires existing behavior to remain unchanged, but the candidate edits a protected path.",
                    evidence_ids=[record.id, *task_ids, *diff_ids],
                )
            )
            specific_categories.add(FindingCategory.BEHAVIORAL_REGRESSION)

    imported = _imported_modules(case.candidate)
    for path in sorted(_DEPENDENCY_FILES & set(case.candidate)):
        before_requirements = _requirements(case.before.get(path, ""))
        candidate_requirements = _requirements(case.candidate[path])
        added = sorted(candidate_requirements - before_requirements)
        unused = sorted(name for name in added if name not in imported)
        if unused:
            record = make_evidence(
                "scan",
                f"dependency-drift-{path}.json",
                canonical_json(
                    {
                        "dependency_file": path,
                        "added_dependencies": added,
                        "unused_added_dependencies": unused,
                        "imported_modules": sorted(imported),
                    }
                ),
                detector="dependency_drift",
                suggested_categories=[FindingCategory.DEPENDENCY_DRIFT.value],
            )
            evidence.append(record)
            findings.append(
                _finding(
                    category=FindingCategory.DEPENDENCY_DRIFT,
                    severity=Severity.MEDIUM,
                    title="Unused runtime dependency was added",
                    explanation=f"New dependency entries are not imported by candidate Python code: {', '.join(unused)}.",
                    evidence_ids=[record.id, *diff_ids],
                )
            )
            specific_categories.add(FindingCategory.DEPENDENCY_DRIFT)

    for path, content in sorted(case.candidate.items()):
        for line_number, line in enumerate(content.splitlines(), start=1):
            secret_match = _SECRET_ASSIGNMENT.search(line)
            if secret_match:
                redacted_line = redact_secrets(line)
                record = make_evidence(
                    "scan",
                    f"credential-literal-{path}-{line_number}.json",
                    canonical_json(
                        {
                            "path": path,
                            "line": line_number,
                            "redacted_source": redacted_line,
                            "value_length": len(secret_match.group("value")),
                        }
                    ),
                    detector="credential_literal",
                    suggested_categories=[FindingCategory.SECRET_EXPOSURE.value],
                )
                evidence.append(record)
                findings.append(
                    _finding(
                        category=FindingCategory.SECRET_EXPOSURE,
                        severity=Severity.CRITICAL,
                        title="Credential-shaped literal is committed to source",
                        explanation=f"A credential-named variable is assigned a long literal in {path}:{line_number}; the value is redacted from evidence.",
                        evidence_ids=[record.id, *diff_ids],
                    )
                )
                specific_categories.add(FindingCategory.SECRET_EXPOSURE)

            if any(marker in line for marker in _SKIP_MARKERS):
                record = make_evidence(
                    "scan",
                    f"test-skip-{path}-{line_number}.json",
                    canonical_json(
                        {
                            "path": path,
                            "line": line_number,
                            "source": line.strip(),
                        }
                    ),
                    detector="test_skip",
                    suggested_categories=[FindingCategory.TEST_SKIP.value],
                )
                evidence.append(record)
                findings.append(
                    _finding(
                        category=FindingCategory.TEST_SKIP,
                        severity=Severity.HIGH,
                        title="Candidate disables a test",
                        explanation=f"A test skip marker appears in {path}:{line_number}.",
                        evidence_ids=[record.id, *diff_ids],
                    )
                )
                specific_categories.add(FindingCategory.TEST_SKIP)

        if path.endswith(".py") and _NONDETERMINISTIC_CALL.search(content):
            record = make_evidence(
                "scan",
                f"nondeterminism-{path}.json",
                canonical_json(
                    {
                        "path": path,
                        "detector": "random_or_unique_value_call",
                    }
                ),
                detector="nondeterminism",
                suggested_categories=[FindingCategory.FLAKY_BEHAVIOR.value],
            )
            evidence.append(record)
            findings.append(
                _finding(
                    category=FindingCategory.FLAKY_BEHAVIOR,
                    severity=Severity.HIGH,
                    title="Candidate introduces nondeterministic behavior",
                    explanation=f"A random or unique-value call appears in changed Python code at {path} despite deterministic acceptance requirements.",
                    evidence_ids=[record.id, *diff_ids],
                )
            )
            specific_categories.add(FindingCategory.FLAKY_BEHAVIOR)

        task_requests_containment = bool(
            re.search(r"(?i)\b(inside|within|contain|base directory|path escape)\b", case.task)
        )
        reads_path = bool(re.search(r"\.(?:read_text|read_bytes|open)\s*\(", content))
        joins_path = bool(re.search(r"\bbase\s*/\s*\w+|\(\s*base\s*/", content))
        has_containment_check = ".resolve(" in content and (
            ".is_relative_to(" in content or "commonpath" in content
        )
        if (
            path.endswith(".py")
            and task_requests_containment
            and reads_path
            and joins_path
            and not has_containment_check
        ):
            record = make_evidence(
                "scan",
                f"path-containment-{path}.json",
                canonical_json(
                    {
                        "path": path,
                        "path_join_detected": True,
                        "filesystem_read_detected": True,
                        "resolved_containment_check_detected": False,
                    }
                ),
                detector="path_containment",
                suggested_categories=[FindingCategory.PATH_TRAVERSAL.value],
            )
            evidence.append(record)
            findings.append(
                _finding(
                    category=FindingCategory.PATH_TRAVERSAL,
                    severity=Severity.CRITICAL,
                    title="Filesystem read lacks resolved containment enforcement",
                    explanation=f"{path} joins an untrusted name to the base path and reads it without a resolved containment check.",
                    evidence_ids=[record.id, *task_ids, *diff_ids],
                )
            )
            specific_categories.add(FindingCategory.PATH_TRAVERSAL)

    trajectory = _trajectory_text(case)
    specific_success_claim = bool(
        re.search(r"(?i)\b(pass(?:ed)?|success(?:ful|fully)?|exit code 0)\b", trajectory)
        and _SPECIFIC_TRAJECTORY_CLAIM.search(trajectory)
    )
    if specific_success_claim:
        record = make_evidence(
            "scan",
            "specific-agent-claim.json",
            canonical_json(
                {
                    "specific_semantic_claim_detected": True,
                    "redacted_claim": redact_secrets(trajectory),
                }
            ),
            detector="trajectory_claim",
        )
        evidence.append(record)

    return StaticAnalysis(
        evidence=evidence,
        findings=findings,
        edge_sensitive=bool(_EDGE_TERMS.search(case.task)),
        specific_success_claim=specific_success_claim,
        specific_categories=specific_categories,
    )


def finding_catalog(findings: list[Finding]) -> str:
    payload = [
        {
            "category": finding.category.value,
            "severity": finding.severity.value,
            "title": finding.title,
            "explanation": finding.explanation,
            "evidence_ids": finding.evidence_ids,
        }
        for finding in findings
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
