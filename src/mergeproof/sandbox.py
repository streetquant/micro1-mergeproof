from __future__ import annotations

import json
import math
import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import NoReturn

from .collector import make_evidence
from .models import (
    CaseInput,
    CommandSpec,
    EvidenceRecord,
    Finding,
    FindingCategory,
    FindingStatus,
    Severity,
)
from .utils import canonical_json, redact_secrets, sha256_text

DEFAULT_SANDBOX_ENGINE = "bubblewrap"
_ALLOWED_PYTHON_MODULES = {"py_compile", "unittest"}
_TEST_SKIP_OUTPUT = re.compile(r"(?i)(?:skipped\s*=\s*[1-9]\d*|skipped\s+[1-9]\d*)")
_TEST_TIMING = re.compile(r"Ran (\d+) tests? in [0-9.]+s")
_TMP_PATH = re.compile(r"/tmp/(?:tmp|pytest-of-)[A-Za-z0-9_.-]+")
_TMPFS_BYTES = 32 * 1024 * 1024
_FILE_SIZE_BYTES = 16 * 1024 * 1024
_OPEN_FILES = 128


class SandboxUnavailable(RuntimeError):
    """Raised when the configured isolation boundary cannot be established."""


@dataclass(frozen=True)
class VerificationAnalysis:
    evidence: list[EvidenceRecord] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    denied: bool = False
    failed: bool = False
    skipped: bool = False
    timed_out: bool = False
    specific_categories: set[FindingCategory] = field(default_factory=set)


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


def command_policy(spec: CommandSpec) -> tuple[bool, str]:
    argv = spec.argv
    if argv[0] != "python":
        return False, f"executable is not allow-listed: {argv[0]}"
    if len(argv) < 3 or argv[1] != "-m":
        return False, "verification must invoke an allow-listed Python module with python -m"
    module = argv[2]
    if module not in _ALLOWED_PYTHON_MODULES:
        return False, f"Python module is not allow-listed: {module}"
    if any(token in {"-c", "--command"} for token in argv[3:]):
        return False, "inline code execution is not allowed"
    for token in argv[3:]:
        if "\x00" in token:
            return False, "verification arguments may not contain NUL bytes"
        candidate_path = PurePosixPath(token)
        if candidate_path.is_absolute() or ".." in candidate_path.parts:
            return False, f"unsafe verification argument: {token}"
    if module == "py_compile":
        targets = argv[3:]
        if not targets:
            return False, "py_compile requires at least one candidate-relative target"
        if any(not target.endswith(".py") for target in targets):
            return False, "py_compile targets must be Python source files"
    cwd = PurePosixPath(spec.cwd)
    if cwd.is_absolute() or ".." in cwd.parts:
        return False, f"unsafe verification cwd: {spec.cwd}"
    return True, "allow-listed Python verification"


def _normalize_output(value: str, *, host_root: Path, sandbox_id: str) -> str:
    text = value.replace(str(host_root), "<HOST_WORKSPACE>")
    text = text.replace("/workspace", "<WORKSPACE>")
    text = text.replace(sandbox_id, "<SANDBOX>")
    text = _TMP_PATH.sub("<TMP>", text)
    text = _TEST_TIMING.sub(r"Ran \1 tests in <TIME>s", text)
    return redact_secrets(text[-8_000:])


def _validated_target(root: Path, relative: str) -> Path:
    if not relative or "\x00" in relative:
        raise ValueError("candidate paths must be non-empty and contain no NUL bytes")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path in {PurePosixPath("."), PurePosixPath("/")}:
        raise ValueError(f"unsafe candidate path: {relative}")
    target = (root / Path(*path.parts)).resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError(f"candidate path escapes the materialization root: {relative}")
    return target


def _materialize(tree: dict[str, str], root: Path) -> None:
    root = root.resolve()
    root.chmod(0o755)
    directories: set[Path] = {root}
    for relative, content in sorted(tree.items()):
        target = _validated_target(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        directories.update(
            path for path in target.parents if path == root or path.is_relative_to(root)
        )
        target.write_text(content, encoding="utf-8")
        target.chmod(0o644)
    for directory in directories:
        directory.chmod(0o755)


def _sandbox_id(case_id: str, spec: CommandSpec, attempt: int) -> str:
    nonce = f"{time.monotonic_ns()}"
    digest = sha256_text(
        f"{case_id}\0{canonical_json(spec.model_dump(mode='json'))}\0{attempt}\0{nonce}"
    )[:16]
    return f"mergeproof-{digest}"


def _bubblewrap_executable() -> str:
    executable = shutil.which("bwrap")
    if executable is None:
        raise SandboxUnavailable("bubblewrap is required for bounded verification")
    return str(Path(executable).resolve())


def _runtime_bindings() -> list[str]:
    arguments: list[str] = []
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            arguments.extend(("--ro-bind", path, path))
    return arguments


def _bubblewrap_prefix(*, root: Path, spec: CommandSpec) -> list[str]:
    workdir = "/workspace"
    if spec.cwd not in {"", "."}:
        workdir = f"/workspace/{PurePosixPath(spec.cwd).as_posix()}"
    return [
        _bubblewrap_executable(),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        *_runtime_bindings(),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--size",
        str(_TMPFS_BYTES),
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(root),
        "/workspace",
        "--chdir",
        workdir,
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "PYTHONHASHSEED",
        "0",
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
        "--setenv",
        "PYTHONPATH",
        "/workspace",
        "--setenv",
        "PYTHONPYCACHEPREFIX",
        "/tmp/pycache",
        "--setenv",
        "PYTHONSAFEPATH",
        "1",
        "/usr/bin/python",
        *spec.argv[1:],
    ]


def _apply_resource_limits(timeout_seconds: float) -> None:
    # RLIMIT_AS destabilizes native runtimes that reserve large virtual address ranges.
    # RLIMIT_NPROC is counted per host UID on Linux and can reject threads because of
    # unrelated caller processes. Bubblewrap supplies the process/network/filesystem
    # boundary; these limits safely bound CPU time, output size, descriptors, and cores.
    cpu_seconds = max(1, math.ceil(timeout_seconds) + 1)
    limits = (
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, _FILE_SIZE_BYTES),
        (resource.RLIMIT_NOFILE, _OPEN_FILES),
        (resource.RLIMIT_CPU, cpu_seconds),
    )
    for resource_id, value in limits:
        resource.setrlimit(resource_id, (value, value))


@lru_cache(maxsize=1)
def _bubblewrap_available() -> bool:
    try:
        executable = _bubblewrap_executable()
        completed = subprocess.run(
            [
                executable,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                *_runtime_bindings(),
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--size",
                str(1024 * 1024),
                "--tmpfs",
                "/tmp",
                "--clearenv",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "/usr/bin/python",
                "-c",
                "pass",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, SandboxUnavailable):
        return False
    return completed.returncode == 0


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _execute_once(
    *,
    case_id: str,
    spec: CommandSpec,
    attempt: int,
    root: Path,
) -> dict[str, object]:
    sandbox_id = _sandbox_id(case_id, spec, attempt)
    command = _bubblewrap_prefix(root=root, spec=spec)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        preexec_fn=lambda: _apply_resource_limits(spec.timeout_seconds),
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=spec.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process)
        stdout, stderr = process.communicate()
    returncode = None if timed_out else process.returncode
    normalized_stdout = _normalize_output(stdout, host_root=root, sandbox_id=sandbox_id)
    normalized_stderr = _normalize_output(stderr, host_root=root, sandbox_id=sandbox_id)
    combined = f"{normalized_stdout}\n{normalized_stderr}"
    skipped = bool(_TEST_SKIP_OUTPUT.search(combined))
    passed = not timed_out and returncode in spec.expected_exit_codes
    return {
        "argv": spec.argv,
        "attempt": attempt,
        "expected_exit_codes": spec.expected_exit_codes,
        "returncode": returncode,
        "passed": passed,
        "skipped": skipped,
        "timed_out": timed_out,
        "stdout": normalized_stdout,
        "stderr": normalized_stderr,
    }


def _unsupported_engine(engine: str) -> NoReturn:
    raise SandboxUnavailable(
        f"unsupported sandbox engine {engine!r}; this release accepts only rootless bubblewrap"
    )


def verify_case(
    case: CaseInput,
    *,
    engine: str = DEFAULT_SANDBOX_ENGINE,
) -> VerificationAnalysis:
    if engine != DEFAULT_SANDBOX_ENGINE:
        _unsupported_engine(engine)
    if not _bubblewrap_available():
        raise SandboxUnavailable(
            "bubblewrap could not establish a networkless user/mount namespace; verification refused"
        )

    evidence: list[EvidenceRecord] = []
    findings: list[Finding] = []
    denied = failed = skipped = timed_out = False
    specific_categories: set[FindingCategory] = set()

    policy_record = make_evidence(
        "sandbox",
        "sandbox-policy.json",
        canonical_json(
            {
                "allowlisted_python_modules": sorted(_ALLOWED_PYTHON_MODULES),
                "core_dumps": "disabled",
                "cpu_time": "bounded by command timeout plus one second",
                "engine": engine,
                "environment": "cleared-and-rebuilt",
                "file_size_bytes": _FILE_SIZE_BYTES,
                "network": "unshared",
                "open_files": _OPEN_FILES,
                "process_namespace": "unshared",
                "project_import_path": "/workspace",
                "repository_mount": "read-only",
                "runtime": "/usr/bin/python",
                "system_runtime_mounts": "read-only",
                "tmpfs_bytes": _TMPFS_BYTES,
            }
        ),
        isolation=engine,
    )
    evidence.append(policy_record)

    with tempfile.TemporaryDirectory(prefix=f"mergeproof-{case.id}-") as raw_root:
        root = Path(raw_root)
        _materialize(case.candidate, root)
        for command_index, spec in enumerate(case.verification_commands, start=1):
            allowed, reason = command_policy(spec)
            if not allowed:
                denied = True
                specific_categories.add(FindingCategory.UNSAFE_COMMAND)
                record = make_evidence(
                    "command",
                    f"command-{command_index}-denied.json",
                    canonical_json(
                        {
                            "allowed": False,
                            "argv": spec.argv,
                            "label": spec.label,
                            "reason": reason,
                        }
                    ),
                    suggested_categories=[FindingCategory.UNSAFE_COMMAND.value],
                )
                evidence.append(record)
                findings.append(
                    _finding(
                        category=FindingCategory.UNSAFE_COMMAND,
                        severity=Severity.CRITICAL,
                        title="Declared verification command violates the execution policy",
                        explanation=reason,
                        evidence_ids=[record.id, policy_record.id],
                    )
                )
                continue

            for attempt in range(1, spec.repeat + 1):
                result = _execute_once(
                    case_id=case.id,
                    spec=spec,
                    attempt=attempt,
                    root=root,
                )
                result_record = make_evidence(
                    "command",
                    f"command-{command_index}-attempt-{attempt}.json",
                    canonical_json(
                        {
                            "allowed": True,
                            "label": spec.label,
                            "policy_reason": reason,
                            **result,
                        }
                    ),
                    suggested_categories=(
                        [FindingCategory.TEST_FAILURE.value] if not bool(result["passed"]) else []
                    ),
                )
                evidence.append(result_record)
                if bool(result["skipped"]):
                    skipped = True
                    specific_categories.add(FindingCategory.TEST_SKIP)
                    findings.append(
                        _finding(
                            category=FindingCategory.TEST_SKIP,
                            severity=Severity.HIGH,
                            title="Verification completed with skipped tests",
                            explanation=f"{spec.label} attempt {attempt} reported one or more skipped tests.",
                            evidence_ids=[result_record.id, policy_record.id],
                        )
                    )
                if not bool(result["passed"]):
                    failed = True
                    specific_categories.add(FindingCategory.TEST_FAILURE)
                    timed_out = timed_out or bool(result["timed_out"])
                    findings.append(
                        _finding(
                            category=FindingCategory.TEST_FAILURE,
                            severity=Severity.HIGH,
                            title=(
                                "Verification command exceeded its timeout"
                                if bool(result["timed_out"])
                                else "Verification command failed"
                            ),
                            explanation=(
                                f"{spec.label} attempt {attempt} exceeded {spec.timeout_seconds:.3g} seconds."
                                if bool(result["timed_out"])
                                else f"{spec.label} attempt {attempt} returned {result['returncode']}; expected {spec.expected_exit_codes}."
                            ),
                            evidence_ids=[result_record.id, policy_record.id],
                        )
                    )

    summary_record = make_evidence(
        "sandbox",
        "verification-summary.json",
        json.dumps(
            {
                "commands_declared": len(case.verification_commands),
                "denied": denied,
                "engine": engine,
                "failed": failed,
                "skipped": skipped,
                "timed_out": timed_out,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        suggested_categories=sorted(category.value for category in specific_categories),
    )
    evidence.append(summary_record)
    return VerificationAnalysis(
        evidence=evidence,
        findings=findings,
        denied=denied,
        failed=failed,
        skipped=skipped,
        timed_out=timed_out,
        specific_categories=specific_categories,
    )
