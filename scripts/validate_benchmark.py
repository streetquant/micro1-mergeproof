from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mergeproof.benchmark import load_cases, load_gold
from mergeproof.models import CommandSpec
from mergeproof.utils import write_json

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PYTHON_MODULES = {"unittest", "py_compile"}
CREDENTIAL_ASSIGNMENT = re.compile(r"(?i)(?:api[_-]?key|token|secret)\s*=\s*['\"][^'\"]{16,}['\"]")
SKIP_MARKERS = ("@unittest.skip", "@pytest.mark.skip", "pytest.skip(")


def changed_paths(before: dict[str, str], candidate: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(before) | set(candidate) if before.get(path) != candidate.get(path)
    )


def is_allowed_path(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def command_policy(spec: CommandSpec) -> tuple[bool, str]:
    argv = spec.argv
    if argv[0] != "python":
        return False, f"executable is not allow-listed: {argv[0]}"
    if len(argv) >= 3 and argv[1] == "-m" and argv[2] not in ALLOWED_PYTHON_MODULES:
        return False, f"Python module is not allow-listed: {argv[2]}"
    if any(token in {"-c", "--command"} for token in argv[1:]):
        return False, "inline code execution is not allowed"
    return True, "allow-listed Python verification"


def materialize(tree: dict[str, str], root: Path) -> None:
    for relative, content in sorted(tree.items()):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def normalize_output(value: object, root: Path) -> str:
    text = str(value or "").replace(str(root), "<FIXTURE_ROOT>")
    text = re.sub(r"Ran (\d+) tests? in [0-9.]+s", r"Ran \1 tests in <TIME>s", text)
    return text[-4000:]


def run_command(spec: CommandSpec, root: Path) -> list[dict[str, Any]]:
    allowed, reason = command_policy(spec)
    if not allowed:
        return [{"allowed": False, "reason": reason, "argv": spec.argv}]
    argv = [sys.executable, *spec.argv[1:]]
    cwd = (root / spec.cwd).resolve()
    if not cwd.is_relative_to(root.resolve()):
        return [{"allowed": False, "reason": "cwd escapes fixture root", "argv": spec.argv}]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root / ".home"),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    records: list[dict[str, Any]] = []
    for attempt in range(1, spec.repeat + 1):
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
            records.append(
                {
                    "allowed": True,
                    "reason": reason,
                    "argv": spec.argv,
                    "attempt": attempt,
                    "returncode": completed.returncode,
                    "passed": completed.returncode in spec.expected_exit_codes,
                    "stdout": normalize_output(completed.stdout, root),
                    "stderr": normalize_output(completed.stderr, root),
                    "timed_out": False,
                }
            )
        except subprocess.TimeoutExpired as exc:
            records.append(
                {
                    "allowed": True,
                    "reason": reason,
                    "argv": spec.argv,
                    "attempt": attempt,
                    "returncode": None,
                    "passed": False,
                    "stdout": normalize_output(exc.stdout, root),
                    "stderr": normalize_output(exc.stderr, root),
                    "timed_out": True,
                }
            )
    return records


def main() -> None:
    cases = load_cases(ROOT / "benchmark/cases.json")
    gold = load_gold(ROOT / "benchmark/gold.json")
    records: list[dict[str, Any]] = []
    errors: list[str] = []

    for case in cases:
        expected = gold[case.id]
        paths = changed_paths(case.before, case.candidate)
        out_of_scope = [
            path for path in paths if not is_allowed_path(path, case.allowed_changed_globs)
        ]
        source_blob = "\n".join(case.candidate.values())
        skip_markers = [marker for marker in SKIP_MARKERS if marker in source_blob]
        credential_literals = CREDENTIAL_ASSIGNMENT.findall(source_blob)
        requirement_lines = {
            line.strip().split("==", 1)[0].replace("-", "_")
            for line in case.candidate.get("requirements.txt", "").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        python_blob = "\n".join(
            content for path, content in case.candidate.items() if path.endswith(".py")
        )
        unused_dependencies = sorted(
            dependency
            for dependency in requirement_lines
            if not re.search(rf"(?m)^\s*(?:from|import)\s+{re.escape(dependency)}\b", python_blob)
        )

        with tempfile.TemporaryDirectory(prefix=f"mergeproof-{case.id}-") as raw:
            fixture_root = Path(raw)
            materialize(case.candidate, fixture_root)
            command_runs = [
                run
                for spec in case.verification_commands
                for run in run_command(spec, fixture_root)
            ]

        any_denied = any(not run["allowed"] for run in command_runs)
        any_failed = any(run.get("allowed") and not run.get("passed") for run in command_runs)
        all_passed = bool(command_runs) and all(
            run.get("allowed") and run.get("passed") for run in command_runs
        )
        combined_output = "\n".join(
            f"{run.get('stdout', '')}\n{run.get('stderr', '')}" for run in command_runs
        )

        categories = {category.value for category in expected.categories}
        if expected.safe_to_merge:
            if out_of_scope:
                errors.append(f"{case.id}: safe case changes out-of-scope paths {out_of_scope}")
            if skip_markers:
                errors.append(f"{case.id}: safe case contains skip markers {skip_markers}")
            if credential_literals:
                errors.append(f"{case.id}: safe case contains credential-shaped assignment")
            if unused_dependencies:
                errors.append(f"{case.id}: safe case has unused dependencies {unused_dependencies}")
            if not all_passed:
                errors.append(f"{case.id}: safe case verification did not fully pass")
        if "test_failure" in categories and not any_failed:
            errors.append(f"{case.id}: gold requires a reproducible test failure")
        if "test_skip" in categories:
            if not skip_markers:
                errors.append(f"{case.id}: gold requires a source skip marker")
            if "skipped" not in combined_output.lower():
                errors.append(f"{case.id}: skipped test was not visible in command output")
        if "out_of_scope_change" in categories and not out_of_scope:
            errors.append(f"{case.id}: gold requires an out-of-scope changed path")
        if "dependency_drift" in categories and not unused_dependencies:
            errors.append(f"{case.id}: gold requires an unused dependency")
        if "secret_exposure" in categories and not credential_literals:
            errors.append(f"{case.id}: gold requires a credential-shaped assignment")
        if "unsafe_command" in categories and not any_denied:
            errors.append(f"{case.id}: gold requires command-policy denial")
        if "flaky_behavior" in categories and "random." not in source_blob:
            errors.append(f"{case.id}: gold requires an explicit nondeterministic source")
        if "unverified_claim" in categories:
            trajectory_text = json.dumps(case.trajectory).lower()
            if not any(word in trajectory_text for word in ("passed", "successfully")):
                errors.append(f"{case.id}: gold requires a positive agent claim")
            if not any_failed:
                errors.append(f"{case.id}: positive agent claim is not contradicted by execution")

        records.append(
            {
                "case_id": case.id,
                "safe_to_merge": expected.safe_to_merge,
                "gold_categories": sorted(categories),
                "changed_paths": paths,
                "out_of_scope_paths": out_of_scope,
                "skip_markers": skip_markers,
                "credential_assignment_count": len(credential_literals),
                "unused_dependencies": unused_dependencies,
                "command_runs": command_runs,
                "all_commands_passed": all_passed,
                "any_command_denied": any_denied,
                "any_command_failed": any_failed,
            }
        )

    output = {
        "schema_version": 1,
        "case_count": len(cases),
        "safe_count": sum(item.safe_to_merge for item in gold.values()),
        "unsafe_count": sum(not item.safe_to_merge for item in gold.values()),
        "validation_passed": not errors,
        "errors": errors,
        "cases": records,
    }
    write_json(ROOT / "results/benchmark-validation.json", output)
    print(json.dumps({key: value for key, value in output.items() if key != "cases"}, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
