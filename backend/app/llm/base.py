"""The provider contract every LLM adapter implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.llm.types import LLMRequest, ModelTier, ProviderResult


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable provider id used in routing chains, quotas and traces."""
        ...

    def model_for(self, tier: ModelTier) -> str:
        """Concrete model that serves `tier` on this provider."""
        ...

    async def generate(self, request: LLMRequest, model: str) -> ProviderResult:
        """Run one completion. Must raise only app.llm.errors.LLMProviderError subclasses."""
        ...

    async def list_models(self) -> list[str]:
        """Model ids visible to the configured credentials (used by the smoke check)."""
        ...
