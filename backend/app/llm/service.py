"""LLM service used by agents: cache, budget, redaction, persistence of every call.

agent -> LLMService.structured(...)
          1. redact PII (bank content only)
          2. cache lookup (prompt version + tier + inputs + schema)
          3. per-run budget check (hard stop, prevents runaway loops)
          4. StructuredLLM (router fallback + schema validation + repair)
          5. restore PII in the validated output
          6. persist llm_calls row (tokens, latency, provider trail, outcome)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import select, text, update

from app.config import Settings
from app.core.errors import RegTraceError
from app.db import Database
from app.llm.errors import LLMError, StructuredOutputError
from app.llm.factory import LLMStack
from app.llm.json_tools import extract_json_text, inline_schema
from app.llm.prompts import Prompt
from app.llm.types import LLMRequest, LLMResponse, Message, ModelTier
from app.models import LLMCacheEntry, LLMCall
from app.observability.logging import get_logger
from app.security.redaction import Redactor

T = TypeVar("T", bound=BaseModel)
_log = get_logger("regtrace.llm.service")


class LLMBudgetExceededError(RegTraceError):
    code = "llm_budget_exceeded"
    title = "Run exceeded its LLM call budget"
    http_status = 429


@dataclass(frozen=True)
class CallContext:
    agent: str
    run_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None


@dataclass(frozen=True)
class StructuredOutcome(Generic[T]):
    value: T
    cache_hit: bool
    provider: str
    model: str
    prompt_version: str


class LLMService:
    def __init__(self, stack: LLMStack, db: Database, settings: Settings) -> None:
        self._stack = stack
        self._db = db
        self._settings = settings

    @property
    def available(self) -> bool:
        return bool(self._stack.router.providers)

    async def structured(
        self,
        *,
        ctx: CallContext,
        prompt: Prompt,
        user_content: str,
        schema: type[T],
        tier: ModelTier = ModelTier.REASONING,
        max_output_tokens: int = 4096,
        redact: bool = False,
        use_cache: bool = True,
    ) -> StructuredOutcome[T]:
        redactor = Redactor()
        content = redactor.redact(user_content) if redact else user_content
        request = LLMRequest(
            purpose=prompt.name,
            system=prompt.system,
            messages=[Message(role="user", content=content)],
            tier=tier,
            max_output_tokens=max_output_tokens,
        )
        key = _cache_key(prompt, request, schema)
        started = time.perf_counter()

        if use_cache:
            cached = await self._cache_get(key)
            if cached is not None:
                try:
                    value = schema.model_validate_json(extract_json_text(cached.response_text))
                except ValueError:
                    value = None  # schema changed shape; fall through to a fresh call
                if value is not None:
                    await self._record(
                        ctx,
                        prompt,
                        tier,
                        cached.provider,
                        cached.model,
                        0,
                        0,
                        (time.perf_counter() - started) * 1000,
                        True,
                        "SUCCESS",
                        0,
                        [],
                        None,
                    )
                    return StructuredOutcome(
                        _restore(value, redactor, schema),
                        True,
                        cached.provider,
                        cached.model,
                        prompt.version,
                    )

        await self._consume_budget(ctx)
        try:
            result = await self._stack.structured.generate(request, schema)
        except LLMError as exc:
            outcome = "INVALID_OUTPUT" if isinstance(exc, StructuredOutputError) else "EXHAUSTED"
            await self._record(
                ctx,
                prompt,
                tier,
                None,
                None,
                0,
                0,
                (time.perf_counter() - started) * 1000,
                False,
                outcome,
                0,
                exc.context.get("attempts", []),
                exc.detail,
            )
            raise

        responses: list[LLMResponse] = result.responses
        last = responses[-1]
        input_tokens = sum(r.usage.input_tokens for r in responses)
        output_tokens = sum(r.usage.output_tokens for r in responses)
        await self._record(
            ctx,
            prompt,
            tier,
            last.provider,
            last.model,
            input_tokens,
            output_tokens,
            (time.perf_counter() - started) * 1000,
            False,
            "SUCCESS",
            len(responses) - 1,
            [a.model_dump(mode="json") for r in responses for a in r.attempts],
            None,
        )
        if use_cache:
            await self._cache_put(key, prompt.name, last, result.value.model_dump_json())
        return StructuredOutcome(
            _restore(result.value, redactor, schema),
            False,
            last.provider,
            last.model,
            prompt.version,
        )

    # -- persistence --------------------------------------------------------------

    async def _cache_get(self, key: str) -> LLMCacheEntry | None:
        async with self._db.system_session() as session:
            entry = await session.get(LLMCacheEntry, key)
            if entry is not None:
                await session.execute(
                    update(LLMCacheEntry)
                    .where(LLMCacheEntry.key == key)
                    .values(hits=LLMCacheEntry.hits + 1, last_hit_at=text("now()"))
                )
            return entry

    async def _cache_put(self, key: str, purpose: str, response: LLMResponse, body: str) -> None:
        async with self._db.system_session() as session:
            exists = await session.scalar(select(LLMCacheEntry.key).where(LLMCacheEntry.key == key))
            if exists is None:
                session.add(
                    LLMCacheEntry(
                        key=key,
                        purpose=purpose,
                        response_text=body,
                        provider=response.provider,
                        model=response.model,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                    )
                )

    async def _consume_budget(self, ctx: CallContext) -> None:
        if ctx.run_id is None:
            return
        async with self._db.system_session() as session:
            used = await session.scalar(
                text(
                    "UPDATE workflow_runs SET llm_calls_used = llm_calls_used + 1 "
                    "WHERE id = :id AND llm_calls_used < llm_call_budget RETURNING llm_calls_used"
                ),
                {"id": ctx.run_id},
            )
        if used is None:
            raise LLMBudgetExceededError(
                "The workflow run reached its LLM call budget", context={"run_id": str(ctx.run_id)}
            )

    async def _record(
        self,
        ctx: CallContext,
        prompt: Prompt,
        tier: ModelTier,
        provider: str | None,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        cache_hit: bool,
        outcome: str,
        repairs: int,
        attempts: list[dict[str, Any]],
        error: str | None,
    ) -> None:
        price_in, price_out = (
            (self._settings.equivalent_price_reasoning_in, self._settings.equivalent_price_reasoning_out)
            if tier is ModelTier.REASONING
            else (self._settings.equivalent_price_fast_in, self._settings.equivalent_price_fast_out)
        )
        equivalent = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
        async with self._db.system_session() as session:
            session.add(
                LLMCall(
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    purpose=prompt.name,
                    prompt_version=prompt.version,
                    tier=tier.value,
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                    outcome=outcome,
                    repairs=repairs,
                    attempts=attempts,
                    actual_cost_usd=0.0,  # free tiers / local model
                    equivalent_cost_usd=round(equivalent, 6),
                    error=error,
                )
            )
        _log.info(
            "llm.call",
            agent=ctx.agent,
            purpose=prompt.name,
            outcome=outcome,
            cache_hit=cache_hit,
            provider=provider,
            tokens=input_tokens + output_tokens,
        )


def _cache_key(prompt: Prompt, request: LLMRequest, schema: type[BaseModel]) -> str:
    payload = {
        "prompt": prompt.ref,
        "tier": request.tier.value,
        "system": request.system,
        "messages": [m.model_dump() for m in request.messages],
        "schema": inline_schema(schema.model_json_schema()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _restore(value: T, redactor: Redactor, schema: type[T]) -> T:
    if not redactor.redacted_count:
        return value
    return schema.model_validate_json(redactor.restore(value.model_dump_json()))
