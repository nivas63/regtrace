"""Composition root: builds every long-lived service once per process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.config import Settings
from app.db import Database
from app.llm import LLMStack, build_llm_stack
from app.llm.service import LLMService
from app.retrieval.embeddings import Embedder, Reranker, build_embedder, build_reranker
from app.storage import FileStore
from app.workflows.tools import ToolRegistry

if TYPE_CHECKING:
    from app.workflows.engine import WorkflowEngine


@dataclass
class Services:
    settings: Settings
    db: Database
    http: httpx.AsyncClient
    llm_stack: LLMStack
    llm: LLMService
    embedder: Embedder
    reranker: Reranker
    store: FileStore
    tools: ToolRegistry
    _engine: WorkflowEngine | None = field(default=None, repr=False)

    @property
    def engine(self) -> WorkflowEngine:
        if self._engine is None:
            raise RuntimeError("Workflow engine not initialised")
        return self._engine


@asynccontextmanager
async def build_services(settings: Settings) -> AsyncIterator[Services]:
    # Deferred imports: these modules import agents/services that need the container type.
    from app.tools_catalog import register_tools
    from app.workflows.definitions import register_workflows
    from app.workflows.engine import WorkflowEngine

    http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.llm_request_timeout_s, connect=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    db = Database.from_settings(settings)
    stack = build_llm_stack(settings, http)
    Path(settings.model_cache_dir).mkdir(parents=True, exist_ok=True)
    services = Services(
        settings=settings,
        db=db,
        http=http,
        llm_stack=stack,
        llm=LLMService(stack, db, settings),
        embedder=build_embedder(settings),
        reranker=build_reranker(settings),
        store=FileStore(settings.storage_dir),
        tools=ToolRegistry(),
    )
    register_tools(services.tools)
    engine = WorkflowEngine(services)
    register_workflows(engine)
    services._engine = engine
    try:
        yield services
    finally:
        await http.aclose()
        await db.dispose()
