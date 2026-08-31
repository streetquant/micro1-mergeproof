from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from scripts.package_final_release import verify_release_directory  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a downloaded DriftProof release directory and all three archives."
    )
    parser.add_argument("directory", nargs="?", type=Path, default=Path("release/final"))
    args = parser.parse_args()
    result = verify_release_directory(args.directory.expanduser().resolve())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
