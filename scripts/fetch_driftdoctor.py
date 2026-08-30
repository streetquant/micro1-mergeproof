from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "upstream" / "driftdoctor.lock.json"
DEFAULT_DESTINATION = ROOT / ".cache" / "driftdoctor-upstream"


class UpstreamVerificationError(RuntimeError):
    """Raised when the fetched repository does not match the immutable lock."""


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    binary: bool = False,
) -> str | bytes:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace") if binary else completed.stderr
        raise UpstreamVerificationError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{stderr[-4000:]}"
        )
    return completed.stdout


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "repository",
        "commit",
        "tree",
        "archive_sha256",
        "license_sha256",
        "requirements_sha256",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise UpstreamVerificationError(f"upstream lock is missing fields: {missing}")
    return payload


def fetch_and_verify(destination: Path, *, reset: bool = False) -> dict[str, Any]:
    lock = load_lock()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not destination.exists():
        _run(["git", "init", "--quiet", str(destination)])
        _run(["git", "remote", "add", "origin", str(lock["repository"])], cwd=destination)
    elif not (destination / ".git").is_dir():
        raise UpstreamVerificationError(
            f"destination exists but is not a git repository: {destination}"
        )

    origin = str(_run(["git", "remote", "get-url", "origin"], cwd=destination)).strip()
    if origin.rstrip("/").removesuffix(".git") != str(lock["repository"]).rstrip("/").removesuffix(
        ".git"
    ):
        raise UpstreamVerificationError(
            f"origin mismatch: expected {lock['repository']!r}, observed {origin!r}"
        )

    if reset:
        _run(["git", "reset", "--hard", "HEAD"], cwd=destination)
        _run(["git", "clean", "-ffd", "--exclude=.venv/"], cwd=destination)

    _run(
        [
            "git",
            "fetch",
            "--quiet",
            "--filter=blob:none",
            "--depth=1",
            "origin",
            str(lock["commit"]),
        ],
        cwd=destination,
    )
    _run(
        ["git", "checkout", "--quiet", "--detach", "--force", str(lock["commit"])], cwd=destination
    )

    observed_commit = str(_run(["git", "rev-parse", "HEAD"], cwd=destination)).strip()
    observed_tree = str(_run(["git", "rev-parse", "HEAD^{tree}"], cwd=destination)).strip()
    archive = _run(["git", "archive", "--format=tar", "HEAD"], cwd=destination, binary=True)
    assert isinstance(archive, bytes)

    observed = {
        "commit": observed_commit,
        "tree": observed_tree,
        "archive_sha256": _sha256_bytes(archive),
        "license_sha256": _sha256_file(destination / "LICENSE"),
        "requirements_sha256": _sha256_file(destination / "requirements.txt"),
    }
    expected = {key: str(lock[key]) for key in observed}
    mismatches = {
        key: {"expected": expected[key], "observed": observed[key]}
        for key in observed
        if observed[key] != expected[key]
    }
    if mismatches:
        raise UpstreamVerificationError(
            "fetched DriftDoctor does not match the immutable lock: "
            + json.dumps(mismatches, sort_keys=True)
        )

    return {
        "schema_version": 1,
        "verified": True,
        "destination": str(destination),
        "repository": lock["repository"],
        **observed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and cryptographically verify the pinned DriftDoctor upstream."
    )
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Discard tracked modifications before verification; preserve an untracked .venv.",
    )
    args = parser.parse_args()
    result = fetch_and_verify(args.destination, reset=args.reset)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
