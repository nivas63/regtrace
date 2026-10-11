from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api import serializers as S
from app.api.deps import Analyst, Reader, ServicesDep
from app.core.errors import NotFoundError
from app.models import (
    ImpactAssessment,
    Obligation,
    ObligationCluster,
    ObligationClusterMember,
    Regulation,
    RegulatoryConflict,
    Simulation,
)
from app.rag.qa import answer_question

router = APIRouter(tags=["analysis"])


@router.get("/impact")
async def list_impact(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (
            await session.scalars(
                select(ImpactAssessment)
                .where(ImpactAssessment.bank_id == principal.bank_id)
                .order_by(ImpactAssessment.created_at.desc())
            )
        ).all()
    return [S.dump(r, S.IMPACT) for r in rows]


@router.get("/impact/{impact_id}")
async def get_impact(impact_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        row = await session.get(ImpactAssessment, impact_id)
        if row is None:
            raise NotFoundError("Impact assessment not found")
    return {**S.dump(row, S.IMPACT), "report": row.report}


class SimulationRequest(BaseModel):
    scenario: str = Field(min_length=10, max_length=1000)


@router.post("/simulation/run", status_code=202)
async def run_simulation(body: SimulationRequest, services: ServicesDep, principal: Analyst) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        simulation = Simulation(bank_id=principal.bank_id, scenario=body.scenario)
        session.add(simulation)
        await session.flush()
        simulation_id = simulation.id
    run = await services.engine.enqueue(
        bank_id=principal.bank_id,
        workflow_type="run_simulation",
        input={"simulation_id": str(simulation_id)},
        triggered_by=f"user:{principal.user_id}",
    )
    async with services.db.tenant_session(principal.bank_id) as session:
        row = await session.get(Simulation, simulation_id)
        if row is not None:
            row.run_id = run.id
    return {"simulation_id": str(simulation_id), "run_id": str(run.id)}


@router.get("/simulation")
async def list_simulations(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (await session.scalars(select(Simulation).order_by(Simulation.created_at.desc()).limit(50))).all()
    return [S.dump(r, S.SIMULATION) for r in rows]


@router.get("/simulation/{simulation_id}")
async def get_simulation(simulation_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        row = await session.get(Simulation, simulation_id)
        if row is None:
            raise NotFoundError("Simulation not found")
    return S.dump(row, S.SIMULATION)


@router.post("/contradictions/detect", status_code=202)
async def detect_contradictions(services: ServicesDep, principal: Analyst) -> dict[str, Any]:
    run = await services.engine.enqueue(
        bank_id=principal.bank_id,
        workflow_type="cross_regulation_analysis",
        input={},
        triggered_by=f"user:{principal.user_id}",
    )
    return {"run_id": str(run.id)}


async def _obligation_views(session: Any, ids: set[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    rows = (
        await session.execute(
            select(Obligation, Regulation)
            .join(Regulation, Regulation.id == Obligation.regulation_id)
            .where(Obligation.id.in_(ids))
        )
    ).all()
    return {
        o.id: {
            **S.dump(
                o, ("id", "section_path", "source_quote", "obligation_type", "modality", "effective_from", "status")
            ),
            "regulation": S.dump(r, ("id", "title", "regulator", "ref_no")),
        }
        for o, r in rows
    }


@router.get("/conflicts")
async def conflicts(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (await session.scalars(select(RegulatoryConflict).order_by(RegulatoryConflict.confidence.desc()))).all()
        views = await _obligation_views(session, {r.obligation_a_id for r in rows} | {r.obligation_b_id for r in rows})
    return [
        {
            **S.dump(r, S.CONFLICT),
            "rule_a": views.get(r.obligation_a_id),
            "rule_b": views.get(r.obligation_b_id),
            "label": "Potential conflict - human review required",
        }
        for r in rows
    ]


@router.get("/clusters")
async def clusters(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (await session.scalars(select(ObligationCluster).order_by(ObligationCluster.confidence.desc()))).all()
        members = (await session.scalars(select(ObligationClusterMember))).all()
        views = await _obligation_views(session, {m.obligation_id for m in members})
    by_cluster: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for m in members:
        by_cluster.setdefault(m.cluster_id, []).append(
            {**(views.get(m.obligation_id) or {}), "similarity": m.similarity}
        )
    return [{**S.dump(c, S.CLUSTER), "members": by_cluster.get(c.id, [])} for c in rows]


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@router.post("/query")
async def query(body: QueryRequest, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    result = await answer_question(services, principal.bank_id, principal.user_id, body.question)
    return {**result.__dict__, "disclaimer": "AI-assisted answer grounded in the cited sources. Not legal advice."}
