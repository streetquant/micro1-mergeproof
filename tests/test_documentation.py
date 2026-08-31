from __future__ import annotations

import json
from pathlib import Path

import pytest

from driftproof.templates import CONTEXT_TEMPLATE
from scripts.verify_docs import DocumentationError, verify_docs


def minimal_docs(root: Path, *, readme: str = "# Test\n") -> Path:
    root.mkdir()
    (root / "README.md").write_text(readme, encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (root / "docs").mkdir()
    examples = root / "examples"
    examples.mkdir()
    project = examples / "candidate"
    project.mkdir()
    (project / "BUSINESS_CONTEXT.md").write_text(
        "The public contract must expose `customer_id`.\n",
        encoding="utf-8",
    )
    (examples / "driftproof-request.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "driftproof.request.v1",
                "project": "candidate",
            }
        ),
        encoding="utf-8",
    )
    (examples / "BUSINESS_CONTEXT.template.md").write_text(
        CONTEXT_TEMPLATE,
        encoding="utf-8",
    )
    return root


def test_repository_documentation_contract_is_current() -> None:
    result = verify_docs()

    assert result["verified"] is True
    assert result["document_count"] >= 13
    assert result["relative_link_references"] >= 30
    assert result["cli_command_references"] >= 60


def test_documentation_check_detects_broken_relative_link(tmp_path: Path) -> None:
    root = minimal_docs(tmp_path / "repository", readme="[missing](docs/missing.md)\n")

    with pytest.raises(DocumentationError, match="broken_links"):
        verify_docs(root)


def test_documentation_check_detects_invented_cli_command(tmp_path: Path) -> None:
    root = minimal_docs(tmp_path / "repository", readme="`driftproof invent`\n")

    with pytest.raises(DocumentationError, match="unknown_commands"):
        verify_docs(root)


def test_documentation_check_detects_stale_generated_template(tmp_path: Path) -> None:
    root = minimal_docs(tmp_path / "repository")
    (root / "examples" / "BUSINESS_CONTEXT.template.md").write_text(
        "# hand-edited drift\n",
        encoding="utf-8",
    )

    with pytest.raises(DocumentationError, match="executable template"):
        verify_docs(root)


def test_documentation_check_rejects_invalid_request_example(tmp_path: Path) -> None:
    root = minimal_docs(tmp_path / "repository")
    request = root / "examples" / "driftproof-request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "driftproof.request.v1",
                "project": "candidate",
                "invented_authority": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invented_authority"):
        verify_docs(root)
