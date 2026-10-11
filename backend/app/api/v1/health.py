"""Health endpoints.

- /health/live  : process is up (no dependencies checked) - container liveness probe
- /health/ready : database reachable - readiness probe; 503 otherwise
- /health/llm   : configured providers, circuit state and local quota usage.
                  Makes NO model calls, so polling it never consumes free-tier quota.
"""

from __future__ import annotations

import math

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.api.deps import DatabaseDep, LLMDep, SettingsDep
from app.llm.types import ModelTier

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadyResponse(BaseModel):
    status: str
    database: str


class QuotaView(BaseModel):
    requests_last_minute: int
    requests_last_day: int
    tokens_last_minute: int
    rpm_limit: int | None
    rpd_limit: int | None
    tpm_limit: int | None
    blocked_for_s: float


class ProviderView(BaseModel):
    provider: str
    tier: str
    model: str
    circuit: str
    quota: QuotaView


class LLMHealthResponse(BaseModel):
    configured_providers: list[str]
    chains: dict[str, list[str]]
    routes: list[ProviderView]


@router.get("/live", response_model=LiveResponse)
async def live(settings: SettingsDep) -> LiveResponse:
    return LiveResponse(status="ok", version=__version__, environment=settings.environment)


@router.get("/ready", response_model=ReadyResponse)
async def ready(db: DatabaseDep) -> ReadyResponse:
    await db.ping()  # raises DependencyUnavailableError -> 503 problem+json
    return ReadyResponse(status="ok", database="ok")


@router.get("/llm", response_model=LLMHealthResponse)
async def llm_health(llm: LLMDep) -> LLMHealthResponse:
    llm_router = llm.router
    routes: list[ProviderView] = []
    for tier in ModelTier:
        for name in llm_router.chain(tier):
            provider = llm_router.providers.get(name)
            if provider is None:
                continue
            model = provider.model_for(tier)
            key = f"{name.split('#')[0]}:{model}"
            snap = llm_router.rate_limiter.snapshot(key)
            routes.append(
                ProviderView(
                    provider=name,
                    tier=tier.value,
                    model=model,
                    circuit=llm_router.breaker(key).state.value,
                    quota=QuotaView(
                        requests_last_minute=snap.requests_last_minute,
                        requests_last_day=snap.requests_last_day,
                        tokens_last_minute=snap.tokens_last_minute,
                        rpm_limit=snap.limit.rpm,
                        rpd_limit=snap.limit.rpd,
                        tpm_limit=snap.limit.tpm,
                        blocked_for_s=0.0 if math.isinf(snap.blocked_for_s) else snap.blocked_for_s,
                    ),
                )
            )
    return LLMHealthResponse(
        configured_providers=sorted(llm_router.providers),
        chains={tier.value: llm_router.chain(tier) for tier in ModelTier},
        routes=routes,
    )
