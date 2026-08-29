from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mergeproof.models import ModelUsage
from mergeproof.providers import GeminiProvider, LLMProvider, ProviderError, ReplayProvider
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
            "response": {
                "data": data,
                "raw_text": json.dumps(data),
                "usage": usage.model_dump(mode="json"),
            }
        },
    )
    provider = ReplayProvider(model=model, replay_dir=tmp_path)
    response = provider.complete_json(agent=agent, system=system, user=user)
    assert response.data == data
    assert response.usage.provider == "replay"
    assert response.usage.latency_ms == 0


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
