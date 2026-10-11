from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api import serializers as S
from app.api.deps import Analyst, AuditReader, Reader, Reviewer, ServicesDep
from app.core.errors import ConflictError, NotFoundError
from app.knowledge_graph.graph import subgraph
from app.models import AuditLog, EvalRun, HumanReview, LLMCall, ToolCall, WorkflowRun, WorkflowStep
from app.services import audit, reviews

router = APIRouter(tags=["governance"])


@router.get("/agent-runs")
async def list_runs(services: ServicesDep, principal: Reader, limit: int = 50) -> list[dict[str, Any]]:
    async with services.db.system_session() as session:
        rows = (
            await session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.bank_id == principal.bank_id)
                .order_by(WorkflowRun.created_at.desc())
                .limit(min(limit, 200))
            )
        ).all()
    return [S.dump(r, S.RUN) for r in rows]


@router.get("/agent-runs/{run_id}")
async def get_run(run_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    """Full execution trace: plan, steps (attempts, decisions, outputs), tool calls, LLM calls."""
    async with services.db.system_session() as session:
        run = await session.get(WorkflowRun, run_id)
        if run is None or run.bank_id != principal.bank_id:
            raise NotFoundError("Run not found")
        steps = (
            await session.scalars(
                select(WorkflowStep).where(WorkflowStep.run_id == run_id).order_by(WorkflowStep.started_at)
            )
        ).all()
        tools = (
            await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at))
        ).all()
        llm = (
            await session.scalars(select(LLMCall).where(LLMCall.run_id == run_id).order_by(LLMCall.created_at))
        ).all()
    return {
        **S.dump(run, S.RUN),
        "steps": [S.dump(s, S.STEP) for s in steps],
        "tool_calls": [S.dump(t, S.TOOL_CALL) for t in tools],
        "llm_calls": [S.dump(c, S.LLM_CALL) for c in llm],
        "totals": {
            "llm_calls": len(llm),
            "cache_hits": sum(c.cache_hit for c in llm),
            "tokens": sum(c.input_tokens + c.output_tokens for c in llm),
            "equivalent_cost_usd": round(sum(c.equivalent_cost_usd for c in llm), 5),
            "tool_calls": len(tools),
        },
    }


@router.post("/agent-runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: uuid.UUID, services: ServicesDep, principal: Analyst) -> dict[str, Any]:
    """Resume a failed run from its last completed step (e.g. after provider quota recovers)."""
    async with services.db.system_session() as session:
        run = await session.get(WorkflowRun, run_id)
        if run is None or run.bank_id != principal.bank_id:
            raise NotFoundError("Run not found")
    if not await services.engine.retry(run_id):
        raise ConflictError("Only FAILED runs can be retried")
    async with services.db.tenant_session(principal.bank_id) as session:
        await audit.record(
            session,
            bank_id=principal.bank_id,
            actor=audit.Actor.user(principal.user_id),
            action="workflow.retried",
            subject_type="workflow_run",
            subject_id=run_id,
            run_id=run_id,
        )
    return {"run_id": str(run_id), "status": "QUEUED"}


@router.get("/reviews")
async def list_reviews(
    services: ServicesDep, principal: Reader, status: str = "PENDING", subject_type: str | None = None
) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        query = select(HumanReview).where(HumanReview.bank_id == principal.bank_id)
        if status != "ALL":
            query = query.where(HumanReview.status == status)
        if subject_type:
            query = query.where(HumanReview.subject_type == subject_type)
        rows = (
            await session.scalars(query.order_by(HumanReview.priority, HumanReview.created_at.desc()).limit(500))
        ).all()
    return [S.dump(r, S.REVIEW) for r in rows]


class DecisionRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT", "MODIFY"]
    comment: str | None = Field(default=None, max_length=2000)
    modification: dict[str, Any] | None = None


@router.post("/reviews/{review_id}/decision")
async def decide(
    review_id: uuid.UUID, body: DecisionRequest, services: ServicesDep, principal: Reviewer
) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        review = await reviews.decide(session, principal, review_id, body.decision, body.comment, body.modification)
        result = S.dump(review, S.REVIEW)
    if review.subject_type == "AMENDMENT" and review.run_id:
        await services.engine.resume(review.run_id)
    return result


@router.get("/audit")
async def audit_log(
    services: ServicesDep,
    principal: AuditReader,
    subject_id: str | None = None,
    action: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        query = select(AuditLog)
        if subject_id:
            query = query.where(AuditLog.subject_id == subject_id)
        if action:
            query = query.where(AuditLog.action.like(f"{action}%"))
        rows = (await session.scalars(query.order_by(AuditLog.id.desc()).limit(min(limit, 1000)))).all()
    return [S.dump(r, S.AUDIT) for r in rows]


@router.get("/audit/verify")
async def verify(services: ServicesDep, principal: AuditReader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        bank_chain = await audit.verify_chain(session, principal.bank_id)
        system_chain = await audit.verify_chain(session, None)
    return {"bank_chain": bank_chain.__dict__, "system_chain": system_chain.__dict__}


@router.get("/graph")
async def graph(services: ServicesDep, principal: Reader, node_id: uuid.UUID) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        first = await subgraph(session, [node_id])
        neighbours = {uuid.UUID(e["src_id"]) for e in first} | {uuid.UUID(e["dst_id"]) for e in first}
        edges = await subgraph(session, list(neighbours | {node_id}))
    return {"root": str(node_id), "edges": edges}


@router.get("/observability/llm")
async def llm_observability(services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.system_session() as session:
        rows = (
            await session.execute(
                select(
                    LLMCall.purpose,
                    LLMCall.provider,
                    LLMCall.outcome,
                    func.count(),
                    func.sum(LLMCall.input_tokens),
                    func.sum(LLMCall.output_tokens),
                    func.avg(LLMCall.latency_ms),
                    func.count().filter(LLMCall.cache_hit),
                    func.sum(LLMCall.equivalent_cost_usd),
                    func.sum(LLMCall.repairs),
                ).group_by(LLMCall.purpose, LLMCall.provider, LLMCall.outcome)
            )
        ).all()
        tools = (
            await session.execute(
                select(ToolCall.tool, ToolCall.status, func.count(), func.avg(ToolCall.latency_ms)).group_by(
                    ToolCall.tool, ToolCall.status
                )
            )
        ).all()
        steps = (
            await session.execute(
                select(
                    WorkflowStep.step_name, WorkflowStep.status, func.count(), func.avg(WorkflowStep.duration_ms)
                ).group_by(WorkflowStep.step_name, WorkflowStep.status)
            )
        ).all()
        runs = S.pairs(
            (
                await session.execute(
                    select(WorkflowRun.status, func.count())
                    .where(WorkflowRun.bank_id == principal.bank_id)
                    .group_by(WorkflowRun.status)
                )
            ).all()
        )
    router_view = services.llm_stack.router
    return {
        "llm": [
            {
                "purpose": r[0],
                "provider": r[1],
                "outcome": r[2],
                "calls": r[3],
                "input_tokens": int(r[4] or 0),
                "output_tokens": int(r[5] or 0),
                "avg_latency_ms": round(float(r[6] or 0), 1),
                "cache_hits": r[7],
                "equivalent_cost_usd": round(float(r[8] or 0), 5),
                "repairs": int(r[9] or 0),
            }
            for r in rows
        ],
        "tools": [
            {"tool": t[0], "status": t[1], "calls": t[2], "avg_latency_ms": round(float(t[3] or 0), 1)} for t in tools
        ],
        "steps": [
            {"step": s[0], "status": s[1], "count": s[2], "avg_duration_ms": round(float(s[3] or 0), 1)} for s in steps
        ],
        "runs_by_status": runs,
        "providers": sorted(router_view.providers),
        "tool_catalogue": services.tools.catalogue(),
        "actual_cost_usd": 0.0,
    }


@router.get("/eval/runs")
async def eval_runs(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.system_session() as session:
        rows = (await session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(50))).all()
    return [S.dump(r, ("id", "suite", "dataset_version", "metrics", "details", "notes", "created_at")) for r in rows]
