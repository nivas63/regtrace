"""Client-side quota tracking for free-tier providers.

Free tiers enforce requests/minute, tokens/minute and requests/day. Tracking these
locally lets the router skip a provider *before* burning a request on a 429 and
lets the UI show remaining quota.

Prototype implementation: counters are in-process memory, so each process (API,
worker) tracks its own usage. Phase 2 moves the counters into Postgres so all
processes share one view of the quota.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.config import RateLimitConfig

_MINUTE = 60.0
_DAY = 86_400.0


@dataclass
class _Window:
    requests_day: deque[float] = field(default_factory=deque)
    tokens_minute: deque[tuple[float, int]] = field(default_factory=deque)
    blocked_until: float = 0.0


@dataclass(frozen=True)
class Reservation:
    key: str
    timestamp: float
    estimated_tokens: int


@dataclass(frozen=True)
class QuotaSnapshot:
    key: str
    requests_last_minute: int
    requests_last_day: int
    tokens_last_minute: int
    limit: RateLimitConfig
    blocked_for_s: float


class RateLimiter:
    def __init__(
        self,
        limits: Mapping[str, RateLimitConfig],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = dict(limits)
        self._clock = clock
        self._windows: dict[str, _Window] = {}

    def _window(self, key: str) -> _Window:
        return self._windows.setdefault(key, _Window())

    def _prune(self, window: _Window, now: float) -> None:
        while window.requests_day and now - window.requests_day[0] >= _DAY:
            window.requests_day.popleft()
        while window.tokens_minute and now - window.tokens_minute[0][0] >= _MINUTE:
            window.tokens_minute.popleft()

    def wait_time(self, key: str, estimated_tokens: int) -> float:
        """Seconds until a request of this size is allowed (0 = now, inf = not today)."""
        now = self._clock()
        window = self._window(key)
        self._prune(window, now)
        limit = self._limits.get(key, RateLimitConfig())
        waits = [max(0.0, window.blocked_until - now)]

        if limit.rpd is not None and len(window.requests_day) >= limit.rpd:
            # Rolling 24h window: frees up when the oldest request ages out.
            waits.append(window.requests_day[0] + _DAY - now)

        if limit.rpm is not None:
            recent = [t for t in window.requests_day if now - t < _MINUTE]
            if len(recent) >= limit.rpm:
                waits.append(recent[len(recent) - limit.rpm] + _MINUTE - now)

        if limit.tpm is not None:
            if estimated_tokens > limit.tpm:
                return math.inf  # can never fit on this provider
            used = sum(tokens for _, tokens in window.tokens_minute)
            overflow = used + estimated_tokens - limit.tpm
            for ts, tokens in window.tokens_minute:
                if overflow <= 0:
                    break
                overflow -= tokens
                waits.append(ts + _MINUTE - now)

        return max(waits)

    def reserve(self, key: str, estimated_tokens: int) -> Reservation:
        """Record a request that is about to be sent (call only when wait_time()==0)."""
        now = self._clock()
        window = self._window(key)
        window.requests_day.append(now)
        window.tokens_minute.append((now, estimated_tokens))
        return Reservation(key=key, timestamp=now, estimated_tokens=estimated_tokens)

    def settle(self, reservation: Reservation, actual_tokens: int) -> None:
        """Replace the token estimate with the provider-reported usage."""
        window = self._window(reservation.key)
        for index, (ts, tokens) in enumerate(window.tokens_minute):
            if ts == reservation.timestamp and tokens == reservation.estimated_tokens:
                window.tokens_minute[index] = (ts, actual_tokens)
                return

    def block(self, key: str, seconds: float) -> None:
        """Honour a provider-reported cooldown (e.g. from a 429 retry-after)."""
        window = self._window(key)
        window.blocked_until = max(window.blocked_until, self._clock() + seconds)

    def snapshot(self, key: str) -> QuotaSnapshot:
        now = self._clock()
        window = self._window(key)
        self._prune(window, now)
        return QuotaSnapshot(
            key=key,
            requests_last_minute=sum(1 for t in window.requests_day if now - t < _MINUTE),
            requests_last_day=len(window.requests_day),
            tokens_last_minute=sum(tokens for _, tokens in window.tokens_minute),
            limit=self._limits.get(key, RateLimitConfig()),
            blocked_for_s=max(0.0, window.blocked_until - now),
        )


def estimate_tokens(text_chars: int, max_output_tokens: int) -> int:
    """Pre-call estimate: ~4 chars/token input plus the requested output allowance.

    Providers such as Groq count the *requested* max tokens against the per-minute
    token quota, so the full allowance is reserved and settled with actual usage."""
    return math.ceil(text_chars / 4) + max_output_tokens
