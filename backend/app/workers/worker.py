"""Job worker: claims QUEUED workflow runs (SELECT ... FOR UPDATE SKIP LOCKED) and
executes them. Runs in-process by default (see Settings.worker_in_process) or as a
standalone process: `python -m app.workers.worker`.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid

from app.observability.logging import get_logger
from app.workflows.engine import WorkflowEngine

_log = get_logger("regtrace.worker")


class Worker:
    def __init__(self, engine: WorkflowEngine, poll_interval_s: float) -> None:
        self._engine = engine
        self._poll = poll_interval_s
        self._stop = asyncio.Event()
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"

    async def run_forever(self) -> None:
        recovered = await self._engine.recover_orphans()
        _log.info("worker.started", worker_id=self.worker_id, recovered_runs=recovered)
        while not self._stop.is_set():
            try:
                run_id = await self._engine.claim_next(self.worker_id)
                if run_id is None:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._poll)
                    continue
                await self._engine.execute(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the worker alive; the run itself records failures
                _log.exception("worker.loop_error")
                await asyncio.sleep(self._poll)
        _log.info("worker.stopped", worker_id=self.worker_id)

    def stop(self) -> None:
        self._stop.set()


async def _main() -> None:
    from app.config import get_settings
    from app.container import build_services
    from app.observability.logging import configure_logging

    settings = get_settings()
    configure_logging(settings)
    async with build_services(settings) as services:
        await Worker(services.engine, settings.worker_poll_interval_s).run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
