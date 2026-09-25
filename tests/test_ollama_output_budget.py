import json
import httpx
import pytest

from src.services import ai
from src.services.ai import AIService, ProviderConfig


@pytest.mark.parametrize("think", [None, False, True, "low", "medium", "high", "max"])
async def test_ollama_thinking_override_preserves_output_budget(monkeypatch, think):
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"done_reason": "stop", "message": {"content": "{}"}})
    client = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(ai.settings, "ai_ollama_think", think)
    await AIService()._call_ollama(ProviderConfig("ollama", "test", "test-model", "https://example.com/api"), "extract", max_tokens=800)
    assert captured["options"]["num_predict"] == 800
    assert captured["format"] == "json"
    if think is None:
        assert "think" not in captured
    else:
        assert captured["think"] == think


async def test_ollama_truncation_is_explicit_even_if_content_looks_valid(monkeypatch):
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "done_reason": "length", "message": {"content": "{}"}, "eval_count": 800,
    }))
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    with pytest.raises(RuntimeError, match="truncated at 800 output tokens"):
        await AIService()._call_ollama(ProviderConfig("ollama", "test", "test-model", "https://example.com/api"), "extract")
