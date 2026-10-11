"""Seed the SYNTHETIC demo bank through the real ingestion pipeline.

Usage (from backend/):
  python -m scripts.seed_demo              # bank docs + ITG, data-retention, KYC v1
  python -m scripts.seed_demo --with-v2    # also upload KYC v2 (normally done live in the demo)

Needs at least one LLM provider configured (GEMINI_API_KEY / GROQ_API_KEY / Ollama).
Responses are cached, so re-running costs no additional quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.container import build_services
from app.observability.logging import configure_logging
from app.services.seed import seed_demo

DEFAULT_DATA = Path(__file__).resolve().parents[2] / "data"


async def main(with_v2: bool, with_amendment: bool) -> int:
    settings = get_settings()
    DATA = Path(settings.data_dir) if settings.data_dir else DEFAULT_DATA
    configure_logging(settings.model_copy(update={"log_json": False}))
    if not (DATA / "synthetic" / "manifest.json").exists():
        print("Generate the corpus first: python -m scripts.generate_demo_data")
        return 1
    async with build_services(settings) as services:
        if not services.llm.available:
            print("WARNING: no LLM provider configured - LLM-dependent steps will fail and open review items.")
        result = await seed_demo(services, DATA, include_v2=with_v2, include_amendment=with_amendment)
    failed = [r for r in result.runs if r["status"] != "SUCCEEDED"]
    print(json.dumps({"bank_id": str(result.bank_id), "runs": len(result.runs), "failed": failed}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-v2", action="store_true")
    parser.add_argument("--with-amendment", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.with_v2, args.with_amendment)))
