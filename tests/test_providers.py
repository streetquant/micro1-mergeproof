from __future__ import annotations

import json
from pathlib import Path

from mergeproof.models import ModelUsage
from mergeproof.providers import ReplayProvider
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
