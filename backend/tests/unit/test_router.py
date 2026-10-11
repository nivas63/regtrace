from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.config import RateLimitConfig
from app.llm.errors import (
    LLMExhaustedError,
    LLMPermanentError,
    LLMProviderError,
    LLMRateLimitedError,
    LLMTransientError,
)
from app.llm.rate_limiter import RateLimiter
from app.llm.router import LLMRouter, RouterPolicy
from app.llm.types import (
    AttemptOutcome,
    FinishReason,
    LLMRequest,
    Message,
    ModelTier,
    ProviderResult,
    TokenUsage,
)
from tests.conftest import FakeClock


class ScriptedProvider:
    """Test double: returns/raises a scripted sequence of outcomes."""

    def __init__(self, name: str, script: Sequence[str | LLMProviderError]) -> None:
        self._name = name
        self._script = list(script)
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def model_for(self, tier: ModelTier) -> str:
        return f"{self._name}-{tier.value}"

    async def generate(self, request: LLMRequest, model: str) -> ProviderResult:
        self.calls += 1
        step = self._script.pop(0)
        if isinstance(step, LLMProviderError):
            raise step
        return ProviderResult(
            text=step,
            model=model,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            finish_reason=FinishReason.STOP,
        )

    async def list_models(self) -> list[str]:
        return []


REQUEST = LLMRequest(purpose="test", system="sys", messages=[Message(role="user", content="hi")])


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _router(
    *providers: ScriptedProvider,
    clock: FakeClock,
    limits: dict[str, RateLimitConfig] | None = None,
    sleeps: Sleeps | None = None,
    policy: RouterPolicy | None = None,
) -> LLMRouter:
    return LLMRouter(
        providers={p.name: p for p in providers},
        chains={tier: [p.name for p in providers] for tier in ModelTier},
        rate_limiter=RateLimiter(limits or {}, clock=clock),
        policy=policy or RouterPolicy(max_retries=2, backoff_base_s=1.0),
        clock=clock,
        sleep=sleeps or Sleeps(),
    )


async def test_first_provider_success(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", ["ok"])
    response = await _router(primary, clock=clock).generate(REQUEST)
    assert response.text == "ok" and response.provider == "a"
    assert [a.outcome for a in response.attempts] == [AttemptOutcome.SUCCESS]


async def test_rate_limit_falls_through_without_retrying(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", [LLMRateLimitedError("429", provider="a", retry_after_s=40)])
    backup = ScriptedProvider("b", ["from b"])
    router = _router(primary, backup, clock=clock)

    response = await router.generate(REQUEST)

    assert response.provider == "b" and primary.calls == 1
    assert response.attempts[0].outcome is AttemptOutcome.RATE_LIMITED
    # The provider-reported cooldown is honoured on the next request too.
    assert router.rate_limiter.wait_time("a:a-reasoning", 1) == 40


async def test_transient_error_retries_with_backoff(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", [LLMTransientError("503", provider="a"), "recovered"])
    sleeps = Sleeps()
    response = await _router(primary, clock=clock, sleeps=sleeps).generate(REQUEST)
    assert response.text == "recovered" and primary.calls == 2
    assert len(sleeps.calls) == 1 and 1.0 <= sleeps.calls[0] <= 1.5


async def test_permanent_error_skips_to_next_provider(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", [LLMPermanentError("401", provider="a", status_code=401)])
    backup = ScriptedProvider("b", ["ok"])
    response = await _router(primary, backup, clock=clock).generate(REQUEST)
    assert response.provider == "b" and primary.calls == 1


async def test_local_quota_skips_provider_without_calling_it(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", ["never"])
    backup = ScriptedProvider("b", ["ok"])
    router = _router(primary, backup, clock=clock, limits={"a:a-reasoning": RateLimitConfig(rpd=1)})
    router.rate_limiter.reserve("a:a-reasoning", 1)  # daily quota already used

    response = await router.generate(REQUEST)

    assert primary.calls == 0 and response.provider == "b"
    assert response.attempts[0].outcome is AttemptOutcome.QUOTA_SKIPPED


async def test_short_quota_wait_sleeps_instead_of_falling_through(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", ["ok"])
    sleeps = Sleeps()
    router = _router(
        primary,
        clock=clock,
        sleeps=sleeps,
        limits={"a:a-reasoning": RateLimitConfig(rpm=1)},
        policy=RouterPolicy(max_rate_wait_s=90),
    )
    router.rate_limiter.reserve("a:a-reasoning", 1)
    response = await router.generate(REQUEST)
    assert response.provider == "a" and sleeps.calls == [60]


async def test_circuit_opens_after_repeated_failures(clock: FakeClock) -> None:
    failures: list[str | LLMProviderError] = [LLMTransientError("down", provider="a") for _ in range(3)]
    primary = ScriptedProvider("a", failures)
    backup = ScriptedProvider("b", ["1", "2"])
    router = _router(
        primary,
        backup,
        clock=clock,
        policy=RouterPolicy(max_retries=2, breaker_failure_threshold=3),
    )

    await router.generate(REQUEST)  # 3 failures on "a" -> breaker opens
    second = await router.generate(REQUEST)

    assert primary.calls == 3  # not called again while open
    assert second.attempts[0].outcome is AttemptOutcome.CIRCUIT_OPEN


async def test_all_providers_exhausted_raises_with_trail(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", [LLMPermanentError("400", provider="a")])
    backup = ScriptedProvider("b", [LLMRateLimitedError("429", provider="b")])
    with pytest.raises(LLMExhaustedError) as info:
        await _router(primary, backup, clock=clock).generate(REQUEST)
    outcomes = [a.outcome for a in info.value.attempts]
    assert outcomes[:2] == [AttemptOutcome.PERMANENT_ERROR, AttemptOutcome.RATE_LIMITED]
    # Later passes never re-call the permanently failed provider.
    assert AttemptOutcome.PERMANENT_ERROR not in outcomes[2:]


async def test_short_retry_after_is_waited_out_on_same_provider(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", [LLMRateLimitedError("429 tpm", provider="a", retry_after_s=6), "after wait"])
    backup = ScriptedProvider("b", ["should not be used"])
    sleeps = Sleeps()
    router = _router(
        primary, backup, clock=clock, sleeps=sleeps, policy=RouterPolicy(max_retries=2, max_rate_wait_s=15)
    )
    response = await router.generate(REQUEST)
    assert response.provider == "a" and backup.calls == 0
    assert sleeps.calls == [6.5]


async def test_whole_chain_throttled_queues_for_soonest_window(clock: FakeClock) -> None:
    primary = ScriptedProvider("a", ["served after queueing"])
    sleeps = Sleeps()

    async def advancing_sleep(seconds: float) -> None:
        sleeps.calls.append(seconds)
        clock.advance(seconds)

    router = LLMRouter(
        providers={"a": primary},
        chains={tier: ["a"] for tier in ModelTier},
        rate_limiter=RateLimiter({"a:a-reasoning": RateLimitConfig(rpm=1)}, clock=clock),
        policy=RouterPolicy(max_rate_wait_s=15, max_queue_wait_s=90),
        clock=clock,
        sleep=advancing_sleep,
    )
    router.rate_limiter.reserve("a:a-reasoning", 1)  # per-minute slot used: frees in 60 s
    response = await router.generate(REQUEST)
    assert response.text == "served after queueing"
    assert response.attempts[0].outcome is AttemptOutcome.QUOTA_SKIPPED
    assert sleeps.calls and sleeps.calls[0] == 60.5
