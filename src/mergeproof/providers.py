from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from .models import ModelUsage, ProviderResponse
from .utils import extract_json_object, redact_secrets, stable_request_hash, write_json

_DURATION_COMPONENT = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|m)")


def _parse_duration_seconds(value: str) -> float | None:
    stripped = value.strip().lower()
    try:
        return max(0.0, float(stripped))
    except ValueError:
        pass
    matches = list(_DURATION_COMPONENT.finditer(stripped))
    if not matches or "".join(match.group(0) for match in matches) != stripped:
        return None
    factors = {"ms": 0.001, "s": 1.0, "m": 60.0}
    return sum(float(match.group("value")) * factors[match.group("unit")] for match in matches)


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    candidates: list[float] = []
    for header in ("retry-after", "x-ratelimit-reset-tokens"):
        raw = response.headers.get(header)
        if raw is None:
            continue
        parsed = _parse_duration_seconds(raw)
        if parsed is not None:
            candidates.append(parsed)
    delay = max(candidates, default=min(2.0**attempt, 30.0))
    return min(max(delay + 0.5, 0.5), 60.0)


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
            if len(raw_text) > 2_000_000:
                raise ValueError("provider response exceeded the 2 MB safety bound")
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
            http_attempts=max(1, int(token_usage.get("http_attempts", 1))),
            rate_limit_wait_ms=max(0, int(token_usage.get("rate_limit_wait_ms", 0))),
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
        minimum_interval_seconds: float = 0,
        max_attempts: int = 4,
    ) -> None:
        super().__init__(model=model, record_dir=record_dir)
        self._name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self.max_attempts = max(1, max_attempts)
        self._last_request_started_at: float | None = None

    @property
    def name(self) -> str:
        return self._name

    def _pace(self) -> int:
        waited = 0.0
        if self._last_request_started_at is not None:
            elapsed = time.perf_counter() - self._last_request_started_at
            waited = max(0.0, self.minimum_interval_seconds - elapsed)
            if waited:
                time.sleep(waited)
        self._last_request_started_at = time.perf_counter()
        return round(waited * 1000)

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
        attempts = 0
        rate_limit_wait_ms = 0
        with httpx.Client(timeout=self.timeout_seconds) as client:
            while True:
                rate_limit_wait_ms += self._pace()
                attempts += 1
                response = client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempts >= self.max_attempts:
                    response.raise_for_status()
                    body = response.json()
                    break
                delay = _retry_delay_seconds(response, attempts - 1)
                time.sleep(delay)
                rate_limit_wait_ms += round(delay * 1000)
        raw_text = str(body["choices"][0]["message"]["content"])
        usage = body.get("usage", {})
        return raw_text, {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
            "http_attempts": attempts,
            "rate_limit_wait_ms": rate_limit_wait_ms,
        }


class ReplayProvider(LLMProvider):
    name = "replay"

    def __init__(self, *, model: str, replay_dir: Path) -> None:
        super().__init__(model=model, record_dir=None)
        self.replay_dir = replay_dir

    def complete_json(self, *, agent: str, system: str, user: str) -> ProviderResponse:
        request_hash = stable_request_hash(agent, self.model, system, user)
        path = self.replay_dir / f"{request_hash}.json"
        if not path.is_file() or path.is_symlink():
            raise ProviderError(f"missing replay fixture for request {request_hash}: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("fixture root must be an object")
            if payload.get("request_hash") != request_hash:
                raise ValueError("fixture request hash does not match the request")
            if payload.get("model") != self.model:
                raise ValueError("fixture model does not match the configured model")
            if payload.get("agent") != agent:
                raise ValueError("fixture agent role does not match the request")
            request = payload["request"]
            if not isinstance(request, dict):
                raise ValueError("fixture request metadata must be an object")
            if request.get("system_sha256") != stable_request_hash(
                "system", self.model, system, ""
            ):
                raise ValueError("fixture system-prompt identity mismatch")
            if request.get("user_sha256") != stable_request_hash("user", self.model, "", user):
                raise ValueError("fixture user-prompt identity mismatch")
            response = payload["response"]
            if not isinstance(response, dict):
                raise ValueError("fixture response must be an object")
            usage = ModelUsage.model_validate(response["usage"])
            if usage.request_hash != request_hash:
                raise ValueError("fixture usage request hash mismatch")
            data = response["data"]
            if not isinstance(data, dict):
                raise ValueError("fixture response data must be an object")
            raw_text = str(response["raw_text"])
            if len(raw_text) > 2_000_000:
                raise ValueError("replay response exceeded the 2 MB safety bound")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"invalid replay fixture {path.name}: {redact_secrets(str(exc))}"
            ) from exc
        usage = usage.model_copy(update={"provider": self.name, "latency_ms": 0})
        return ProviderResponse(data=dict(data), raw_text=raw_text, usage=usage)

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
            minimum_interval_seconds=12,
            max_attempts=6,
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
