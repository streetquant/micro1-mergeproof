from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from .models import ModelUsage, ProviderResponse
from .utils import extract_json_object, redact_secrets, stable_request_hash, write_json


class ProviderError(RuntimeError):
    pass


class LLMProvider(ABC):
    def __init__(self, *, model: str, record_dir: Path | None = None) -> None:
        self.model = model
        self.record_dir = record_dir

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        raise NotImplementedError

    def complete_json(self, *, agent: str, system: str, user: str) -> ProviderResponse:
        request_hash = stable_request_hash(agent, self.model, system, user)
        started = time.perf_counter()
        try:
            raw_text, token_usage = self._request(system=system, user=user)
            data = extract_json_object(raw_text)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"{self.name} request failed: {redact_secrets(str(exc))}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        usage = ModelUsage(
            provider=self.name,
            model=self.model,
            agent=agent,
            request_hash=request_hash,
            input_tokens=int(token_usage.get("input_tokens", 0)),
            output_tokens=int(token_usage.get("output_tokens", 0)),
            total_tokens=int(token_usage.get("total_tokens", 0)),
            latency_ms=latency_ms,
        )
        response = ProviderResponse(data=data, raw_text=raw_text, usage=usage)
        if self.record_dir is not None:
            self._record(agent=agent, system=system, user=user, response=response)
        return response

    def _record(self, *, agent: str, system: str, user: str, response: ProviderResponse) -> None:
        assert self.record_dir is not None
        payload = {
            "schema_version": 1,
            "request_hash": response.usage.request_hash,
            "provider": self.name,
            "model": self.model,
            "agent": agent,
            "request": {
                "system_sha256": stable_request_hash("system", self.model, system, ""),
                "user_sha256": stable_request_hash("user", self.model, "", user),
                "system_preview": redact_secrets(system[:1000]),
                "user_preview": redact_secrets(user[:2000]),
            },
            "response": {
                "data": response.data,
                "raw_text": redact_secrets(response.raw_text),
                "usage": response.usage.model_dump(mode="json"),
            },
        }
        write_json(self.record_dir / f"{response.usage.request_hash}.json", payload)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        record_dir: Path | None = None,
        timeout_seconds: float = 90,
    ) -> None:
        super().__init__(model=model.removeprefix("models/"), record_dir=record_dir)
        resolved_api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not resolved_api_key:
            raise ProviderError("GEMINI_API_KEY is required for the Gemini provider")
        self.api_key: str = resolved_api_key
        self.timeout_seconds = timeout_seconds

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )
        headers = {"x-goog-api-key": self.api_key}
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "topP": 1,
                "responseMimeType": "application/json",
            },
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
        raw_text = "".join(
            str(part.get("text", "")) for part in body["candidates"][0]["content"]["parts"]
        )
        usage = body.get("usageMetadata", {})
        return raw_text, {
            "input_tokens": int(usage.get("promptTokenCount", 0)),
            "output_tokens": int(usage.get("candidatesTokenCount", 0)),
            "total_tokens": int(usage.get("totalTokenCount", 0)),
        }


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        model: str,
        base_url: str,
        api_key: str,
        record_dir: Path | None = None,
        timeout_seconds: float = 90,
    ) -> None:
        super().__init__(model=model, record_dir=record_dir)
        self._name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return self._name

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/streetquant/micro1-mergeproof"
            headers["X-Title"] = "MergeProof"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )
            response.raise_for_status()
            body = response.json()
        raw_text = str(body["choices"][0]["message"]["content"])
        usage = body.get("usage", {})
        return raw_text, {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }


class ReplayProvider(LLMProvider):
    name = "replay"

    def __init__(self, *, model: str, replay_dir: Path) -> None:
        super().__init__(model=model, record_dir=None)
        self.replay_dir = replay_dir
        self._pending_hash: str | None = None

    def complete_json(self, *, agent: str, system: str, user: str) -> ProviderResponse:
        request_hash = stable_request_hash(agent, self.model, system, user)
        path = self.replay_dir / f"{request_hash}.json"
        if not path.is_file():
            raise ProviderError(f"missing replay fixture: {path}")
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        response = payload["response"]
        usage = ModelUsage.model_validate(response["usage"])
        usage = usage.model_copy(update={"provider": self.name, "latency_ms": 0})
        return ProviderResponse(
            data=dict(response["data"]), raw_text=str(response["raw_text"]), usage=usage
        )

    def _request(self, *, system: str, user: str) -> tuple[str, dict[str, int]]:
        raise NotImplementedError


def build_provider(
    *,
    provider: str,
    model: str,
    record_dir: Path | None = None,
    replay_dir: Path | None = None,
) -> LLMProvider:
    if provider == "gemini":
        return GeminiProvider(model=model, record_dir=record_dir)
    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is required for the Groq provider")
        return OpenAICompatibleProvider(
            provider_name="groq",
            model=model,
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
            record_dir=record_dir,
        )
    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is required for the OpenRouter provider")
        return OpenAICompatibleProvider(
            provider_name="openrouter",
            model=model,
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            record_dir=record_dir,
        )
    if provider == "openai-compatible":
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        if not api_key or not base_url:
            raise ProviderError("OPENAI_API_KEY and OPENAI_BASE_URL are required")
        return OpenAICompatibleProvider(
            provider_name="openai-compatible",
            model=model,
            base_url=base_url,
            api_key=api_key,
            record_dir=record_dir,
        )
    if provider == "replay":
        if replay_dir is None:
            raise ProviderError("replay_dir is required for replay mode")
        return ReplayProvider(model=model, replay_dir=replay_dir)
    raise ProviderError(f"unsupported provider: {provider}")
