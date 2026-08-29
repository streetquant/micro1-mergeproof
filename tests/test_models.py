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
