from __future__ import annotations

import pytest
from pydantic import ValidationError

from mergeproof.models import CaseInput, CommandSpec


def test_case_rejects_parent_traversal_path() -> None:
    with pytest.raises(ValidationError):
        CaseInput(
            id="bad",
            title="bad",
            task="bad",
            before={},
            candidate={"../escape.py": "pass\n"},
        )


def test_case_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        CaseInput(
            id="bad",
            title="bad",
            task="bad",
            before={},
            candidate={"/tmp/escape.py": "pass\n"},
        )


def test_command_requires_nonempty_tokens() -> None:
    with pytest.raises(ValidationError):
        CommandSpec(argv=["python", ""])


def test_command_rejects_escaping_cwd() -> None:
    with pytest.raises(ValidationError):
        CommandSpec(argv=["python", "-m", "py_compile", "value.py"], cwd="../outside")


def test_command_rejects_duplicate_exit_codes() -> None:
    with pytest.raises(ValidationError):
        CommandSpec(
            argv=["python", "-m", "py_compile", "value.py"],
            expected_exit_codes=[0, 0],
        )


def test_case_rejects_windows_separator_path() -> None:
    with pytest.raises(ValidationError):
        CaseInput(
            id="bad",
            title="bad",
            task="bad",
            before={},
            candidate={"src\\escape.py": "pass\n"},
        )


def test_case_rejects_oversized_file() -> None:
    with pytest.raises(ValidationError, match="1 MB"):
        CaseInput(
            id="oversized",
            title="oversized",
            task="Review the file.",
            before={},
            candidate={"large.txt": "x" * 1_000_001},
        )
