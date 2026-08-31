SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

.PHONY: setup doctor format format-check lint typecheck test smoke replay schemas schema-export schema-check protocol-smoke build check reproduce release

setup:
	uv sync --locked --extra dev --extra dbt

doctor:
	uv run mergeproof doctor --json
	uv run driftproof doctor --json

format:
	uv run ruff format src tests scripts

format-check:
	uv run ruff format --check src tests scripts

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src/driftproof src/mergeproof scripts/verify_replay.py scripts/package_final_release.py scripts/export_schemas.py

test:
	uv run pytest -q

smoke:
	uv run pytest -q tests/test_cli.py tests/test_driftproof_cli.py tests/test_driftproof_protocol.py tests/test_intake.py tests/test_reporting.py tests/test_driftproof_reporting.py tests/test_sandbox.py

replay:
	uv run python scripts/verify_replay.py

schemas: schema-export

schema-export:
	uv run python scripts/export_schemas.py

schema-check:
	uv run python scripts/export_schemas.py --check

protocol-smoke:
	uv run mergeproof capabilities >/dev/null
	uv run mergeproof schema agent-response >/dev/null
	uv run driftproof capabilities >/dev/null
	uv run driftproof schema request >/dev/null
	uv run driftproof schema agent-response >/dev/null

build:
	uv build

check: format-check lint typecheck schema-check protocol-smoke test replay build

reproduce:
	bash scripts/reproduce.sh

release:
	uv run python scripts/package_final_release.py --output release/final
