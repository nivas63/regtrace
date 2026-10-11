from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Environment, Settings
from app.main import create_app


class FakeClock:
    """Deterministic monotonic clock for rate limiter / breaker / router tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    # Port 1 refuses connections immediately: readiness must report 503, fast.
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=Environment.TEST,
        log_json=False,
        database_url="postgresql+asyncpg://u:p@127.0.0.1:1/none",
        db_connect_timeout_s=1.0,
        gemini_api_key=None,
        groq_api_key=None,
        ollama_enabled=False,
        worker_in_process=False,
        embedding_backend="hashing",
        reranker_backend="none",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        yield test_client
