"""Integration fixtures: a real Postgres (the docker-compose one) with a separate
`regtrace_test` database, migrated to head once per test session.

Skipped automatically when Postgres is not reachable.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import Database

OWNER_DSN = os.environ.get("TEST_OWNER_DSN", "postgresql://regtrace:regtrace@localhost:5432")
TEST_DB = "regtrace_test"
BACKEND = Path(__file__).resolve().parents[2]


def _async_url(dsn: str, db: str) -> str:
    return dsn.replace("postgresql://", "postgresql+asyncpg://") + f"/{db}"


def _app_url() -> str:
    host = OWNER_DSN.split("@", 1)[1]
    return f"postgresql+asyncpg://regtrace_app:regtrace_app@{host}/{TEST_DB}"


async def _recreate_database() -> None:
    conn = await asyncpg.connect(f"{OWNER_DSN}/postgres", timeout=3)
    try:
        await conn.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{TEST_DB}'")
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    try:
        asyncio.run(_recreate_database())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")
    env = {
        **os.environ,
        "MIGRATION_DATABASE_URL": _async_url(OWNER_DSN, TEST_DB),
        "DATABASE_URL": _app_url(),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    yield _app_url()


@pytest.fixture
def db_settings(migrated_db: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=migrated_db,
        migration_database_url=_async_url(OWNER_DSN, TEST_DB),
        embedding_backend="hashing",
        reranker_backend="none",
        worker_in_process=False,
        log_json=False,
        storage_dir=str(BACKEND / ".pytest_storage"),
    )


@pytest.fixture
async def database(db_settings: Settings) -> AsyncIterator[Database]:
    db = Database.from_settings(db_settings)
    yield db
    await db.dispose()


@pytest.fixture
async def owner_exec(migrated_db: str) -> AsyncIterator[object]:
    """Execute raw SQL as the owner role (bypasses RLS) - for setup and tamper tests."""
    engine = create_async_engine(_async_url(OWNER_DSN, TEST_DB))

    async def run(sql: str, **params: object) -> list[tuple[object, ...]]:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return [tuple(r) for r in result.fetchall()] if result.returns_rows else []

    yield run
    await engine.dispose()


async def make_bank(owner_exec: object, name: str | None = None) -> uuid.UUID:
    bank_id = uuid.uuid4()
    await owner_exec(  # type: ignore[operator]
        "INSERT INTO banks (id, name, profile, is_synthetic, created_at, updated_at) "
        "VALUES (:id, :name, '{}'::jsonb, true, now(), now())",
        id=bank_id,
        name=name or f"Test Bank {bank_id.hex[:6]}",
    )
    return bank_id
