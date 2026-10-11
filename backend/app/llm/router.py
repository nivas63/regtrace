"""Tier-based LLM router with quota awareness, retries, circuit breaking and fallback.

Routing algorithm for one request:

    for provider in chain[tier]:
        skip if not configured, circuit open, or local quota cannot free up soon
        up to (1 + max_retries) attempts:
            success          -> return (with the full attempt trail)
            rate limited     -> honour retry-after, move to next provider
            transient error  -> exponential backoff + jitter, retry same provider
            permanent error  -> move to next provider
    raise LLMExhaustedError(attempts)

Every attempt, including skips, is recorded in `LLMResponse.attempts` so the agent
trace can show exactly why a given model answered.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from app.llm.base import LLMProvider
from app.llm.circuit_breaker import CircuitBreaker
from app.llm.errors import (
    LLMExhaustedError,
    LLMPermanentError,
    LLMRateLimitedError,
    LLMTransientError,
)
from app.llm.rate_limiter import RateLimiter, estimate_tokens
from app.llm.types import (
    AttemptOutcome,
    AttemptRecord,
    LLMRequest,
    LLMResponse,
    ModelTier,
    ProviderResult,
)
from app.observability.logging import get_logger

_log = get_logger("regtrace.llm")
# Used when a 429 carries no retry-after hint.
_DEFAULT_RATE_LIMIT_COOLDOWN_S = 30.0
# Passes over the chain when every provider is momentarily throttled.
_MAX_PASSES = 3
_HARD_FAILURES = frozenset({AttemptOutcome.PERMANENT_ERROR, AttemptOutcome.TRANSIENT_ERROR})


@dataclass(frozen=True)
class RouterPolicy:
    max_retries: int = 2
    backoff_base_s: float = 1.0
    max_rate_wait_s: float = 15.0
    # Upper bound on queueing for the soonest free window when the whole chain is throttled.
    max_queue_wait_s: float = 90.0
    breaker_failure_threshold: int = 3
    breaker_cooldown_s: float = 60.0


class LLMRouter:
    def __init__(
        self,
        providers: Mapping[str, LLMProvider],
        chains: Mapping[ModelTier, Sequence[str]],
        rate_limiter: RateLimiter,
        policy: RouterPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._providers = dict(providers)
        self._chains = {tier: list(chain) for tier, chain in chains.items()}
        self._limiter = rate_limiter
        self._policy = policy
        self._clock = clock
        self._sleep = sleep
        self._breakers: dict[str, CircuitBreaker] = {}

    @property
    def providers(self) -> Mapping[str, LLMProvider]:
        return self._providers

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._limiter

    def chain(self, tier: ModelTier) -> list[str]:
        return list(self._chains.get(tier, []))

    def breaker(self, key: str) -> CircuitBreaker:
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(
                self._policy.breaker_failure_threshold,
                self._policy.breaker_cooldown_s,
                clock=self._clock,
            )
        return self._breakers[key]

    async def generate(self, request: LLMRequest) -> LLMResponse:
        attempts: list[AttemptRecord] = []
        started = self._clock()
        estimate = estimate_tokens(
            len(request.system) + sum(len(m.content) for m in request.messages),
            request.max_output_tokens,
        )

        failed: set[str] = set()
        for pass_no in range(_MAX_PASSES):
            response = await self._pass(request, estimate, attempts, started, failed)
            if response is not None:
                return response
            # Providers that errored (not merely throttled) are not retried in later passes.
            failed |= {a.provider for a in attempts if a.outcome in _HARD_FAILURES}
            # Every provider declined. If one of them only needs a short wait for its
            # per-minute window (free tiers), queue for it instead of failing the step.
            wait = self._shortest_quota_wait(request, estimate, failed)
            if wait is None or wait > self._policy.max_queue_wait_s or pass_no == _MAX_PASSES - 1:
                break
            _log.info("llm.queue_for_quota", purpose=request.purpose, wait_s=round(wait, 1), pass_no=pass_no)
            await self._sleep(wait + 0.5)

        _log.error(
            "llm.exhausted",
            purpose=request.purpose,
            tier=request.tier.value,
            attempts=[a.model_dump(mode="json") for a in attempts],
        )
        raise LLMExhaustedError(
            f"No provider could serve tier '{request.tier.value}' for '{request.purpose}'",
            attempts=attempts,
        )

    def _quota_key(self, provider: LLMProvider, tier: ModelTier) -> str:
        # Quota is per provider family + model ("gemini#2" shares the "gemini" account).
        return f"{provider.name.split('#')[0]}:{provider.model_for(tier)}"

    def _shortest_quota_wait(self, request: LLMRequest, estimate: int, failed: set[str]) -> float | None:
        waits = []
        for name in self._chains.get(request.tier, []):
            provider = self._providers.get(name)
            if provider is None or name in failed:
                continue
            key = self._quota_key(provider, request.tier)
            if not self.breaker(key).allow():
                continue
            waits.append(self._limiter.wait_time(key, estimate))
        # Only throttled providers (positive, finite wait) are worth queueing for; a zero
        # wait means the provider declined for another reason (error, bad request).
        throttled = [w for w in waits if 0 < w != float("inf")]
        return min(throttled) if throttled else None

    async def _pass(
        self, request: LLMRequest, estimate: int, attempts: list[AttemptRecord], started: float, failed: set[str]
    ) -> LLMResponse | None:
        for name in self._chains.get(request.tier, []):
            provider = self._providers.get(name)
            if provider is None or name in failed:
                continue  # not configured, or already failed for a non-quota reason
            result = await self._try_provider(provider, request, estimate, attempts)
            if result is not None:
                latency = (self._clock() - started) * 1000
                _log.info(
                    "llm.completed",
                    purpose=request.purpose,
                    tier=request.tier.value,
                    provider=provider.name,
                    model=result.model,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    latency_ms=round(latency, 1),
                    attempts=len(attempts),
                )
                return LLMResponse(
                    text=result.text,
                    provider=provider.name,
                    model=result.model,
                    usage=result.usage,
                    finish_reason=result.finish_reason,
                    latency_ms=latency,
                    attempts=attempts,
                )
        return None

    async def _try_provider(
        self,
        provider: LLMProvider,
        request: LLMRequest,
        estimate: int,
        attempts: list[AttemptRecord],
    ) -> ProviderResult | None:
        model = provider.model_for(request.tier)
        key = self._quota_key(provider, request.tier)
        breaker = self.breaker(key)

        for attempt_no in range(self._policy.max_retries + 1):
            if not breaker.allow():
                attempts.append(_record(provider.name, model, AttemptOutcome.CIRCUIT_OPEN))
                return None

            wait = self._limiter.wait_time(key, estimate)
            if wait > self._policy.max_rate_wait_s:
                attempts.append(
                    _record(
                        provider.name,
                        model,
                        AttemptOutcome.QUOTA_SKIPPED,
                        error=f"local quota frees in {wait:.0f}s",
                    )
                )
                return None
            if wait > 0:
                await self._sleep(wait)

            reservation = self._limiter.reserve(key, estimate)
            call_started = self._clock()
            try:
                result = await provider.generate(request, model)
            except LLMRateLimitedError as exc:
                attempts.append(
                    _record(
                        provider.name,
                        model,
                        AttemptOutcome.RATE_LIMITED,
                        latency_ms=(self._clock() - call_started) * 1000,
                        error=exc.detail,
                    )
                )
                # A short, provider-stated cool-down (e.g. a per-minute token window) is
                # cheaper to wait out than to fall through; long ones fall through and the
                # provider is blocked locally for the stated period.
                if (
                    exc.retry_after_s is not None
                    and exc.retry_after_s <= self._policy.max_rate_wait_s
                    and attempt_no < self._policy.max_retries
                ):
                    await self._sleep(exc.retry_after_s + 0.5)
                    continue
                self._limiter.block(key, exc.retry_after_s or _DEFAULT_RATE_LIMIT_COOLDOWN_S)
                return None
            except LLMTransientError as exc:
                breaker.record_failure()
                attempts.append(
                    _record(
                        provider.name,
                        model,
                        AttemptOutcome.TRANSIENT_ERROR,
                        latency_ms=(self._clock() - call_started) * 1000,
                        error=exc.detail,
                    )
                )
                if attempt_no < self._policy.max_retries:
                    await self._sleep(self._backoff(attempt_no))
                continue
            except LLMPermanentError as exc:
                breaker.record_failure()
                attempts.append(
                    _record(
                        provider.name,
                        model,
                        AttemptOutcome.PERMANENT_ERROR,
                        latency_ms=(self._clock() - call_started) * 1000,
                        error=exc.detail,
                    )
                )
                return None

            breaker.record_success()
            self._limiter.settle(reservation, result.usage.total)
            attempts.append(
                _record(
                    provider.name,
                    result.model,
                    AttemptOutcome.SUCCESS,
                    latency_ms=(self._clock() - call_started) * 1000,
                )
            )
            return result
        return None

    def _backoff(self, attempt_no: int) -> float:
        base = self._policy.backoff_base_s * 2.0**attempt_no
        return base + random.uniform(0, base / 2)  # noqa: S311 - jitter, not crypto


def _record(
    provider: str,
    model: str,
    outcome: AttemptOutcome,
    *,
    latency_ms: float = 0.0,
    error: str | None = None,
) -> AttemptRecord:
    return AttemptRecord(provider=provider, model=model, outcome=outcome, latency_ms=latency_ms, error=error)
