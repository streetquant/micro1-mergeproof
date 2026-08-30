from __future__ import annotations

import csv
import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from mergeproof.utils import sha256_text

_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", flags=re.DOTALL)
_REF = re.compile(r"ref\s*\(\s*(['\"])(?P<name>[^'\"]+)\1\s*\)")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProjectValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SelectItem:
    path: str
    expression: str
    output: str


@dataclass(frozen=True)
class ProjectSnapshot:
    root: Path
    tree_sha256: str
    sql_files: dict[str, str]
    yaml_files: dict[str, str]
    csv_headers: set[str]
    model_names: set[str]
    refs: set[str]
    select_items: list[SelectItem] = field(default_factory=list)

    @property
    def sql_text(self) -> str:
        return "\n".join(self.sql_files.values())

    @property
    def yaml_text(self) -> str:
        return "\n".join(self.yaml_files.values())

    def expressions_for(self, output: str) -> list[SelectItem]:
        lowered = output.lower()
        return [item for item in self.select_items if item.output.lower() == lowered]


def _source_tree_sha256(root: Path) -> str:
    ignored = {
        ".git",
        ".venv",
        ".user.yml",
        "logs",
        "target",
        "dbt_packages",
        "__pycache__",
    }
    records: list[bytes] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(root)
        if ignored.intersection(relative_path.parts) or path.suffix in {".pyc", ".duckdb"}:
            continue
        relative = relative_path.as_posix().encode()
        digest = hashlib.sha256(path.read_bytes()).digest()
        records.extend((relative, b"\0", digest, b"\n"))
    return hashlib.sha256(b"".join(records)).hexdigest()


def _split_select_list(sql: str) -> list[str]:
    clean = _SQL_COMMENT.sub("", sql)
    match = re.search(r"\bselect\b", clean, flags=re.IGNORECASE)
    if match is None:
        return []
    start = match.end()
    depth = 0
    quote: str | None = None
    items: list[str] = []
    current: list[str] = []
    index = start
    while index < len(clean):
        char = clean[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                if index + 1 < len(clean) and clean[index + 1] == quote:
                    current.append(clean[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if depth == 0 and clean[index : index + 4].lower() == "from":
            before = clean[index - 1] if index > 0 else " "
            after = clean[index + 4] if index + 4 < len(clean) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                break
        if depth == 0 and char == ",":
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(char)
        index += 1
    final = "".join(current).strip()
    if final:
        items.append(final)
    return items


def _parse_select_items(path: str, sql: str) -> list[SelectItem]:
    parsed: list[SelectItem] = []
    for raw in _split_select_list(sql):
        item = re.sub(r"\s+", " ", raw).strip()
        alias_match = re.search(r"\s+as\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", item, flags=re.IGNORECASE)
        if alias_match:
            output = alias_match.group(1)
            expression = item[: alias_match.start()].strip()
            parsed.append(SelectItem(path=path, expression=expression, output=output))
            continue
        simple = item.split(".")[-1].strip()
        if _IDENTIFIER.fullmatch(simple):
            parsed.append(SelectItem(path=path, expression=item, output=simple))
    return parsed


def _validate_project(root: Path) -> None:
    if not root.is_dir():
        raise ProjectValidationError(f"project root does not exist: {root}")
    for required in ("dbt_project.yml", "profiles.yml"):
        if not (root / required).is_file():
            raise ProjectValidationError(f"missing required project file: {required}")
    symlinks = [str(path.relative_to(root)) for path in root.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ProjectValidationError(f"symlinks are not allowed in reviewed projects: {symlinks}")

    profile = (root / "profiles.yml").read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?im)^\s*type\s*:\s*duckdb\s*$", profile) is None:
        raise ProjectValidationError("only a project-local DuckDB profile is allowed")
    forbidden_profile = re.compile(
        r"(?i)\b(http|https|s3|gcs|azure|motherduck|attach|extension|external_access)\b"
    )
    if forbidden_profile.search(profile):
        raise ProjectValidationError("profile requests a remote or extension capability")
    for raw_path in re.findall(r"(?im)^\s*path\s*:\s*['\"]?([^'\"\n#]+)", profile):
        value = raw_path.strip()
        if value == ":memory:":
            continue
        candidate = Path(value)
        lower_value = value.lower()
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or value.startswith("~")
            or lower_value.startswith(("md:", "motherduck:", "http:", "https:"))
            or "://" in lower_value
        ):
            raise ProjectValidationError(f"DuckDB path must be project-relative: {value}")

    project_config = (root / "dbt_project.yml").read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?im)^\s*(on-run-start|on-run-end)\s*:", project_config):
        raise ProjectValidationError("dbt lifecycle hooks are not allowed in the review sandbox")
    if list(root.glob("models/**/*.py")):
        raise ProjectValidationError("Python dbt models are outside the verified execution profile")


def snapshot_project(root: Path) -> ProjectSnapshot:
    root = root.resolve()
    _validate_project(root)
    sql_files: dict[str, str] = {}
    yaml_files: dict[str, str] = {}
    select_items: list[SelectItem] = []
    refs: set[str] = set()

    for path in sorted(root.glob("models/**/*.sql")) + sorted(root.glob("macros/**/*.sql")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        sql_files[relative] = text
        select_items.extend(_parse_select_items(relative, text))
        refs.update(match.group("name") for match in _REF.finditer(text))

    for pattern in ("models/**/*.yml", "models/**/*.yaml"):
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                yaml_files[path.relative_to(root).as_posix()] = path.read_text(
                    encoding="utf-8", errors="replace"
                )

    headers: set[str] = set()
    for path in sorted((root / "input").glob("*.csv")):
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            with suppress(StopIteration):
                headers.update(value.strip() for value in next(reader) if value.strip())

    model_names = {path.stem for path in root.glob("models/**/*.sql") if path.is_file()}
    return ProjectSnapshot(
        root=root,
        tree_sha256=_source_tree_sha256(root),
        sql_files=sql_files,
        yaml_files=yaml_files,
        csv_headers=headers,
        model_names=model_names,
        refs=refs,
        select_items=select_items,
    )


def snapshot_identity(snapshot: ProjectSnapshot) -> str:
    return sha256_text(
        "\n".join([snapshot.tree_sha256, *sorted(snapshot.sql_files), *sorted(snapshot.yaml_files)])
    )
