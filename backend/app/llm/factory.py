"""Builds the LLM stack from Settings. The only place that knows concrete adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config import LLMProviderName, Settings
from app.llm.base import LLMProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.openai_compat import OpenAICompatibleProvider
from app.llm.rate_limiter import RateLimiter
from app.llm.router import LLMRouter, RouterPolicy
from app.llm.structured import StructuredLLM
from app.llm.types import ModelTier


@dataclass(frozen=True)
class LLMStack:
    router: LLMRouter
    structured: StructuredLLM


def _model_slots(primary: str, fallbacks: list[str]) -> list[str]:
    return list(dict.fromkeys([primary, *fallbacks]))


def build_provider_instances(
    settings: Settings, client: httpx.AsyncClient
) -> tuple[dict[str, LLMProvider], dict[ModelTier, list[str]]]:
    """Instantiate usable providers. A provider with several models becomes several
    router entries ("gemini", "gemini#2", ...) so quota exhaustion of one model falls
    through to the next. Returns the instances and the per-tier routing chains."""
    timeout = settings.llm_request_timeout_s
    families: dict[str, tuple[list[str], list[str], Callable[[str, str, str], LLMProvider]]] = {}
    if settings.gemini_api_key is not None:
        api_key = settings.gemini_api_key

        def gemini(name: str, reasoning: str, fast: str) -> LLMProvider:
            return GeminiProvider(
                client=client,
                api_key=api_key,
                base_url=settings.gemini_base_url,
                reasoning_model=reasoning,
                fast_model=fast,
                timeout_s=timeout,
                native_json_schema=settings.gemini_native_json_schema,
                thinking_budget=settings.gemini_thinking_budget,
                min_output_tokens=settings.gemini_min_output_tokens,
                name=name,
            )

        families[LLMProviderName.GEMINI] = (
            _model_slots(settings.gemini_reasoning_model, settings.gemini_reasoning_fallback_models),
            _model_slots(settings.gemini_fast_model, settings.gemini_fast_fallback_models),
            gemini,
        )
    if settings.groq_api_key is not None:
        groq_key = settings.groq_api_key

        def groq(name: str, reasoning: str, fast: str) -> LLMProvider:
            return OpenAICompatibleProvider(
                name=name,
                client=client,
                base_url=settings.groq_base_url,
                reasoning_model=reasoning,
                fast_model=fast,
                timeout_s=timeout,
                api_key=groq_key,
            )

        families[LLMProviderName.GROQ] = (
            _model_slots(settings.groq_reasoning_model, settings.groq_reasoning_fallback_models),
            _model_slots(settings.groq_fast_model, settings.groq_fast_fallback_models),
            groq,
        )
    if settings.ollama_enabled:

        def ollama(name: str, reasoning: str, fast: str) -> LLMProvider:
            # Local CPU inference is slow; allow much longer than cloud calls.
            return OpenAICompatibleProvider(
                name=name,
                client=client,
                base_url=settings.ollama_base_url,
                reasoning_model=reasoning,
                fast_model=fast,
                timeout_s=max(timeout, 300.0),
            )

        families[LLMProviderName.OLLAMA] = ([settings.ollama_reasoning_model], [settings.ollama_fast_model], ollama)

    providers: dict[str, LLMProvider] = {}
    instance_names: dict[str, list[str]] = {}
    for family, (reasoning, fast, make) in families.items():
        names = []
        for i in range(max(len(reasoning), len(fast))):
            name = family if i == 0 else f"{family}#{i + 1}"
            providers[name] = make(name, reasoning[min(i, len(reasoning) - 1)], fast[min(i, len(fast) - 1)])
            names.append(name)
        instance_names[family] = names

    def chain(order: list[LLMProviderName], tier: ModelTier) -> list[str]:
        out: list[str] = []
        for family in order:
            if family not in families:
                continue
            reasoning, fast, _ = families[family]
            slots = len(reasoning) if tier is ModelTier.REASONING else len(fast)
            out.extend(instance_names[family][:slots])
        return out

    chains = {
        ModelTier.REASONING: chain(settings.llm_reasoning_chain, ModelTier.REASONING),
        ModelTier.FAST: chain(settings.llm_fast_chain, ModelTier.FAST),
    }
    return providers, chains


def build_providers(settings: Settings, client: httpx.AsyncClient) -> dict[str, LLMProvider]:
    return build_provider_instances(settings, client)[0]


def build_llm_stack(settings: Settings, client: httpx.AsyncClient) -> LLMStack:
    providers, chains = build_provider_instances(settings, client)
    router = LLMRouter(
        providers=providers,
        chains=chains,
        rate_limiter=RateLimiter(settings.llm_rate_limits),
        policy=RouterPolicy(
            max_retries=settings.llm_max_retries,
            backoff_base_s=settings.llm_backoff_base_s,
            max_rate_wait_s=settings.llm_max_rate_wait_s,
            max_queue_wait_s=settings.llm_max_queue_wait_s,
            breaker_failure_threshold=settings.llm_breaker_failure_threshold,
            breaker_cooldown_s=settings.llm_breaker_cooldown_s,
        ),
    )
    return LLMStack(router=router, structured=StructuredLLM(router))
