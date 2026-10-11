"""HTTP-level adapter tests (respx mocks the network; no real API calls)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.llm.errors import LLMPermanentError, LLMRateLimitedError, LLMTransientError
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.openai_compat import OpenAICompatibleProvider
from app.llm.types import FinishReason, LLMRequest, Message, ModelTier

GEMINI = "https://gemini.test/v1beta"
GROQ = "https://groq.test/openai/v1"
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}
REQUEST = LLMRequest(
    purpose="t",
    system="You are precise.",
    messages=[Message(role="user", content="clause text")],
    json_schema=SCHEMA,
)


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def _gemini(http: httpx.AsyncClient, native: bool = True) -> GeminiProvider:
    return GeminiProvider(
        client=http,
        api_key=SecretStr("g-key"),
        base_url=GEMINI,
        reasoning_model="gemini-2.5-flash",
        fast_model="gemini-2.5-flash-lite",
        timeout_s=5,
        native_json_schema=native,
    )


def _groq(http: httpx.AsyncClient) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="groq",
        client=http,
        base_url=GROQ,
        reasoning_model="openai/gpt-oss-120b",
        fast_model="openai/gpt-oss-20b",
        timeout_s=5,
        api_key=SecretStr("q-key"),
    )


@respx.mock
async def test_gemini_request_shape_and_parse(http: httpx.AsyncClient) -> None:
    route = respx.post(f"{GEMINI}/models/gemini-2.5-flash:generateContent").respond(
        json={
            "candidates": [
                {
                    "content": {"parts": [{"text": "thinking", "thought": True}, {"text": '{"a":"x"}'}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 4,
                "thoughtsTokenCount": 6,
            },
        }
    )
    provider = _gemini(http)
    result = await provider.generate(REQUEST, provider.model_for(ModelTier.REASONING))

    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["x-goog-api-key"] == "g-key"
    assert sent["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert sent["generationConfig"]["responseMimeType"] == "application/json"
    assert result.text == '{"a":"x"}'  # thought parts excluded
    assert result.usage.output_tokens == 10  # thinking tokens are accounted
    assert result.finish_reason is FinishReason.STOP


@respx.mock
async def test_gemini_prompt_schema_fallback(http: httpx.AsyncClient) -> None:
    route = respx.post(url__regex=r".*:generateContent").respond(
        json={"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}
    )
    await _gemini(http, native=False).generate(REQUEST, "gemini-2.5-flash")
    sent = json.loads(route.calls.last.request.content)
    assert "responseJsonSchema" not in sent["generationConfig"]
    assert "JSON Schema" in sent["systemInstruction"]["parts"][0]["text"]


@respx.mock
async def test_gemini_429_reads_retry_delay_from_body(http: httpx.AsyncClient) -> None:
    respx.post(url__regex=r".*:generateContent").respond(
        429,
        json={"error": {"code": 429, "details": [{"retryDelay": "37s"}]}},
    )
    with pytest.raises(LLMRateLimitedError) as info:
        await _gemini(http).generate(REQUEST, "gemini-2.5-flash")
    assert info.value.retry_after_s == 37


@respx.mock
async def test_gemini_blocked_prompt_is_permanent(http: httpx.AsyncClient) -> None:
    respx.post(url__regex=r".*:generateContent").respond(json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(LLMPermanentError, match="SAFETY"):
        await _gemini(http).generate(REQUEST, "gemini-2.5-flash")


@respx.mock
async def test_openai_compat_json_mode_and_parse(http: httpx.AsyncClient) -> None:
    route = respx.post(f"{GROQ}/chat/completions").respond(
        json={
            "model": "openai/gpt-oss-120b",
            "choices": [{"message": {"content": '{"a":"y"}'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        }
    )
    result = await _groq(http).generate(REQUEST, "openai/gpt-oss-120b")

    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["authorization"] == "Bearer q-key"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["role"] == "system" and "JSON" in sent["messages"][0]["content"]
    assert result.finish_reason is FinishReason.LENGTH
    assert result.usage.total == 28


@respx.mock
async def test_openai_compat_error_classification(http: httpx.AsyncClient) -> None:
    route = respx.post(f"{GROQ}/chat/completions")
    provider = _groq(http)

    route.respond(429, headers={"retry-after": "12"}, json={"error": {}})
    with pytest.raises(LLMRateLimitedError) as info:
        await provider.generate(REQUEST, "m")
    assert info.value.retry_after_s == 12

    route.respond(503, json={"error": {}})
    with pytest.raises(LLMTransientError):
        await provider.generate(REQUEST, "m")

    route.respond(401, json={"error": {"message": "bad key"}})
    with pytest.raises(LLMPermanentError):
        await provider.generate(REQUEST, "m")

    route.side_effect = httpx.ConnectError("refused")
    with pytest.raises(LLMTransientError):
        await provider.generate(REQUEST, "m")


@respx.mock
async def test_gemini_strips_unsupported_schema_keywords(http: httpx.AsyncClient) -> None:
    route = respx.post(url__regex=r".*:generateContent").respond(
        json={"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}
    )
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "maxItems": 5, "minItems": 1, "items": {"type": "string", "maxLength": 9}}
        },
    }
    request = REQUEST.model_copy(update={"json_schema": schema})
    await _gemini(http).generate(request, "gemini-3.5-flash")
    sent = json.loads(route.calls.last.request.content)["generationConfig"]["responseJsonSchema"]
    assert "maxItems" not in sent["properties"]["items"] and "minItems" not in sent["properties"]["items"]
    assert sent["properties"]["items"]["items"]["maxLength"] == 9
