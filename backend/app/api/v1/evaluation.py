from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.api.deps import Analyst, ServicesDep
from app.evaluation.harness import Evaluator

router = APIRouter(prefix="/eval", tags=["evaluation"])
DATA = Path(__file__).resolve().parents[4] / "data"


@router.post("/run")
async def run_evaluation(services: ServicesDep, principal: Analyst) -> dict[str, Any]:
    """Recompute all evaluation suites from the current database state (no LLM calls)."""
    data_dir = Path(services.settings.data_dir) if services.settings.data_dir else DATA
    return await Evaluator(services, data_dir, principal.bank_id).run_all(notes=f"triggered by {principal.email}")
