"""Provider-neutral request/response types for LLM calls."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelTier(StrEnum):
    """Callers ask for a capability tier, never a concrete model name."""

    REASONING = "reasoning"  # extraction, mapping adjudication, change semantics
    FAST = "fast"  # classification, verification, query rewriting


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    purpose: str = Field(min_length=1, description="Stable label for tracing, e.g. 'obligation_extraction'")
    system: str
    messages: list[Message] = Field(min_length=1)
    tier: ModelTier = ModelTier.REASONING
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=2048, gt=0, le=65_536)
    # When set, the provider is asked for JSON output conforming to this schema.
    json_schema: dict[str, Any] | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"  # output truncated at max tokens
    OTHER = "other"


class ProviderResult(BaseModel):
    """What a single provider adapter returns for one successful call."""

    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    usage: TokenUsage
    finish_reason: FinishReason


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"  # provider returned 429
    QUOTA_SKIPPED = "quota_skipped"  # local limiter predicted a 429; not sent
    CIRCUIT_OPEN = "circuit_open"
    TRANSIENT_ERROR = "transient_error"
    PERMANENT_ERROR = "permanent_error"


class AttemptRecord(BaseModel):
    """One routing decision/attempt. The full list is persisted for traceability."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    outcome: AttemptOutcome
    latency_ms: float = 0.0
    error: str | None = None


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    provider: str
    model: str
    usage: TokenUsage
    finish_reason: FinishReason
    latency_ms: float
    attempts: list[AttemptRecord]
