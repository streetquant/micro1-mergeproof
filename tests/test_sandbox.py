from __future__ import annotations

import os
from pathlib import Path

import pytest

from mergeproof.models import CaseInput, CommandSpec, FindingCategory
from mergeproof.sandbox import (
    _bubblewrap_available,
    _materialize,
    command_policy,
    verify_case,
)


def test_command_policy_is_narrow_and_path_safe() -> None:
    assert command_policy(
        CommandSpec(argv=["python", "-m", "unittest", "discover", "-s", "tests", "-q"])
    )[0]
    assert not command_policy(CommandSpec(argv=["bash", "-c", "echo unsafe"]))[0]
    assert not command_policy(CommandSpec(argv=["python", "-m", "pytest"]))[0]
    assert not command_policy(CommandSpec(argv=["python", "-m", "py_compile", "../escape.py"]))[0]


def test_materialize_rejects_parent_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe candidate path"):
        _materialize({"../outside.txt": "x"}, tmp_path)
    assert not (tmp_path.parent / "outside.txt").exists()


@pytest.mark.skipif(not _bubblewrap_available(), reason="bubblewrap namespace unavailable")
def test_verification_clears_parent_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOP_SECRET_FOR_TEST", "must-not-enter-sandbox")
    case = CaseInput(
        id="environment-test",
        title="Environment test",
        task="Run the local test without inheriting submitted credentials.",
        before={},
        candidate={
            "tests/test_environment.py": (
                "import os\n"
                "import unittest\n\n"
                "class EnvironmentTests(unittest.TestCase):\n"
                "    def test_secret_is_absent(self):\n"
                "        self.assertIsNone(os.getenv('TOP_SECRET_FOR_TEST'))\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            )
        },
        verification_commands=[
            CommandSpec(argv=["python", "-m", "unittest", "discover", "-s", "tests", "-q"])
        ],
        allowed_changed_globs=["tests/**"],
    )
    result = verify_case(case)
    assert result.failed is False
    assert result.denied is False
    assert result.specific_categories == set()
    assert "must-not-enter-sandbox" not in str(
        result.model_dump() if hasattr(result, "model_dump") else result
    )


@pytest.mark.skipif(not _bubblewrap_available(), reason="bubblewrap namespace unavailable")
def test_verification_can_import_the_read_only_project_package() -> None:
    case = CaseInput(
        id="project-import-test",
        title="Project import test",
        task="Run the local test against the candidate project package.",
        before={},
        candidate={
            "src/__init__.py": "",
            "src/value.py": "def value() -> int:\n    return 2\n",
            "tests/test_value.py": (
                "import unittest\n"
                "from src.value import value\n\n"
                "class ValueTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        self.assertEqual(value(), 2)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        },
        verification_commands=[
            CommandSpec(argv=["python", "-m", "unittest", "discover", "-s", "tests", "-q"])
        ],
        allowed_changed_globs=["src/**", "tests/**"],
    )
    result = verify_case(case)
    assert result.failed is False
    assert result.denied is False
    assert result.specific_categories == set()


@pytest.mark.skipif(not _bubblewrap_available(), reason="bubblewrap namespace unavailable")
def test_unsafe_command_is_denied_without_execution() -> None:
    case = CaseInput(
        id="unsafe-command-test",
        title="Unsafe command test",
        task="Verification must be local and bounded.",
        before={},
        candidate={"value.py": "x = 1\n"},
        verification_commands=[CommandSpec(argv=["bash", "-c", "touch should-not-exist"])],
        allowed_changed_globs=["value.py"],
    )
    result = verify_case(case)
    assert result.denied is True
    assert FindingCategory.UNSAFE_COMMAND in result.specific_categories
    assert not os.path.exists("should-not-exist")
