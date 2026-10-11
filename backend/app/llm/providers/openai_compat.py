"""Adapter for any OpenAI-compatible Chat Completions API.

One adapter serves both Groq (free cloud tier) and Ollama (local, unlimited),
because both expose `/chat/completions` with the same request/response shape.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

from app.llm.errors import LLMPermanentError
from app.llm.json_tools import schema_instruction
from app.llm.providers._http import get_json, post_json
from app.llm.types import FinishReason, LLMRequest, ModelTier, ProviderResult, TokenUsage

_FINISH_MAP = {"stop": FinishReason.STOP, "length": FinishReason.LENGTH}


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        client: httpx.AsyncClient,
        base_url: str,
        reasoning_model: str,
        fast_model: str,
        timeout_s: float,
        api_key: SecretStr | None = None,
    ) -> None:
        self._name = name
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._models = {ModelTier.REASONING: reasoning_model, ModelTier.FAST: fast_model}
        self._timeout_s = timeout_s
        self._api_key = api_key

    @property
    def name(self) -> str:
        return self._name

    def model_for(self, tier: ModelTier) -> str:
        return self._models[tier]

    def _headers(self) -> dict[str, str]:
        if self._api_key is None:
            return {}
        return {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

    def build_body(self, request: LLMRequest, model: str) -> dict[str, Any]:
        system = request.system
        body: dict[str, Any] = {
            "model": model,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_schema is not None:
            # json_object mode is the portable subset (Groq + Ollama); the schema
            # itself goes in the prompt and is enforced by Pydantic afterwards.
            body["response_format"] = {"type": "json_object"}
            system = f"{system}\n\n{schema_instruction(request.json_schema)}"
        body["messages"] = [
            {"role": "system", "content": system},
            *({"role": m.role, "content": m.content} for m in request.messages),
        ]
        return body

    async def generate(self, request: LLMRequest, model: str) -> ProviderResult:
        payload = await post_json(
            self._client,
            provider=self.name,
            url=f"{self._base_url}/chat/completions",
            headers=self._headers(),
            body=self.build_body(request, model),
            timeout_s=self._timeout_s,
        )
        choices = payload.get("choices") or []
        if not choices:
            raise LLMPermanentError(f"{self.name} returned no choices", provider=self.name)
        choice = choices[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = payload.get("usage") or {}
        return ProviderResult(
            text=text,
            model=payload.get("model", model),
            usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            finish_reason=_FINISH_MAP.get(choice.get("finish_reason", ""), FinishReason.OTHER),
        )

    async def list_models(self) -> list[str]:
        payload = await get_json(
            self._client,
            provider=self.name,
            url=f"{self._base_url}/models",
            headers=self._headers(),
            timeout_s=self._timeout_s,
        )
        return sorted(str(m["id"]) for m in payload.get("data", []))
