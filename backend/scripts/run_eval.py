"""Run the evaluation harness against the current database state.

Usage (from backend/):  python -m scripts.run_eval [--notes "..."]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.container import build_services
from app.evaluation.harness import Evaluator
from app.models import Bank
from app.observability.logging import configure_logging
from app.services.seed import BANK_NAME

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"


async def main(notes: str | None) -> int:
    settings = get_settings()
    DATA = Path(settings.data_dir) if settings.data_dir else DEFAULT_DATA
    configure_logging(settings.model_copy(update={"log_level": "WARNING", "log_json": False}))
    async with build_services(settings) as services:
        async with services.db.system_session() as session:
            bank_id = await session.scalar(select(Bank.id).where(Bank.name == BANK_NAME))
        if bank_id is None:
            print("Demo bank not found. Run: python -m scripts.seed_demo")
            return 1
        results = await Evaluator(services, DATA, bank_id).run_all(notes)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes", default=None)
    sys.exit(asyncio.run(main(parser.parse_args().notes)))
