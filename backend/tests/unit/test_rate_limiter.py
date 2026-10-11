from __future__ import annotations

import math

from app.config import RateLimitConfig
from app.llm.circuit_breaker import BreakerState, CircuitBreaker
from app.llm.rate_limiter import RateLimiter
from tests.conftest import FakeClock

KEY = "gemini:flash"


def test_rpm_window_blocks_then_frees(clock: FakeClock) -> None:
    limiter = RateLimiter({KEY: RateLimitConfig(rpm=2)}, clock=clock)
    for _ in range(2):
        assert limiter.wait_time(KEY, 10) == 0
        limiter.reserve(KEY, 10)
    assert limiter.wait_time(KEY, 10) == 60
    clock.advance(30)
    assert limiter.wait_time(KEY, 10) == 30
    clock.advance(30)
    assert limiter.wait_time(KEY, 10) == 0


def test_daily_quota_is_rolling_24h(clock: FakeClock) -> None:
    limiter = RateLimiter({KEY: RateLimitConfig(rpd=1)}, clock=clock)
    limiter.reserve(KEY, 1)
    clock.advance(3600)
    assert limiter.wait_time(KEY, 1) == 86_400 - 3600


def test_tpm_counts_settled_actual_usage(clock: FakeClock) -> None:
    limiter = RateLimiter({KEY: RateLimitConfig(tpm=1000)}, clock=clock)
    reservation = limiter.reserve(KEY, 900)
    assert limiter.wait_time(KEY, 200) > 0
    limiter.settle(reservation, 300)  # provider reported far fewer tokens
    assert limiter.wait_time(KEY, 200) == 0


def test_request_larger_than_tpm_can_never_fit(clock: FakeClock) -> None:
    limiter = RateLimiter({KEY: RateLimitConfig(tpm=100)}, clock=clock)
    assert math.isinf(limiter.wait_time(KEY, 101))


def test_block_honours_retry_after(clock: FakeClock) -> None:
    limiter = RateLimiter({}, clock=clock)
    limiter.block(KEY, 20)
    assert limiter.wait_time(KEY, 1) == 20
    assert limiter.snapshot(KEY).blocked_for_s == 20


def test_unknown_key_is_unlimited(clock: FakeClock) -> None:
    limiter = RateLimiter({}, clock=clock)
    for _ in range(100):
        limiter.reserve("ollama:local", 5000)
    assert limiter.wait_time("ollama:local", 5000) == 0


def test_circuit_breaker_open_half_open_close(clock: FakeClock) -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=60, clock=clock)
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN and not breaker.allow()

    clock.advance(60)
    assert breaker.state is BreakerState.HALF_OPEN and breaker.allow()
    breaker.record_failure()  # failed probe re-opens immediately
    assert breaker.state is BreakerState.OPEN

    clock.advance(60)
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
