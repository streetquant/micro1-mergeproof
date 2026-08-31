from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from driftproof.project import snapshot_project
from driftproof.runner import (
    _bubblewrap_command,
    _runtime_python,
    find_dbt_executable,
    run_dbt_build,
)


def _minimal_dbt_project(root: Path) -> Path:
    root.mkdir()
    (root / "models").mkdir()
    (root / "dbt_project.yml").write_text(
        "\n".join(
            [
                "name: runner_fixture",
                "version: '1.0'",
                "config-version: 2",
                "profile: runner_fixture",
                "model-paths: ['models']",
                "models:",
                "  runner_fixture:",
                "    +materialized: table",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "profiles.yml").write_text(
        "\n".join(
            [
                "runner_fixture:",
                "  target: dev",
                "  outputs:",
                "    dev:",
                "      type: duckdb",
                "      path: runner.duckdb",
                "      schema: main",
                "      threads: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "models" / "value.sql").write_text("select 1 as value\n", encoding="utf-8")
    return root


def test_dbt_discovery_falls_back_to_the_active_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("fixture\n", encoding="utf-8")
    dbt = venv / "bin" / "dbt"
    dbt.write_text("#!/bin/sh\n", encoding="utf-8")
    dbt.chmod(0o755)

    monkeypatch.setattr("driftproof.runner.shutil.which", lambda _name: None)
    monkeypatch.setattr("driftproof.runner.sys.executable", str(python))

    assert find_dbt_executable() == str(dbt.resolve())


def test_runtime_python_resolves_an_absolute_virtualenv_symlink(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    interpreter = runtime / "bin" / "python3.13"
    interpreter.write_bytes(b"fixture")
    interpreter.chmod(0o755)

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(interpreter)
    dbt = venv / "bin" / "dbt"
    dbt.write_text("#!/bin/sh\n", encoding="utf-8")
    dbt.chmod(0o755)
    (venv / "lib" / "python3.13" / "site-packages").mkdir(parents=True)

    python_root, python_binary, site_relative = _runtime_python(venv)

    assert python_root == runtime
    assert python_binary == "python3.13"
    assert site_relative == "lib/python3.13/site-packages"


def test_bubblewrap_command_mounts_runtime_and_venv_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    interpreter = runtime / "bin" / "python3.13"
    interpreter.write_bytes(b"fixture")
    interpreter.chmod(0o755)

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(interpreter)
    dbt = venv / "bin" / "dbt"
    dbt.write_text("#!/bin/sh\n", encoding="utf-8")
    dbt.chmod(0o755)
    (venv / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    bwrap = tmp_path / "bwrap"
    bwrap.write_bytes(b"fixture")
    bwrap.chmod(0o755)

    original_which = shutil.which
    monkeypatch.setattr(
        "driftproof.runner.shutil.which",
        lambda name: str(bwrap) if name == "bwrap" else original_which(name),
    )

    command = _bubblewrap_command(str(dbt), worktree)

    runtime_index = command.index("/runtime-python")
    assert command[runtime_index - 1] == str(runtime)
    assert "/runtime-venv/lib/python3.13/site-packages" in command
    assert "/runtime-python/bin/python3.13" in command
    inline_index = command.index("-c")
    assert command[inline_index + 1] == "from dbt.cli.main import cli; cli()"
    assert "/runtime-venv/bin/python" not in command


@pytest.mark.skipif(
    shutil.which("bwrap") is None or shutil.which("dbt") is None,
    reason="bubblewrap and dbt are required for the real isolation regression",
)
def test_bubblewrap_build_runs_with_uv_managed_python(tmp_path: Path) -> None:
    project = _minimal_dbt_project(tmp_path / "project")
    original_hash = snapshot_project(project).tree_sha256

    result = run_dbt_build(
        project,
        work_root=tmp_path / "work",
        candidate_id="uv-runtime-regression",
        timeout_seconds=120,
        isolation="bubblewrap",
    )

    assert result.passed is True, result.stderr
    assert result.returncode == 0
    assert result.isolation == "bubblewrap"
    assert result.command == [
        "dbt",
        "build",
        "--project-dir",
        "<WORKTREE>",
        "--profiles-dir",
        "<WORKTREE>",
        "--no-use-colors",
    ]
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "No such file or directory" not in result.stderr
    assert snapshot_project(project).tree_sha256 == original_hash
    assert not (project / "runner.duckdb").exists()
