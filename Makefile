SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

VIDEO_OUTPUT ?= release/video
MEDIA_DIRECTORY ?=
GIT_HEAD := $(shell git rev-parse HEAD)

.PHONY: setup doctor judge-demo submission submission-check video-doctor video video-verify format format-check lint typecheck test smoke replay schemas schema-export schema-check protocol-smoke build check reproduce release release-verify

setup:
	uv sync --locked --extra dev --extra dbt

doctor:
	uv run mergeproof doctor --json
	uv run driftproof doctor --json

judge-demo:
	bash scripts/judge_quickstart.sh

submission:
	uv run python scripts/render_submission.py

submission-check:
	uv run python scripts/render_submission.py --check

video-doctor:
	uv run python scripts/render_demo_video.py --check --expected-commit "$(GIT_HEAD)"

video:
	uv run python scripts/render_demo_video.py --output "$(VIDEO_OUTPUT)"

video-verify:
	uv run python scripts/verify_demo_video.py "$(VIDEO_OUTPUT)" --source-root . --expected-commit "$(GIT_HEAD)"

format:
	uv run ruff format src tests scripts

format-check:
	uv run ruff format --check src tests scripts

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src/driftproof src/mergeproof scripts/verify_replay.py scripts/package_final_release.py scripts/export_schemas.py scripts/judge_packet.py scripts/render_demo_video.py scripts/render_submission.py scripts/standalone_release_verifier.py scripts/verify_demo_video.py scripts/verify_release.py

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
	uv run driftproof schema demo-response >/dev/null
	uv run driftproof schema fingerprint-response >/dev/null
	uv run driftproof schema response-verification >/dev/null
	uv run driftproof schema agent-response >/dev/null
	uv run driftproof demo --help >/dev/null
	uv run driftproof verify-response --help >/dev/null
	uv run driftproof fingerprint examples/judge-demo-safe >/dev/null

build:
	uv build

check: format-check lint typecheck schema-check submission-check protocol-smoke test replay build

reproduce:
	bash scripts/reproduce.sh

release:
	@args=(--output release/final); \
	if [[ -n "$(MEDIA_DIRECTORY)" ]]; then args+=(--media-directory "$(MEDIA_DIRECTORY)"); fi; \
	uv run python scripts/package_final_release.py "${args[@]}"

release-verify:
	@uv run python scripts/verify_release.py release/final
