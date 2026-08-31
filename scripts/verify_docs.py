from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from typer.main import get_command

from driftproof.cli import app as driftproof_app
from driftproof.models import DriftProofReviewRequest
from driftproof.templates import CONTEXT_TEMPLATE
from mergeproof.cli import app as mergeproof_app
from mergeproof.utils import pretty_json, sha256_text

ROOT = Path(__file__).resolve().parents[1]
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_CLI_COMMAND = re.compile(r"\b(driftproof|mergeproof)[ \t]+([a-z][a-z0-9-]*)\b")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")


class DocumentationError(RuntimeError):
    """Raised when executable documentation contracts have drifted."""


def _markdown_files(root: Path) -> list[Path]:
    files = [
        path
        for path in (root / "README.md", root / "CHANGELOG.md", root / "AGENTS.md")
        if path.is_file()
    ]
    files.extend(sorted((root / "docs").glob("*.md")))
    files.extend(sorted((root / "submission").glob("*.md")))
    return files


def _relative_link_target(document: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#") or target.startswith(_EXTERNAL_PREFIXES):
        return None
    relative = target.split("#", 1)[0]
    if not relative:
        return None
    return (document.parent / relative).resolve(strict=False)


def _group_commands(app: Any) -> set[str]:
    command = get_command(app)
    commands = getattr(command, "commands", None)
    if not isinstance(commands, Mapping):
        raise DocumentationError("expected a command group while building the CLI catalog")
    names = {key for key in commands if isinstance(key, str)}
    if len(names) != len(commands):
        raise DocumentationError("command group contains a non-string command name")
    return names


def _command_catalog() -> dict[str, set[str]]:
    return {
        "driftproof": _group_commands(driftproof_app),
        "mergeproof": _group_commands(mergeproof_app),
    }


def verify_docs(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    documents = _markdown_files(root)
    if not documents:
        raise DocumentationError("no Markdown documentation was found")

    broken_links: list[dict[str, str]] = []
    unknown_commands: list[dict[str, str]] = []
    command_catalog = _command_catalog()
    command_references = 0
    link_references = 0

    for document in documents:
        text = document.read_text(encoding="utf-8", errors="strict")
        for raw_target in _MARKDOWN_LINK.findall(text):
            target = _relative_link_target(document, raw_target)
            if target is None:
                continue
            link_references += 1
            if not target.exists():
                broken_links.append(
                    {
                        "document": document.relative_to(root).as_posix(),
                        "target": raw_target,
                    }
                )
        for tool, command in _CLI_COMMAND.findall(text):
            command_references += 1
            if command not in command_catalog[tool]:
                unknown_commands.append(
                    {
                        "document": document.relative_to(root).as_posix(),
                        "command": f"{tool} {command}",
                    }
                )

    request_path = root / "examples" / "driftproof-request.json"
    if request_path.is_symlink() or not request_path.is_file():
        raise DocumentationError("examples/driftproof-request.json is missing or unsafe")
    request = DriftProofReviewRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    example_base = request_path.parent.resolve()
    project = (example_base / request.project).resolve(strict=False)
    context = (
        (example_base / request.context).resolve(strict=False)
        if request.context is not None
        else project / "BUSINESS_CONTEXT.md"
    )
    if not project.is_dir() or project.is_symlink():
        raise DocumentationError(f"example request project is missing or unsafe: {project}")
    if not context.is_file() or context.is_symlink():
        raise DocumentationError(f"example request context is missing or unsafe: {context}")

    template_path = root / "examples" / "BUSINESS_CONTEXT.template.md"
    if template_path.is_symlink() or not template_path.is_file():
        raise DocumentationError("examples/BUSINESS_CONTEXT.template.md is missing or unsafe")
    template = template_path.read_text(encoding="utf-8")
    if template != CONTEXT_TEMPLATE:
        raise DocumentationError(
            "examples/BUSINESS_CONTEXT.template.md differs from the executable template"
        )

    if broken_links or unknown_commands:
        raise DocumentationError(
            json.dumps(
                {
                    "broken_links": broken_links,
                    "unknown_commands": unknown_commands,
                },
                sort_keys=True,
            )
        )

    payload = {
        "schema_version": 1,
        "verified": True,
        "documents": [path.relative_to(root).as_posix() for path in documents],
        "document_count": len(documents),
        "relative_link_references": link_references,
        "cli_command_references": command_references,
        "example_request_sha256": sha256_text(pretty_json(request.model_dump(mode="json")) + "\n"),
        "context_template_sha256": sha256_text(template),
        "command_catalog": {tool: sorted(commands) for tool, commands in command_catalog.items()},
    }
    return payload


def main() -> None:
    print(json.dumps(verify_docs(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
