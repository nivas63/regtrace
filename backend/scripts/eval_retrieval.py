"""Retrieval benchmark on an ISOLATED database (no LLM required).

Creates/migrates a scratch database, ingests the synthetic regulations through the
real ingestion path (parse -> structure -> chunk -> embed), then runs the golden
retrieval queries through the production hybrid searcher and prints Recall@k / MRR.

Usage (inside the api container, which has the real embedding models):
  docker compose exec api python -m scripts.eval_retrieval
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import asyncpg

from app.config import get_settings
from app.container import build_services
from app.domain.types import DocClass
from app.evaluation.harness import DOC_FILES, Evaluator
from app.observability.logging import configure_logging
from app.services.ingestion import IngestionService
from app.services.seed import ensure_bank

SCRATCH_DB = "regtrace_retrieval_eval"


async def main() -> int:
    base = get_settings()
    owner = base.migration_database_url.rsplit("/", 1)[0]
    app_url = base.database_url.rsplit("/", 1)[0]
    conn = await asyncpg.connect(owner.replace("+asyncpg", "") + "/postgres")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {SCRATCH_DB}")
    finally:
        await conn.close()
    env = {**os.environ, "MIGRATION_DATABASE_URL": f"{owner}/{SCRATCH_DB}", "DATABASE_URL": f"{app_url}/{SCRATCH_DB}"}
    await asyncio.to_thread(
        subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True
    )

    settings = base.model_copy(update={"database_url": env["DATABASE_URL"], "worker_in_process": False})
    configure_logging(settings.model_copy(update={"log_level": "WARNING", "log_json": False}))
    data = Path(settings.data_dir or Path(__file__).resolve().parents[2] / "data")
    async with build_services(settings) as services:
        bank_id = await ensure_bank(services, "retrieval-eval")
        ingestion = IngestionService(services)
        for filename in dict.fromkeys(DOC_FILES.values()):
            path = data / "synthetic" / "regulations" / filename
            outcome = await ingestion.upload(
                bank_id=bank_id,
                user_id=None,
                filename=filename,
                data=path.read_bytes(),
                doc_class=DocClass.REGULATION,
                start_workflow=False,
            )
            await ingestion.parse_and_index(bank_id, outcome.document.id)
        result = await Evaluator(services, data, bank_id).retrieval()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
