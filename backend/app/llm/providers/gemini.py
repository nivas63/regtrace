"""Google Gemini adapter (Generative Language REST API, free tier via AI Studio).

Uses the REST API over the shared httpx client rather than the Google SDK: one HTTP
stack for every provider, a single timeout/retry policy, and easy HTTP-level tests.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from pydantic import SecretStr

from app.llm.errors import LLMPermanentError
from app.llm.json_tools import schema_instruction
from app.llm.providers._http import get_json, post_json
from app.llm.types import FinishReason, LLMRequest, ModelTier, ProviderResult, TokenUsage

_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
_FINISH_MAP = {"STOP": FinishReason.STOP, "MAX_TOKENS": FinishReason.LENGTH}


class GeminiProvider:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        base_url: str,
        reasoning_model: str,
        fast_model: str,
        timeout_s: float,
        native_json_schema: bool = True,
        thinking_budget: int | None = None,
        min_output_tokens: int = 0,
        name: str = "gemini",
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._models = {ModelTier.REASONING: reasoning_model, ModelTier.FAST: fast_model}
        self._timeout_s = timeout_s
        self._native_json_schema = native_json_schema
        self._thinking_budget = thinking_budget
        self._min_output_tokens = min_output_tokens
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def model_for(self, tier: ModelTier) -> str:
        return self._models[tier]

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key.get_secret_value()}

    def build_body(self, request: LLMRequest) -> dict[str, Any]:
        system = request.system
        generation: dict[str, Any] = {
            "temperature": request.temperature,
            # Thinking tokens count against this budget; give thinking models headroom.
            "maxOutputTokens": max(request.max_output_tokens, self._min_output_tokens),
        }
        if request.json_schema is not None:
            generation["responseMimeType"] = "application/json"
            if self._native_json_schema:
                generation["responseJsonSchema"] = gemini_schema(request.json_schema)
            else:
                system = f"{system}\n\n{schema_instruction(request.json_schema)}"
        if self._thinking_budget is not None:
            generation["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}

        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [{"text": m.content}],
                }
                for m in request.messages
            ],
            "generationConfig": generation,
        }

    async def generate(self, request: LLMRequest, model: str) -> ProviderResult:
        payload = await post_json(
            self._client,
            provider=self.name,
            url=f"{self._base_url}/models/{model}:generateContent",
            headers=self._headers(),
            body=self.build_body(request),
            timeout_s=self._timeout_s,
            retry_after_from_body=_retry_delay_from_error,
        )
        return self._parse(payload, model)

    def _parse(self, payload: dict[str, Any], model: str) -> ProviderResult:
        candidates = payload.get("candidates") or []
        if not candidates:
            reason = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMPermanentError(f"gemini returned no output ({reason})", provider=self.name)

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        # 2.5 models may return thought-summary parts; only keep the answer text.
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        raw_finish = candidate.get("finishReason", "STOP")
        if not text and raw_finish not in _FINISH_MAP:
            raise LLMPermanentError(f"gemini produced no text (finishReason={raw_finish})", provider=self.name)

        meta = payload.get("usageMetadata") or {}
        usage = TokenUsage(
            input_tokens=int(meta.get("promptTokenCount", 0)),
            # Thinking tokens count against output quota, so account for them.
            output_tokens=int(meta.get("candidatesTokenCount", 0)) + int(meta.get("thoughtsTokenCount", 0)),
        )
        return ProviderResult(
            text=text,
            model=payload.get("modelVersion", model),
            usage=usage,
            finish_reason=_FINISH_MAP.get(raw_finish, FinishReason.OTHER),
        )

    async def list_models(self) -> list[str]:
        payload = await get_json(
            self._client,
            provider=self.name,
            url=f"{self._base_url}/models?pageSize=200",
            headers=self._headers(),
            timeout_s=self._timeout_s,
        )
        return sorted(
            m["name"].removeprefix("models/")
            for m in payload.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        )


def _retry_delay_from_error(payload: dict[str, Any]) -> float | None:
    """Gemini puts the cooldown in error.details[].retryDelay, e.g. "37s"."""
    for detail in (payload.get("error") or {}).get("details", []) or []:
        match = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


# Keywords the Gemini responseJsonSchema endpoint rejects (HTTP 400 INVALID_ARGUMENT).
# They are still enforced locally by Pydantic validation of the response.
_GEMINI_UNSUPPORTED = frozenset({"maxItems", "minItems"})


def gemini_schema(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: gemini_schema(v) for k, v in node.items() if k not in _GEMINI_UNSUPPORTED}
    if isinstance(node, list):
        return [gemini_schema(v) for v in node]
    return node
