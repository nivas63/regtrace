"""Async database engine and session management.

Tenant isolation: bank-scoped tables have Postgres row-level security keyed on the
transaction-local setting `app.bank_id`. `tenant_session(bank_id)` opens a
transaction and sets it with `set_config(..., is_local => true)`, so the setting can
never leak to another request through the connection pool.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.core.errors import DependencyUnavailableError


class Database:
    def __init__(self, engine: AsyncEngine, connect_timeout_s: float) -> None:
        self.engine = engine
        self._connect_timeout_s = connect_timeout_s
        self.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            pool_pre_ping=True,
            connect_args={"timeout": settings.db_connect_timeout_s},
        )
        return cls(engine, settings.db_connect_timeout_s)

    @asynccontextmanager
    async def tenant_session(self, bank_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        """One transaction scoped to a bank. Commits on success, rolls back on error."""
        async with self.sessionmaker() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.bank_id', :bank_id, true)"),
                {"bank_id": str(bank_id)},
            )
            yield session

    @asynccontextmanager
    async def system_session(self) -> AsyncIterator[AsyncSession]:
        """Transaction without tenant context: sees only shared/public rows and
        system tables (runs, LLM calls, cache). Tenant rows are invisible here."""
        async with self.sessionmaker() as session, session.begin():
            yield session

    async def ping(self) -> None:
        """Round-trip `SELECT 1`; raises DependencyUnavailableError on any failure."""
        try:
            async with asyncio.timeout(self._connect_timeout_s + 1):
                async with self.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise DependencyUnavailableError("Database is unreachable", context={"error": type(exc).__name__}) from exc

    async def dispose(self) -> None:
        await self.engine.dispose()
