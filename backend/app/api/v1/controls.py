from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.api import serializers as S
from app.api.deps import Reader, ServicesDep
from app.core.errors import NotFoundError
from app.models import (
    Control,
    ControlAssessment,
    ControlObligation,
    ControlTest,
    Evidence,
    EvidenceAssessment,
    Obligation,
    Regulation,
)

router = APIRouter(tags=["controls"])


@router.get("/controls")
async def list_controls(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        controls = (
            await session.scalars(select(Control).where(Control.status == "ACTIVE").order_by(Control.control_key))
        ).all()
        overall: dict[uuid.UUID, ControlAssessment] = {}
        for a in (
            await session.scalars(
                select(ControlAssessment)
                .where(ControlAssessment.is_current.is_(True))
                .order_by(ControlAssessment.score)
            )
        ).all():
            overall.setdefault(a.control_id, a)  # weakest current assessment per control
        mapped: dict[uuid.UUID, int] = {}
        for m in (
            await session.scalars(
                select(ControlObligation).where(
                    ControlObligation.mapping_type != "NONE", ControlObligation.review_status != "REJECTED"
                )
            )
        ).all():
            mapped[m.control_id] = mapped.get(m.control_id, 0) + 1
        evidence: dict[uuid.UUID, int] = {}
        for e in (await session.scalars(select(Evidence))).all():
            if e.control_id:
                evidence[e.control_id] = evidence.get(e.control_id, 0) + 1
    return [
        {
            **S.dump(c, S.CONTROL),
            "mapped_obligations": mapped.get(c.id, 0),
            "evidence_items": evidence.get(c.id, 0),
            "assessment": S.dump(overall[c.id], S.CONTROL_ASSESSMENT) if c.id in overall else None,
        }
        for c in controls
    ]


@router.get("/controls/{control_id}")
async def get_control(control_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        control = await session.get(Control, control_id)
        if control is None:
            raise NotFoundError("Control not found")
        assessments = (
            await session.scalars(
                select(ControlAssessment).where(
                    ControlAssessment.control_id == control_id, ControlAssessment.is_current.is_(True)
                )
            )
        ).all()
        tests = (
            await session.scalars(
                select(ControlTest).where(ControlTest.control_id == control_id).order_by(ControlTest.tested_on.desc())
            )
        ).all()
        evidence = (await session.scalars(select(Evidence).where(Evidence.control_id == control_id))).all()
        judgements = (
            await session.scalars(
                select(EvidenceAssessment)
                .where(EvidenceAssessment.control_id == control_id)
                .order_by(EvidenceAssessment.created_at.desc())
                .limit(40)
            )
        ).all()
        mappings = (
            await session.execute(
                select(ControlObligation, Obligation, Regulation.title)
                .join(Obligation, Obligation.id == ControlObligation.obligation_id)
                .join(Regulation, Regulation.id == Obligation.regulation_id)
                .where(ControlObligation.control_id == control_id, Obligation.status == "ACTIVE")
            )
        ).all()
    return {
        **S.dump(control, S.CONTROL),
        "assessments": [S.dump(a, S.CONTROL_ASSESSMENT) for a in assessments],
        "tests": [S.dump(t, S.TEST) for t in tests],
        "evidence": [S.dump(e, S.EVIDENCE) for e in evidence],
        "evidence_judgements": [S.dump(j, S.EVIDENCE_ASSESSMENT) for j in judgements],
        "mappings": [
            {
                **S.dump(m, S.MAPPING),
                "obligation": S.dump(o, ("id", "section_path", "requirement_text", "source_quote", "frequency")),
                "regulation_title": title,
            }
            for m, o, title in mappings
        ],
    }


@router.get("/mappings")
async def mapping_matrix(services: ServicesDep, principal: Reader, include_none: bool = False) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        query = (
            select(ControlObligation, Obligation, Control, Regulation.title)
            .join(Obligation, Obligation.id == ControlObligation.obligation_id)
            .join(Control, Control.id == ControlObligation.control_id)
            .join(Regulation, Regulation.id == Obligation.regulation_id)
            .where(Obligation.status == "ACTIVE")
        )
        if not include_none:
            query = query.where(ControlObligation.mapping_type != "NONE")
        rows = (await session.execute(query)).all()
    return {
        "mappings": [
            {
                **S.dump(m, S.MAPPING),
                "obligation": S.dump(o, ("id", "section_path", "requirement_text", "obligation_type")),
                "regulation_title": title,
                "control": S.dump(c, ("id", "control_key", "name")),
            }
            for m, o, c, title in rows
        ]
    }


@router.get("/evidence")
async def list_evidence(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        rows = (
            await session.execute(
                select(Evidence, Control.control_key)
                .outerjoin(Control, Control.id == Evidence.control_id)
                .order_by(Evidence.collected_on.desc())
            )
        ).all()
        verdicts: dict[uuid.UUID, dict[str, int]] = {}
        for j in (await session.scalars(select(EvidenceAssessment))).all():
            if j.evidence_id:
                counter = verdicts.setdefault(j.evidence_id, {})
                counter[j.verdict] = counter.get(j.verdict, 0) + 1
    return [{**S.dump(e, S.EVIDENCE), "control_key": key, "verdicts": verdicts.get(e.id, {})} for e, key in rows]
