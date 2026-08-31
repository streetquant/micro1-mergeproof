from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mergeproof.intake import IntakeError, load_case, prepare_case_from_git, save_case


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "MergeProof Tests")
    git(repo, "config", "user.email", "mergeproof@example.invalid")
    (repo / "value.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "value.py")
    git(repo, "commit", "-q", "-m", "baseline")
    return repo


def test_prepare_git_request_and_round_trip(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    (repo / "test_value.py").write_text(
        "from value import value\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )

    case = prepare_case_from_git(
        repo=repo,
        base_ref="HEAD",
        task="Return two while preserving an integer result.",
        commands=["python -m py_compile value.py test_value.py"],
    )

    assert case.before["value.py"].endswith("return 1\n")
    assert case.candidate["value.py"].endswith("return 2\n")
    assert "test_value.py" in case.candidate
    assert case.metadata["changed_paths"] == ["test_value.py", "value.py"]
    assert case.allowed_changed_globs == ["test_value.py", "value.py"]
    assert case.verification_commands[0].argv == [
        "python",
        "-m",
        "py_compile",
        "value.py",
        "test_value.py",
    ]

    request_path = tmp_path / "request.json"
    save_case(case, request_path)
    assert load_case(request_path) == case


def test_prepare_without_commands_fails_closed_to_no_verification(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    case = prepare_case_from_git(
        repo=repo,
        base_ref="HEAD",
        task="Return two.",
    )

    assert case.verification_commands == []
    assert case.metadata["verification_selection"] == "none_fail_closed"


def test_git_timeout_is_reported_as_intake_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timed_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=30)

    monkeypatch.setattr("mergeproof.intake.subprocess.run", timed_out)
    with pytest.raises(IntakeError, match="timed out after 30 seconds"):
        prepare_case_from_git(
            repo=tmp_path,
            base_ref="HEAD",
            task="Review the change.",
        )


def test_prepare_requires_explicit_git_root(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    subdirectory = repo / "nested"
    subdirectory.mkdir()
    with pytest.raises(IntakeError, match="Git worktree root"):
        prepare_case_from_git(
            repo=subdirectory,
            base_ref="HEAD",
            task="Review the change.",
        )


def test_prepare_rejects_option_like_base_ref(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    with pytest.raises(IntakeError, match="unsafe Git base ref"):
        prepare_case_from_git(
            repo=repo,
            base_ref="--help",
            task="Review the change.",
        )


def test_prepare_excludes_control_artifacts(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    request = repo / "mergeproof-request.json"
    request.write_text("{}\n", encoding="utf-8")
    bundle = repo / "mergeproof-review"
    bundle.mkdir()
    (bundle / "old-result.json").write_text("{}\n", encoding="utf-8")

    case = prepare_case_from_git(
        repo=repo,
        base_ref="HEAD",
        task="Return two.",
        exclude_paths=[request, bundle],
    )

    assert "mergeproof-request.json" not in case.candidate
    assert "mergeproof-review/old-result.json" not in case.candidate
    assert case.metadata["changed_paths"] == ["value.py"]
    assert case.metadata["excluded_control_paths"] == [
        "mergeproof-request.json",
        "mergeproof-review",
    ]


def test_changed_binary_requires_explicit_request(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    (repo / "value.py").write_bytes(b"\x00\xff\x00")

    with pytest.raises(IntakeError, match="binary"):
        prepare_case_from_git(
            repo=repo,
            base_ref="HEAD",
            task="Review the change.",
            commands=["python -m py_compile value.py"],
        )


def test_load_case_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "request.json"
    link.symlink_to(target)

    with pytest.raises(IntakeError, match="regular file"):
        load_case(link)
