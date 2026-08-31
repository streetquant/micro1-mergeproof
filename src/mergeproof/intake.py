from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from .models import CaseInput, CommandSpec
from .utils import canonical_json, write_json

_MAX_INPUT_BYTES = 15_000_000
_MAX_FILE_BYTES = 1_000_000
_MAX_FILES = 2_000
_MAX_TOTAL_TREE_BYTES = 9_500_000


class IntakeError(ValueError):
    """Raised when a request or Git snapshot cannot be admitted safely."""


def load_case(path: str | Path) -> CaseInput:
    if str(path) == "-":
        payload = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        source = "stdin"
    else:
        source_path = Path(path)
        if not source_path.is_file() or source_path.is_symlink():
            raise IntakeError(f"request must be a regular file: {source_path}")
        payload = source_path.read_bytes()
        source = str(source_path)
    if len(payload) > _MAX_INPUT_BYTES:
        raise IntakeError(f"request exceeds {_MAX_INPUT_BYTES} bytes: {source}")
    try:
        return CaseInput.model_validate_json(payload)
    except ValidationError as exc:
        raise IntakeError(f"invalid MergeProof request in {source}: {exc}") from exc


def save_case(case: CaseInput, path: Path) -> None:
    write_json(path, case.model_dump(mode="json"))


def _git(repo: Path, *args: str, text: bool = False) -> bytes | str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise IntakeError(f"git {' '.join(args[:2])} timed out after 30 seconds") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise IntakeError(f"git {' '.join(args)} failed: {detail}")
    if text:
        return completed.stdout.decode("utf-8", errors="strict")
    return completed.stdout


def _git_root(repo: Path) -> Path:
    requested = repo.resolve()
    root = Path(str(_git(requested, "rev-parse", "--show-toplevel", text=True)).strip()).resolve()
    if root != requested:
        raise IntakeError(
            f"repository argument must be the Git worktree root; received {requested}, root is {root}"
        )
    return root


def _validate_base_ref(value: str) -> str:
    if (
        not value
        or len(value) > 1_000
        or value.startswith("-")
        or "\x00" in value
        or any(character.isspace() for character in value)
    ):
        raise IntakeError(f"unsafe Git base ref: {value!r}")
    return value


def _control_prefixes(root: Path, paths: list[Path] | None) -> list[str]:
    prefixes: set[str] = set()
    for path in paths or []:
        resolved = path.resolve(strict=False)
        if resolved == root or not resolved.is_relative_to(root):
            continue
        prefixes.add(resolved.relative_to(root).as_posix())
    return sorted(prefixes)


def _is_excluded(path: str, prefixes: list[str]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _nul_paths(payload: bytes) -> list[str]:
    paths: list[str] = []
    for item in payload.split(b"\x00"):
        if not item:
            continue
        try:
            value = item.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IntakeError("Git returned a non-UTF-8 path") from exc
        parsed = PurePosixPath(value)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in value:
            raise IntakeError(f"unsafe Git path: {value}")
        paths.append(value)
    return sorted(set(paths))


def _decode_text(path: str, payload: bytes, *, changed: bool) -> str | None:
    if len(payload) > _MAX_FILE_BYTES:
        if changed:
            raise IntakeError(f"changed file exceeds the 1 MB request limit: {path}")
        return None
    if b"\x00" in payload:
        if changed:
            raise IntakeError(f"changed binary file requires an explicit request: {path}")
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        if changed:
            raise IntakeError(
                f"changed non-UTF-8 file requires an explicit request: {path}"
            ) from exc
        return None


def _tree_at_ref(
    repo: Path,
    ref: str,
    changed_paths: set[str],
    excluded_prefixes: list[str],
) -> tuple[dict[str, str], list[str]]:
    raw_files = _git(repo, "ls-tree", "-r", "-z", "--name-only", ref)
    assert isinstance(raw_files, bytes)
    files = _nul_paths(raw_files)
    if len(files) > 10_000:
        raise IntakeError(
            "repository contains more than 10,000 paths; create a focused request JSON"
        )
    tree: dict[str, str] = {}
    omitted: list[str] = []
    total = 0
    for path in files:
        if _is_excluded(path, excluded_prefixes):
            continue
        payload = _git(repo, "show", f"{ref}:{path}")
        assert isinstance(payload, bytes)
        content = _decode_text(path, payload, changed=path in changed_paths)
        if content is None:
            omitted.append(path)
            continue
        total += len(payload)
        if len(tree) >= _MAX_FILES or total > _MAX_TOTAL_TREE_BYTES:
            raise IntakeError(
                "repository text snapshot exceeds the bounded request limits; "
                "create a focused request JSON or reduce the repository scope"
            )
        tree[path] = content
    return tree, omitted


def _worktree(
    repo: Path,
    changed_paths: set[str],
    excluded_prefixes: list[str],
) -> tuple[dict[str, str], list[str]]:
    raw_files = _git(repo, "ls-files", "-z", "-c", "-o", "--exclude-standard")
    assert isinstance(raw_files, bytes)
    files = _nul_paths(raw_files)
    if len(files) > 10_000:
        raise IntakeError(
            "repository contains more than 10,000 paths; create a focused request JSON"
        )
    tree: dict[str, str] = {}
    omitted: list[str] = []
    total = 0
    for path in files:
        if _is_excluded(path, excluded_prefixes):
            continue
        target = repo / path
        if not target.exists():
            continue
        if target.is_symlink():
            if path in changed_paths:
                raise IntakeError(f"changed symlink requires an explicit request: {path}")
            omitted.append(path)
            continue
        if not target.is_file():
            continue
        payload = target.read_bytes()
        content = _decode_text(path, payload, changed=path in changed_paths)
        if content is None:
            omitted.append(path)
            continue
        total += len(payload)
        if len(tree) >= _MAX_FILES or total > _MAX_TOTAL_TREE_BYTES:
            raise IntakeError(
                "repository text snapshot exceeds the bounded request limits; "
                "create a focused request JSON or reduce the repository scope"
            )
        tree[path] = content
    return tree, omitted


def _load_trajectory(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return [
            {
                "role": "human",
                "action": "checkpoint",
                "content": "A qualified human must approve before merge or deployment.",
            }
        ]
    if not path.is_file() or path.is_symlink():
        raise IntakeError(f"trajectory must be a regular JSON file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeError(f"invalid trajectory JSON: {exc}") from exc
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise IntakeError("trajectory JSON must be an array of objects")
    return [dict(item) for item in payload]


def _command_spec(raw: str, index: int, *, timeout_seconds: float, repeat: int) -> CommandSpec:
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        raise IntakeError(f"invalid verification command {index}: {exc}") from exc
    return CommandSpec(
        argv=argv,
        timeout_seconds=timeout_seconds,
        repeat=repeat,
        label=f"verification {index}",
    )


def prepare_case_from_git(
    *,
    repo: Path,
    base_ref: str,
    task: str,
    title: str | None = None,
    commands: list[str] | None = None,
    allowed_changed_globs: list[str] | None = None,
    trajectory_path: Path | None = None,
    exclude_paths: list[Path] | None = None,
    timeout_seconds: float = 15.0,
    repeat: int = 1,
) -> CaseInput:
    root = _git_root(repo)
    base_ref = _validate_base_ref(base_ref)
    base_commit = str(
        _git(
            root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{base_ref}^{{commit}}",
            text=True,
        )
    ).strip()
    changed_payload = _git(root, "diff", "--name-only", "-z", base_commit, "--")
    untracked_payload = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    assert isinstance(changed_payload, bytes) and isinstance(untracked_payload, bytes)
    excluded_prefixes = _control_prefixes(root, exclude_paths)
    changed = sorted(
        path
        for path in set(_nul_paths(changed_payload)) | set(_nul_paths(untracked_payload))
        if not _is_excluded(path, excluded_prefixes)
    )
    if len(changed) > _MAX_FILES:
        raise IntakeError(f"working tree changes exceed the {_MAX_FILES}-path request limit")
    if not changed:
        raise IntakeError(f"no working-tree changes relative to {base_ref}")
    changed_set = set(changed)

    before, omitted_before = _tree_at_ref(
        root,
        base_commit,
        changed_set,
        excluded_prefixes,
    )
    candidate, omitted_candidate = _worktree(root, changed_set, excluded_prefixes)
    missing_changed = sorted(
        path for path in changed if path not in before and path not in candidate
    )
    if missing_changed:
        raise IntakeError(f"changed paths could not be represented as text: {missing_changed}")

    raw_commands = commands or []
    command_specs = [
        _command_spec(raw, index, timeout_seconds=timeout_seconds, repeat=repeat)
        for index, raw in enumerate(raw_commands, start=1)
    ]
    allowed = allowed_changed_globs or changed
    request_identity = canonical_json(
        {
            "base_commit": base_commit,
            "changed_paths": changed,
            "repository": root.name,
            "task": task,
        }
    )
    case_id = f"git-{hashlib.sha256(request_identity.encode()).hexdigest()[:16]}"
    return CaseInput(
        id=case_id,
        title=title or f"Review {root.name} working tree",
        task=task,
        before=before,
        candidate=candidate,
        trajectory=_load_trajectory(trajectory_path),
        verification_commands=command_specs,
        allowed_changed_globs=allowed,
        metadata={
            "source": "git_worktree",
            "repository_name": root.name,
            "base_ref": base_ref,
            "base_commit": base_commit,
            "changed_paths": changed,
            "excluded_control_paths": excluded_prefixes,
            "omitted_unchanged_files": sorted(set(omitted_before) | set(omitted_candidate)),
            "verification_selection": "explicit" if commands else "none_fail_closed",
            "generated_by": "mergeproof prepare",
        },
    )
