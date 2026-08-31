from __future__ import annotations

import math
import re
import resource
import shutil
import subprocess
import time
from pathlib import Path
from typing import Literal

from .models import BuildResult
from .project import snapshot_project

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CLOCK = re.compile(r"(?m)^\s*\d{2}:\d{2}:\d{2}\s+")
_DURATION = re.compile(r"\b\d+(?:\.\d+)?s\b")
_BWRAP_UNAVAILABLE = (
    "operation not permitted",
    "creating new namespace failed",
    "permission denied",
    "no permissions to creating new namespace",
)
_TMPFS_BYTES = 128 * 1024 * 1024
_FILE_SIZE_BYTES = 128 * 1024 * 1024
_OPEN_FILES = 256


class BuildExecutionError(RuntimeError):
    pass


def _copy_project(project: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise BuildExecutionError(f"review worktree may not be a symlink: {destination}")
    if destination.exists():
        shutil.rmtree(destination)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".venv", "logs", "target", "dbt_packages", "__pycache__"}
            or name.endswith(".pyc")
        }

    shutil.copytree(project, destination, ignore=ignore)


def _normalize_output(value: str, *, project: Path, worktree: Path) -> str:
    normalized = _ANSI.sub("", value)
    normalized = normalized.replace(str(project), "<PROJECT>").replace(str(worktree), "<WORKTREE>")
    normalized = _CLOCK.sub("", normalized)
    normalized = _DURATION.sub("<DURATION>", normalized)
    return normalized[-20_000:]


def _display_command() -> list[str]:
    return [
        "dbt",
        "build",
        "--project-dir",
        "<WORKTREE>",
        "--profiles-dir",
        "<WORKTREE>",
        "--no-use-colors",
    ]


def _direct_command(dbt: str, worktree: Path) -> list[str]:
    return [
        dbt,
        "build",
        "--project-dir",
        str(worktree),
        "--profiles-dir",
        str(worktree),
        "--no-use-colors",
    ]


def _runtime_bindings() -> list[str]:
    arguments: list[str] = []
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            arguments.extend(("--ro-bind", path, path))
    return arguments


def _runtime_venv(dbt: str) -> Path:
    venv = Path(dbt).resolve().parent.parent
    if not (venv / "bin" / "python").exists() or not (venv / "bin" / "dbt").is_file():
        raise BuildExecutionError(f"dbt is not installed in a usable Python environment: {dbt}")
    return venv


def _runtime_python(venv: Path) -> tuple[Path, str, str]:
    """Resolve the immutable interpreter and the venv site-packages mount.

    uv virtual environments commonly use an absolute ``bin/python`` symlink.
    Mounting only the virtual environment leaves that interpreter target absent
    inside bubblewrap. Bind the resolved base runtime independently and expose
    only the virtual environment's site-packages through ``PYTHONPATH``.
    """

    python = (venv / "bin" / "python").resolve(strict=True)
    python_root = python.parent.parent
    if not python.is_file() or not python_root.is_dir():
        raise BuildExecutionError(f"Python runtime is not a regular installation: {python}")

    site_packages = sorted(
        path
        for base in (venv / "lib", venv / "lib64")
        if base.is_dir() and not base.is_symlink()
        for path in base.glob("python*/site-packages")
        if path.is_dir() and not path.is_symlink()
    )
    if len(site_packages) != 1:
        raise BuildExecutionError(
            "dbt virtual environment must contain exactly one regular site-packages directory"
        )
    site_relative = site_packages[0].relative_to(venv).as_posix()
    return python_root, python.name, site_relative


def _bubblewrap_command(dbt: str, worktree: Path) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise BuildExecutionError("bubblewrap is not installed")
    venv = _runtime_venv(dbt)
    python_root, python_binary, site_relative = _runtime_python(venv)
    return [
        str(Path(bwrap).resolve()),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        *_runtime_bindings(),
        "--ro-bind",
        str(venv),
        "/runtime-venv",
        "--ro-bind",
        str(python_root),
        "/runtime-python",
        "--bind",
        str(worktree),
        "/workspace",
        "--size",
        str(_TMPFS_BYTES),
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        "/workspace",
        "--clearenv",
        "--setenv",
        "PATH",
        "/runtime-python/bin:/usr/bin:/bin",
        "--setenv",
        "PYTHONPATH",
        f"/runtime-venv/{site_relative}",
        "--setenv",
        "VIRTUAL_ENV",
        "/runtime-venv",
        "--setenv",
        "HOME",
        "/workspace/.home",
        "--setenv",
        "NO_COLOR",
        "1",
        "--setenv",
        "DBT_SEND_ANONYMOUS_USAGE_STATS",
        "false",
        "--setenv",
        "PYTHONHASHSEED",
        "0",
        "--setenv",
        "PYTHONNOUSERSITE",
        "1",
        "--setenv",
        "PYTHONPYCACHEPREFIX",
        "/tmp/pycache",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        f"/runtime-python/bin/{python_binary}",
        "-c",
        "from dbt.cli.main import cli; cli()",
        "build",
        "--project-dir",
        "/workspace",
        "--profiles-dir",
        "/workspace",
        "--no-use-colors",
    ]


def _apply_resource_limits(timeout_seconds: int) -> None:
    # RLIMIT_AS is unsafe for DuckDB and other native runtimes that reserve large virtual
    # address ranges. RLIMIT_NPROC is per host UID on Linux, so applying it here can deny
    # threads based on unrelated processes owned by the caller. Bubblewrap supplies the
    # process, network, and filesystem boundary; these limits bound CPU, output, file
    # descriptors, and core dumps without creating host-load-dependent false failures.
    cpu_seconds = max(1, math.ceil(timeout_seconds) + 5)
    for resource_id, value in (
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, _FILE_SIZE_BYTES),
        (resource.RLIMIT_NOFILE, _OPEN_FILES),
        (resource.RLIMIT_CPU, cpu_seconds),
    ):
        resource.setrlimit(resource_id, (value, value))


def _safe_environment(home: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "NO_COLOR": "1",
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": str(home / ".pycache"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def run_dbt_build(
    project: Path,
    *,
    work_root: Path,
    candidate_id: str,
    timeout_seconds: int = 120,
    isolation: Literal["auto", "disposable_copy", "bubblewrap"] = "auto",
    allow_unconfined: bool = False,
) -> BuildResult:
    project = project.resolve()
    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_id)[:80] or "candidate"
    worktree = work_root / safe_id
    _copy_project(project, worktree)
    home = worktree / ".home"
    home.mkdir(exist_ok=True)
    snapshot = snapshot_project(worktree)

    dbt = shutil.which("dbt")
    if dbt is None:
        raise BuildExecutionError(
            "dbt is not available on PATH; install the pinned dbt optional dependencies"
        )
    dbt = str(Path(dbt).resolve())

    selected: Literal["disposable_copy", "bubblewrap"]
    if isolation in {"auto", "bubblewrap"}:
        if shutil.which("bwrap") is None:
            raise BuildExecutionError(
                "bubblewrap isolation is unavailable; DriftProof refused to execute candidate code. "
                "Use disposable_copy only with an explicit allow_unconfined acknowledgment for a trusted fixture."
            )
        selected = "bubblewrap"
        command = _bubblewrap_command(dbt, worktree)
    else:
        if not allow_unconfined:
            raise BuildExecutionError(
                "disposable_copy is not a security sandbox; pass allow_unconfined only for a trusted fixture"
            )
        selected = "disposable_copy"
        command = _direct_command(dbt, worktree)

    environment = _safe_environment(home)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=worktree,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            preexec_fn=lambda: _apply_resource_limits(timeout_seconds),
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr += f"\nDriftProof timeout after {timeout_seconds} seconds."
    duration_ms = round((time.perf_counter() - started) * 1000)

    normalized_stderr = _normalize_output(stderr, project=project, worktree=worktree)
    if (
        selected == "bubblewrap"
        and returncode != 0
        and any(token in normalized_stderr.lower() for token in _BWRAP_UNAVAILABLE)
    ):
        raise BuildExecutionError(
            "bubblewrap could not establish the required namespace; DriftProof refused to fall back "
            "to unconfined candidate execution"
        )

    return BuildResult(
        command=_display_command(),
        returncode=returncode,
        passed=returncode == 0,
        stdout=_normalize_output(stdout, project=project, worktree=worktree),
        stderr=normalized_stderr,
        duration_ms=duration_ms,
        isolation=selected,
        worktree_sha256=snapshot.tree_sha256,
    )
