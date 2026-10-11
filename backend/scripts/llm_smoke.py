"""Verify free-tier LLM credentials end to end.

For every configured provider this script:
  1. lists the models visible to your key (so you can confirm model names),
  2. makes ONE real structured-output call per tier (2 requests per provider),
  3. prints the validated result, latency and token usage.

Usage (from backend/):  python -m scripts.llm_smoke
It spends 2 requests of daily quota per configured provider.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm.errors import LLMError
from app.llm.factory import build_providers
from app.llm.rate_limiter import RateLimiter
from app.llm.router import LLMRouter, RouterPolicy
from app.llm.structured import StructuredLLM
from app.llm.types import LLMRequest, Message, ModelTier
from app.observability.logging import configure_logging

CLAUSE = (
    "The bank shall review the risk categorisation of customers at least once in "
    "six months and shall report the outcome to the Board."
)


class ClauseProbe(BaseModel):
    modality: Literal["MUST", "MUST_NOT", "SHOULD", "MAY"]
    action: str = Field(description="What must be done, in a few words")
    frequency_iso8601: str | None = Field(description="ISO-8601 duration, e.g. P6M, or null")


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.model_copy(update={"log_json": False, "log_level": "WARNING"}))
    failures = 0

    async with httpx.AsyncClient() as client:
        providers = build_providers(settings, client)
        if not providers:
            print("No providers configured. Set GEMINI_API_KEY and/or GROQ_API_KEY in .env")
            return 1

        for name, provider in providers.items():
            print(f"\n=== {name} ===")
            try:
                models = await provider.list_models()
                print(f"models visible: {len(models)}")
                for tier in ModelTier:
                    wanted = provider.model_for(tier)
                    found = "OK" if wanted in models else "NOT LISTED - check the name"
                    print(f"  {tier.value:<9} -> {wanted}  [{found}]")
            except LLMError as exc:
                print(f"  could not list models: {exc.detail}")

            # Single-provider router so the call cannot silently fall back elsewhere.
            router = LLMRouter(
                providers={name: provider},
                chains={tier: [name] for tier in ModelTier},
                rate_limiter=RateLimiter(settings.llm_rate_limits),
                policy=RouterPolicy(max_retries=1),
            )
            structured = StructuredLLM(router)
            for tier in ModelTier:
                request = LLMRequest(
                    purpose="smoke_test",
                    tier=tier,
                    system="You extract regulatory obligations precisely.",
                    messages=[Message(role="user", content=f"Analyse this clause:\n{CLAUSE}")],
                    max_output_tokens=1024,
                )
                try:
                    result = await structured.generate(request, ClauseProbe)
                except LLMError as exc:
                    failures += 1
                    print(f"  [{tier.value}] FAILED: {exc.code}: {exc.detail}")
                    continue
                last = result.responses[-1]
                print(
                    f"  [{tier.value}] {last.model}: {result.value.model_dump()} "
                    f"({last.latency_ms:.0f} ms, {last.usage.input_tokens} in / "
                    f"{last.usage.output_tokens} out, repairs={len(result.responses) - 1})"
                )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
