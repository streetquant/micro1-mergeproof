SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

.PHONY: setup doctor format format-check lint typecheck test smoke replay schemas build check reproduce

setup:
	uv sync --locked --extra dev --extra dbt

doctor:
	uv run mergeproof doctor --json

format:
	uv run ruff format src tests scripts

format-check:
	uv run ruff format --check src tests scripts

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src/driftproof src/mergeproof scripts/verify_replay.py

test:
	uv run pytest -q

smoke:
	uv run pytest -q tests/test_cli.py tests/test_intake.py tests/test_reporting.py tests/test_sandbox.py

replay:
	uv run python scripts/verify_replay.py

schemas:
	uv run mergeproof schemas

build:
	uv build

check: format-check lint typecheck test replay build

reproduce:
	bash scripts/reproduce.sh
