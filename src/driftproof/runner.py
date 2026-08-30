from __future__ import annotations

import os
import re
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


class BuildExecutionError(RuntimeError):
    pass


def _copy_project(project: Path, destination: Path) -> None:
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
    return normalized[-20000:]


def _dbt_command(dbt: str, worktree: Path) -> list[str]:
    return [
        dbt,
        "build",
        "--project-dir",
        str(worktree),
        "--profiles-dir",
        str(worktree),
        "--no-use-colors",
    ]


def _bubblewrap_command(dbt_command: list[str], worktree: Path) -> list[str]:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise BuildExecutionError("bubblewrap is not installed")
    return [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(worktree),
        str(worktree),
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--setenv",
        "HOME",
        str(worktree / ".home"),
        "--setenv",
        "DBT_SEND_ANONYMOUS_USAGE_STATS",
        "false",
        "--chdir",
        str(worktree),
        *dbt_command,
    ]


def run_dbt_build(
    project: Path,
    *,
    work_root: Path,
    candidate_id: str,
    timeout_seconds: int = 120,
    isolation: Literal["auto", "disposable_copy", "bubblewrap"] = "auto",
) -> BuildResult:
    project = project.resolve()
    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_id)[:80] or "candidate"
    worktree = work_root / safe_id
    _copy_project(project, worktree)
    (worktree / ".home").mkdir(exist_ok=True)
    snapshot = snapshot_project(worktree)

    dbt = shutil.which("dbt")
    if dbt is None:
        raise BuildExecutionError(
            "dbt is not available on PATH; install the pinned dbt optional dependencies"
        )
    dbt = str(Path(dbt).resolve())
    direct_command = _dbt_command(dbt, worktree)
    selected: Literal["disposable_copy", "bubblewrap"] = "disposable_copy"
    command = direct_command
    if isolation in {"auto", "bubblewrap"} and shutil.which("bwrap"):
        command = _bubblewrap_command(direct_command, worktree)
        selected = "bubblewrap"
    elif isolation == "bubblewrap":
        raise BuildExecutionError("bubblewrap isolation was required but is unavailable")

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(worktree / ".home"),
        "NO_COLOR": "1",
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "PYTHONHASHSEED": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
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

    if selected == "bubblewrap" and returncode != 0 and isolation == "auto":
        bwrap_error = _normalize_output(stderr, project=project, worktree=worktree)
        if any(
            token in bwrap_error.lower()
            for token in (
                "operation not permitted",
                "creating new namespace failed",
                "permission denied",
            )
        ):
            selected = "disposable_copy"
            command = direct_command
            started = time.perf_counter()
            completed = subprocess.run(
                command,
                cwd=worktree,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = (
                "bubblewrap unavailable in this environment; used an isolated disposable copy.\n"
                + completed.stderr
            )
            duration_ms = round((time.perf_counter() - started) * 1000)

    return BuildResult(
        command=["dbt", *direct_command[1:]],
        returncode=returncode,
        passed=returncode == 0,
        stdout=_normalize_output(stdout, project=project, worktree=worktree),
        stderr=_normalize_output(stderr, project=project, worktree=worktree),
        duration_ms=duration_ms,
        isolation=selected,
        worktree_sha256=snapshot.tree_sha256,
    )
