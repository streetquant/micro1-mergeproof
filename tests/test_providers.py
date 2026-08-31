from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mergeproof.models import ModelUsage
from mergeproof.providers import (
    GeminiProvider,
    LLMProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ReplayProvider,
    _parse_duration_seconds,
)
from mergeproof.utils import stable_request_hash, write_json


def test_replay_provider_requires_exact_request_hash(tmp_path: Path) -> None:
    agent = "reviewer"
    model = "fixture-model"
    system = "system"
    user = "user"
    request_hash = stable_request_hash(agent, model, system, user)
    usage = ModelUsage(
        provider="gemini",
        model=model,
        agent=agent,
        request_hash=request_hash,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=123,
    )
    data = {"decision": "approve", "summary": "ok", "confidence": 1, "findings": []}
    write_json(
        tmp_path / f"{request_hash}.json",
        {
            "schema_version": 1,
            "request_hash": request_hash,
            "provider": "gemini",
            "model": model,
            "agent": agent,
            "request": {
                "system_sha256": stable_request_hash("system", model, system, ""),
                "user_sha256": stable_request_hash("user", model, "", user),
            },
            "response": {
                "data": data,
                "raw_text": json.dumps(data),
                "usage": usage.model_dump(mode="json"),
            },
        },
    )
    provider = ReplayProvider(model=model, replay_dir=tmp_path)
    response = provider.complete_json(agent=agent, system=system, user=user)
    assert response.data == data
    assert response.usage.provider == "replay"
    assert response.usage.latency_ms == 0


def test_replay_provider_rejects_tampered_identity(tmp_path: Path) -> None:
    agent = "reviewer"
    model = "fixture-model"
    system = "system"
    user = "user"
    request_hash = stable_request_hash(agent, model, system, user)
    usage = ModelUsage(
        provider="gemini",
        model=model,
        agent=agent,
        request_hash=request_hash,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "request_hash": request_hash,
        "provider": "gemini",
        "model": model,
        "agent": agent,
        "request": {
            "system_sha256": stable_request_hash("system", model, system, ""),
            "user_sha256": stable_request_hash("user", model, "", user),
        },
        "response": {
            "data": {"decision": "approve"},
            "raw_text": '{"decision":"approve"}',
            "usage": usage.model_dump(mode="json"),
        },
    }
    fixture = tmp_path / f"{request_hash}.json"

    payload["agent"] = "different-agent"
    write_json(fixture, payload)
    with pytest.raises(ProviderError, match="agent role"):
        ReplayProvider(model=model, replay_dir=tmp_path).complete_json(
            agent=agent, system=system, user=user
        )

    payload["agent"] = agent
    request = payload["request"]
    assert isinstance(request, dict)
    request["system_sha256"] = "0" * 64
    write_json(fixture, payload)
    with pytest.raises(ProviderError, match="system-prompt"):
        ReplayProvider(model=model, replay_dir=tmp_path).complete_json(
            agent=agent, system=system, user=user
        )

    request["system_sha256"] = stable_request_hash("system", model, system, "")
    response_payload = payload["response"]
    assert isinstance(response_payload, dict)
    usage_payload = response_payload["usage"]
    assert isinstance(usage_payload, dict)
    usage_payload["request_hash"] = "f" * 64
    write_json(fixture, payload)
    with pytest.raises(ProviderError, match="usage request hash"):
        ReplayProvider(model=model, replay_dir=tmp_path).complete_json(
            agent=agent, system=system, user=user
        )


class CredentialErrorProvider(LLMProvider):
    def __init__(self, fake_key: str) -> None:
        super().__init__(model="credential-error-model")
        self.fake_key = fake_key

    @property
    def name(self) -> str:
        return "credential-error"

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        request = httpx.Request(
            "GET",
            f"https://example.invalid/generate?key={self.fake_key}",
        )
        raise httpx.RequestError(
            f"request denied for credential {self.fake_key}",
            request=request,
        )


def test_provider_error_redacts_credential_shaped_text() -> None:
    fake_key = "AI" + "za" + ("A" * 32)
    provider = CredentialErrorProvider(fake_key)

    with pytest.raises(ProviderError) as captured:
        provider.complete_json(agent="reviewer", system="system", user="user")

    message = str(captured.value)
    assert fake_key not in message
    assert "[REDACTED_SECRET]" in message


class FakeGeminiResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        data = {
            "decision": "approve",
            "summary": "fixture",
            "confidence": 1,
            "findings": [],
        }
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(data)}],
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 5,
                "totalTokenCount": 12,
            },
        }


class CapturingGeminiClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> CapturingGeminiClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> FakeGeminiResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeGeminiResponse()


def test_gemini_uses_header_only_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_key = "test-key-that-is-not-a-credential"
    client = CapturingGeminiClient()
    monkeypatch.setattr(httpx, "Client", lambda **_: client)

    provider = GeminiProvider(model="fixture-model", api_key=fake_key)
    response = provider.complete_json(agent="reviewer", system="system", user="user")

    assert response.data["decision"] == "approve"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert "key=" not in call["url"]
    assert fake_key not in call["url"]
    assert call["headers"] == {"x-goog-api-key": fake_key}


def test_parse_provider_reset_durations() -> None:
    assert _parse_duration_seconds("9.84s") == pytest.approx(9.84)
    assert _parse_duration_seconds("1m31.2s") == pytest.approx(91.2)
    assert _parse_duration_seconds("250ms") == pytest.approx(0.25)
    assert _parse_duration_seconds("2.5") == pytest.approx(2.5)
    assert _parse_duration_seconds("nonsense") is None


class SequenceClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls = 0

    def __enter__(self) -> SequenceClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> httpx.Response:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def test_openai_compatible_provider_retries_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    limited = httpx.Response(
        429,
        headers={"x-ratelimit-reset-tokens": "1.5s"},
        request=request,
    )
    data = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "decision": "approve",
                            "summary": "fixture",
                            "confidence": 1,
                            "findings": [],
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    success = httpx.Response(200, json=data, request=request)
    client = SequenceClient([limited, success])
    waits: list[float] = []
    monkeypatch.setattr(httpx, "Client", lambda **_: client)
    monkeypatch.setattr("mergeproof.providers.time.sleep", waits.append)

    provider = OpenAICompatibleProvider(
        provider_name="fixture",
        model="fixture-model",
        base_url="https://example.invalid",
        api_key="not-a-credential",
        max_attempts=2,
    )
    response = provider.complete_json(agent="reviewer", system="system", user="user")

    assert response.data["decision"] == "approve"
    assert client.calls == 2
    assert waits == [pytest.approx(2.0)]
    assert response.usage.http_attempts == 2
    assert response.usage.rate_limit_wait_ms == 2000


def test_openai_compatible_provider_paces_sequential_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAICompatibleProvider(
        provider_name="fixture",
        model="fixture-model",
        base_url="https://example.invalid",
        api_key="not-a-credential",
        minimum_interval_seconds=12,
    )
    clock = iter([100.0, 105.0, 112.0])
    waits: list[float] = []
    monkeypatch.setattr("mergeproof.providers.time.perf_counter", lambda: next(clock))
    monkeypatch.setattr("mergeproof.providers.time.sleep", waits.append)

    assert provider._pace() == 0
    assert provider._pace() == 7000
    assert waits == [pytest.approx(7.0)]


def test_missing_replay_fixture_does_not_expose_the_host_directory(tmp_path: Path) -> None:
    replay_dir = tmp_path / "private" / "replay"
    provider = ReplayProvider(model="fixture-model", replay_dir=replay_dir)
    request_hash = stable_request_hash("reviewer", "fixture-model", "system", "user")

    with pytest.raises(ProviderError) as exc_info:
        provider.complete_json(agent="reviewer", system="system", user="user")

    message = str(exc_info.value)
    assert request_hash in message
    assert f"{request_hash}.json" in message
    assert str(tmp_path) not in message
