"""LLM error taxonomy.

Adapters translate every provider-specific failure into exactly one of three
provider errors; the router's behaviour depends only on that classification:

- LLMRateLimitedError -> do not retry here, fall through to the next provider
- LLMTransientError   -> retry with backoff on the same provider, then fall through
- LLMPermanentError   -> fall through immediately (bad key, rejected request, block)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.core.errors import RegTraceError

if TYPE_CHECKING:
    from app.llm.types import AttemptRecord


class LLMError(RegTraceError):
    code = "llm_error"
    title = "LLM error"
    http_status = 502


class LLMProviderError(LLMError):
    def __init__(self, detail: str, *, provider: str, status_code: int | None = None) -> None:
        super().__init__(detail, context={"provider": provider, "status_code": status_code})
        self.provider = provider
        self.status_code = status_code


class LLMRateLimitedError(LLMProviderError):
    code = "llm_rate_limited"

    def __init__(
        self,
        detail: str,
        *,
        provider: str,
        retry_after_s: float | None = None,
        status_code: int | None = 429,
    ) -> None:
        super().__init__(detail, provider=provider, status_code=status_code)
        self.retry_after_s = retry_after_s


class LLMTransientError(LLMProviderError):
    code = "llm_transient"


class LLMPermanentError(LLMProviderError):
    code = "llm_permanent"


class LLMExhaustedError(LLMError):
    """Every provider in the tier's chain failed or was unavailable."""

    code = "llm_unavailable"
    title = "No LLM provider available"
    http_status = 503

    def __init__(self, detail: str, *, attempts: Sequence[AttemptRecord]) -> None:
        super().__init__(detail, context={"attempts": [a.model_dump(mode="json") for a in attempts]})
        self.attempts = list(attempts)


class StructuredOutputError(LLMError):
    """The model's output could not be parsed/validated even after repair attempts."""

    code = "llm_invalid_output"
    title = "LLM returned invalid structured output"
