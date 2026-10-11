"""Application factory.

Run with:  uvicorn --factory app.main:create_app
Using a factory (instead of a module-level `app`) means importing this module has
no side effects, and tests can create apps with their own Settings.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.v1 import api_router
from app.config import Settings, get_settings
from app.container import build_services
from app.observability.logging import configure_logging, get_logger
from app.observability.middleware import RequestContextMiddleware
from app.workers.worker import Worker

_log = get_logger("regtrace.app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build_services(settings) as services:
            app.state.settings = settings
            app.state.services = services
            worker_task: asyncio.Task[None] | None = None
            worker: Worker | None = None
            if settings.worker_in_process:
                worker = Worker(services.engine, settings.worker_poll_interval_s)
                worker_task = asyncio.create_task(worker.run_forever(), name="regtrace-worker")
            _log.info(
                "app.started",
                version=__version__,
                environment=settings.environment,
                llm_providers=sorted(services.llm_stack.router.providers),
                worker_in_process=settings.worker_in_process,
                embedder=services.embedder.name,
            )
            try:
                yield
            finally:
                if worker is not None and worker_task is not None:
                    worker.stop()
                    worker_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await worker_task
                _log.info("app.stopped")

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description="AI-assisted regulatory compliance intelligence. Not legal advice.",
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    # Added last so it is the outermost middleware and wraps everything else.
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app
