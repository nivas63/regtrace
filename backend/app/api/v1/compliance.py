from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api import serializers as S
from app.api.deps import Analyst, Reader, ServicesDep
from app.core.errors import ConflictError, NotFoundError
from app.models import (
    ComplianceFinding,
    Control,
    ControlAssessment,
    HumanReview,
    ImpactAssessment,
    LLMCall,
    Obligation,
    ObligationApplicability,
    Regulation,
    RegulatoryChange,
    RegulatoryConflict,
    RemediationAction,
    RiskAssessment,
    WorkflowRun,
)
from app.services import audit

router = APIRouter(tags=["compliance"])


@router.post("/compliance/assess", status_code=202)
async def assess(services: ServicesDep, principal: Analyst) -> dict[str, Any]:
    run = await services.engine.enqueue(
        bank_id=principal.bank_id,
        workflow_type="compliance_assessment",
        input={"scope": "all"},
        triggered_by=f"user:{principal.user_id}",
    )
    return {"run_id": str(run.id)}


@router.get("/compliance/findings")
async def findings(
    services: ServicesDep,
    principal: Reader,
    status: str = "OPEN",
    gap_type: str | None = None,
    priority: str | None = None,
) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        query = (
            select(ComplianceFinding, RiskAssessment, Obligation, Regulation.title, Control)
            .outerjoin(RiskAssessment, (RiskAssessment.finding_id == ComplianceFinding.id) & RiskAssessment.is_current)
            .join(Obligation, Obligation.id == ComplianceFinding.obligation_id)
            .join(Regulation, Regulation.id == Obligation.regulation_id)
            .outerjoin(Control, Control.id == ComplianceFinding.control_id)
            .where(ComplianceFinding.bank_id == principal.bank_id)
        )
        if status != "ALL":
            query = query.where(ComplianceFinding.status == status)
        if gap_type:
            query = query.where(ComplianceFinding.gap_type == gap_type)
        if priority:
            query = query.where(RiskAssessment.priority == priority)
        rows = (await session.execute(query.order_by(RiskAssessment.score.desc().nulls_last()))).all()
        remediation = {
            r.finding_id: r
            for r in (
                await session.scalars(select(RemediationAction).where(RemediationAction.bank_id == principal.bank_id))
            ).all()
        }
    return [
        {
            **S.dump(f, S.FINDING),
            "risk": S.dump(r, S.RISK) if r else None,
            "obligation": S.dump(
                o, ("id", "section_path", "requirement_text", "source_quote", "obligation_type", "responsible_function")
            ),
            "regulation_title": title,
            "control": S.dump(c, ("id", "control_key", "name", "owner", "business_unit")) if c else None,
            "remediation": S.dump(remediation[f.id], ("id", "status", "priority", "due_date", "owner"))
            if f.id in remediation
            else None,
        }
        for f, r, o, title, c in rows
    ]


@router.get("/compliance/findings/{finding_id}")
async def finding(finding_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        f = await session.get(ComplianceFinding, finding_id)
        if f is None:
            raise NotFoundError("Finding not found")
        risk = await session.scalar(
            select(RiskAssessment).where(RiskAssessment.finding_id == finding_id, RiskAssessment.is_current.is_(True))
        )
        obligation = await session.get(Obligation, f.obligation_id)
        control = await session.get(Control, f.control_id) if f.control_id else None
        assessment = (
            await session.scalar(
                select(ControlAssessment).where(
                    ControlAssessment.control_id == f.control_id,
                    ControlAssessment.obligation_id == f.obligation_id,
                    ControlAssessment.is_current.is_(True),
                )
            )
            if f.control_id
            else None
        )
        actions = (
            await session.scalars(select(RemediationAction).where(RemediationAction.finding_id == finding_id))
        ).all()
    return {
        **S.dump(f, S.FINDING),
        "risk": S.dump(risk, S.RISK) if risk else None,
        "obligation": S.dump(obligation, S.OBLIGATION) if obligation else None,
        "control": S.dump(control, S.CONTROL) if control else None,
        "control_assessment": S.dump(assessment, S.CONTROL_ASSESSMENT) if assessment else None,
        "remediation": [S.dump(a, S.REMEDIATION) for a in actions],
    }


@router.get("/risk/heatmap")
async def heatmap(services: ServicesDep, principal: Reader) -> dict[str, Any]:
    """Open gaps by business unit x priority, and by obligation type x gap type."""
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (
            await session.execute(
                select(
                    ComplianceFinding.gap_type,
                    RiskAssessment.priority,
                    RiskAssessment.score,
                    Obligation.obligation_type,
                    Control.business_unit,
                    Obligation.responsible_function,
                )
                .join(RiskAssessment, (RiskAssessment.finding_id == ComplianceFinding.id) & RiskAssessment.is_current)
                .join(Obligation, Obligation.id == ComplianceFinding.obligation_id)
                .outerjoin(Control, Control.id == ComplianceFinding.control_id)
                .where(ComplianceFinding.bank_id == principal.bank_id, ComplianceFinding.status == "OPEN")
            )
        ).all()
    by_unit: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for gap, prio, _score, otype, unit, function in rows:
        by_unit[unit or function or "Unassigned"][prio] += 1
        by_type[otype][gap] += 1
    return {
        "by_business_unit": {k: dict(v) for k, v in by_unit.items()},
        "by_obligation_type": {k: dict(v) for k, v in by_type.items()},
        "total_open": len(rows),
    }


@router.get("/remediation")
async def remediation(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (
            await session.execute(
                select(RemediationAction, ComplianceFinding, Obligation.section_path, Regulation.title)
                .join(ComplianceFinding, ComplianceFinding.id == RemediationAction.finding_id)
                .join(Obligation, Obligation.id == ComplianceFinding.obligation_id)
                .join(Regulation, Regulation.id == Obligation.regulation_id)
                .where(RemediationAction.bank_id == principal.bank_id)
                .order_by(RemediationAction.priority, RemediationAction.due_date)
            )
        ).all()
    return [
        {
            **S.dump(a, S.REMEDIATION),
            "finding": S.dump(f, ("id", "gap_type", "title", "status")),
            "section_path": path,
            "regulation_title": title,
        }
        for a, f, path, title in rows
    ]


class StatusUpdate(BaseModel):
    status: Literal["IN_PROGRESS", "DONE"]
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/remediation/{action_id}")
async def update_remediation(
    action_id: uuid.UUID, body: StatusUpdate, services: ServicesDep, principal: Analyst
) -> dict[str, Any]:
    """Execution tracking is separate from the AI recommendation: only APPROVED plans
    (or plans not requiring approval) can move to IN_PROGRESS / DONE."""
    async with services.db.tenant_session(principal.bank_id) as session:
        action = await session.get(RemediationAction, action_id)
        if action is None:
            raise NotFoundError("Remediation not found")
        if action.requires_approval and action.status not in ("APPROVED", "IN_PROGRESS"):
            raise ConflictError("This remediation requires approval before execution")
        action.status = body.status
        await audit.record(
            session,
            bank_id=principal.bank_id,
            actor=audit.Actor.user(principal.user_id),
            action=f"remediation.{body.status.lower()}",
            subject_type="remediation",
            subject_id=action_id,
            details={"note": body.note},
        )
        return S.dump(action, S.REMEDIATION)


@router.get("/dashboard/summary")
async def dashboard(services: ServicesDep, principal: Reader) -> dict[str, Any]:
    bank = principal.bank_id
    async with services.db.tenant_session(bank) as session:

        async def count(q: Any) -> int:
            return int(await session.scalar(select(func.count()).select_from(q.subquery())) or 0)

        obligations = await count(select(Obligation.id).where(Obligation.status == "ACTIVE"))
        applicable = await count(
            select(ObligationApplicability.id).where(
                ObligationApplicability.bank_id == bank, ObligationApplicability.decision == "APPLICABLE"
            )
        )
        controls = await count(select(Control.id).where(Control.bank_id == bank, Control.status == "ACTIVE"))
        priorities = S.pairs(
            (
                await session.execute(
                    select(RiskAssessment.priority, func.count())
                    .join(ComplianceFinding, ComplianceFinding.id == RiskAssessment.finding_id)
                    .where(
                        RiskAssessment.is_current, ComplianceFinding.status == "OPEN", ComplianceFinding.bank_id == bank
                    )
                    .group_by(RiskAssessment.priority)
                )
            ).all()
        )
        gap_types = S.pairs(
            (
                await session.execute(
                    select(ComplianceFinding.gap_type, func.count())
                    .where(ComplianceFinding.bank_id == bank, ComplianceFinding.status == "OPEN")
                    .group_by(ComplianceFinding.gap_type)
                )
            ).all()
        )
        ratings = S.pairs(
            (
                await session.execute(
                    select(ControlAssessment.rating, func.count())
                    .where(ControlAssessment.bank_id == bank, ControlAssessment.is_current)
                    .group_by(ControlAssessment.rating)
                )
            ).all()
        )
        covered = await count(
            select(Obligation.id).where(
                Obligation.status == "ACTIVE",
                ~Obligation.id.in_(
                    select(ComplianceFinding.obligation_id).where(
                        ComplianceFinding.bank_id == bank, ComplianceFinding.status == "OPEN"
                    )
                ),
            )
        )
        pending_reviews = await count(
            select(HumanReview.id).where(HumanReview.bank_id == bank, HumanReview.status == "PENDING")
        )
        conflicts = await count(select(RegulatoryConflict.id).where(RegulatoryConflict.review_status == "PENDING"))
        material_changes = await count(select(RegulatoryChange.id).where(RegulatoryChange.is_material))
        remediation = S.pairs(
            (
                await session.execute(
                    select(RemediationAction.status, func.count())
                    .where(RemediationAction.bank_id == bank)
                    .group_by(RemediationAction.status)
                )
            ).all()
        )
        recent_impacts = (
            await session.scalars(
                select(ImpactAssessment)
                .where(ImpactAssessment.bank_id == bank)
                .order_by(ImpactAssessment.created_at.desc())
                .limit(5)
            )
        ).all()
        recent_runs = (
            await session.scalars(
                select(WorkflowRun).where(WorkflowRun.bank_id == bank).order_by(WorkflowRun.created_at.desc()).limit(8)
            )
        ).all()
    async with services.db.system_session() as session:
        llm = (
            await session.execute(
                select(
                    func.count(),
                    func.sum(LLMCall.input_tokens + LLMCall.output_tokens),
                    func.sum(LLMCall.equivalent_cost_usd),
                    func.count().filter(LLMCall.cache_hit.is_(True)),
                )
            )
        ).one()
    return {
        "obligations": obligations,
        "applicable_obligations": applicable,
        "controls": controls,
        "obligations_without_open_gaps": covered,
        "compliance_coverage": round(covered / obligations, 3) if obligations else None,
        "open_gaps_by_priority": priorities,
        "open_gaps_by_type": gap_types,
        "control_ratings": ratings,
        "pending_reviews": pending_reviews,
        "pending_conflicts": conflicts,
        "material_changes": material_changes,
        "remediation_by_status": remediation,
        "recent_impacts": [S.dump(i, S.IMPACT) for i in recent_impacts],
        "recent_runs": [
            S.dump(r, ("id", "workflow_type", "status", "current_step", "created_at", "finished_at"))
            for r in recent_runs
        ],
        "llm": {
            "calls": llm[0],
            "tokens": int(llm[1] or 0),
            "equivalent_cost_usd": round(float(llm[2] or 0), 4),
            "cache_hits": llm[3],
            "actual_cost_usd": 0.0,
        },
        "disclaimer": "AI-assisted compliance assessment. Not legal advice; decisions require human review.",
    }
