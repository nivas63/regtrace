from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.api import serializers as S
from app.api.deps import Analyst, Reader, ServicesDep
from app.core.errors import NotFoundError
from app.models import (
    ComplianceFinding,
    Control,
    ControlObligation,
    Document,
    Obligation,
    ObligationApplicability,
    ObligationLineage,
    Regulation,
    RegulationVersion,
    RegulatoryChange,
    Section,
)

router = APIRouter(tags=["regulations"])


@router.get("/regulations")
async def list_regulations(services: ServicesDep, principal: Reader) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        regulations = (await session.scalars(select(Regulation).order_by(Regulation.created_at))).all()
        versions = (await session.scalars(select(RegulationVersion).order_by(RegulationVersion.version_no))).all()
        counts = S.pairs(
            (
                await session.execute(
                    select(Obligation.regulation_id, func.count())
                    .where(Obligation.status == "ACTIVE")
                    .group_by(Obligation.regulation_id)
                )
            ).all()
        )
        changes = S.pairs(
            (
                await session.execute(
                    select(RegulatoryChange.regulation_id, func.count())
                    .where(RegulatoryChange.is_material)
                    .group_by(RegulatoryChange.regulation_id)
                )
            ).all()
        )
    by_reg: dict[uuid.UUID, list[RegulationVersion]] = {}
    for v in versions:
        by_reg.setdefault(v.regulation_id, []).append(v)
    return [
        {
            **S.dump(r, S.REGULATION),
            "versions": [S.dump(v, S.VERSION) for v in by_reg.get(r.id, [])],
            "active_obligations": counts.get(r.id, 0),
            "material_changes": changes.get(r.id, 0),
        }
        for r in regulations
    ]


@router.get("/regulations/{regulation_id}")
async def get_regulation(
    regulation_id: uuid.UUID, services: ServicesDep, principal: Reader, as_of: date | None = None
) -> dict[str, Any]:
    """Regulation with the version in force on `as_of` (default: today) and its sections."""
    as_of = as_of or date.today()
    async with services.db.tenant_session(principal.bank_id) as session:
        regulation = await session.get(Regulation, regulation_id)
        if regulation is None:
            raise NotFoundError("Regulation not found")
        versions = (
            await session.scalars(
                select(RegulationVersion)
                .where(RegulationVersion.regulation_id == regulation_id)
                .order_by(RegulationVersion.version_no)
            )
        ).all()
        in_force = next(
            (
                v
                for v in reversed(versions)
                if (v.effective_from is None or v.effective_from <= as_of)
                and (v.effective_to is None or v.effective_to > as_of)
            ),
            versions[-1] if versions else None,
        )
        sections = (
            (
                await session.scalars(
                    select(Section).where(Section.version_id == in_force.id).order_by(Section.ordinal)
                )
            ).all()
            if in_force
            else []
        )
        obligations = (
            (await session.scalars(select(Obligation).where(Obligation.version_id == in_force.id))).all()
            if in_force
            else []
        )
        document = await session.get(Document, in_force.document_id) if in_force else None
    by_section: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for o in obligations:
        by_section.setdefault(o.section_id, []).append(
            S.dump(
                o,
                (
                    "id",
                    "requirement_text",
                    "char_start",
                    "char_end",
                    "obligation_type",
                    "modality",
                    "confidence",
                    "status",
                    "review_status",
                ),
            )
        )
    return {
        **S.dump(regulation, S.REGULATION),
        "as_of": as_of.isoformat(),
        "versions": [S.dump(v, S.VERSION) for v in versions],
        "version_in_force": S.dump(in_force, S.VERSION) if in_force else None,
        "document": S.dump(document, S.DOCUMENT) if document else None,
        "sections": [{**S.dump(s, S.SECTION), "obligations": by_section.get(s.id, [])} for s in sections],
    }


@router.get("/regulations/{regulation_id}/changes")
async def regulation_changes(
    regulation_id: uuid.UUID, services: ServicesDep, principal: Reader
) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        changes = (
            await session.scalars(
                select(RegulatoryChange)
                .where(RegulatoryChange.regulation_id == regulation_id)
                .order_by(RegulatoryChange.created_at)
            )
        ).all()
    return [S.dump(c, S.CHANGE) for c in changes]


@router.post("/regulations/{regulation_id}/analyze", status_code=202)
async def analyze_regulation(regulation_id: uuid.UUID, services: ServicesDep, principal: Analyst) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        ids = (
            await session.scalars(
                select(Obligation.id).where(Obligation.regulation_id == regulation_id, Obligation.status == "ACTIVE")
            )
        ).all()
    run = await services.engine.enqueue(
        bank_id=principal.bank_id,
        workflow_type="compliance_assessment",
        input={"regulation_id": str(regulation_id)},
        triggered_by=f"user:{principal.user_id}",
        state={"obligation_ids": [str(i) for i in ids]},
    )
    return {"run_id": str(run.id), "obligations": len(ids)}


@router.get("/obligations")
async def list_obligations(
    services: ServicesDep,
    principal: Reader,
    regulation_id: uuid.UUID | None = None,
    status: str = "ACTIVE",
    obligation_type: str | None = None,
    review_status: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        query = (
            select(Obligation, Regulation.title, Regulation.regulator)
            .join(Regulation, Regulation.id == Obligation.regulation_id)
            .where(Obligation.status == status)
        )
        if regulation_id:
            query = query.where(Obligation.regulation_id == regulation_id)
        if obligation_type:
            query = query.where(Obligation.obligation_type == obligation_type)
        if review_status:
            query = query.where(Obligation.review_status == review_status)
        if q:
            like = f"%{q.lower()}%"
            query = query.where(
                or_(func.lower(Obligation.requirement_text).like(like), func.lower(Obligation.source_quote).like(like))
            )
        rows = (await session.execute(query.order_by(Regulation.title, Obligation.section_path).limit(1000))).all()
        applicability = S.pairs(
            (
                await session.execute(
                    select(ObligationApplicability.obligation_id, ObligationApplicability.decision).where(
                        ObligationApplicability.bank_id == principal.bank_id
                    )
                )
            ).all()
        )
        mapped = S.pairs(
            (
                await session.execute(
                    select(ControlObligation.obligation_id, func.count())
                    .where(
                        ControlObligation.bank_id == principal.bank_id,
                        ControlObligation.mapping_type != "NONE",
                        ControlObligation.review_status != "REJECTED",
                    )
                    .group_by(ControlObligation.obligation_id)
                )
            ).all()
        )
        open_gaps = S.pairs(
            (
                await session.execute(
                    select(ComplianceFinding.obligation_id, func.count())
                    .where(ComplianceFinding.bank_id == principal.bank_id, ComplianceFinding.status == "OPEN")
                    .group_by(ComplianceFinding.obligation_id)
                )
            ).all()
        )
    return [
        {
            **S.dump(o, S.OBLIGATION),
            "regulation_title": title,
            "regulator": regulator,
            "applicability": applicability.get(o.id),
            "mapped_controls": mapped.get(o.id, 0),
            "open_gaps": open_gaps.get(o.id, 0),
        }
        for o, title, regulator in rows
    ]


@router.get("/obligations/{obligation_id}")
async def get_obligation(obligation_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        obligation = await session.get(Obligation, obligation_id)
        if obligation is None:
            raise NotFoundError("Obligation not found")
        section = await session.get(Section, obligation.section_id)
        regulation = await session.get(Regulation, obligation.regulation_id)
        version = await session.get(RegulationVersion, obligation.version_id)
        mappings = (
            await session.execute(
                select(ControlObligation, Control)
                .join(Control, Control.id == ControlObligation.control_id)
                .where(ControlObligation.obligation_id == obligation_id, ControlObligation.bank_id == principal.bank_id)
            )
        ).all()
        applicability = await session.scalar(
            select(ObligationApplicability).where(
                ObligationApplicability.obligation_id == obligation_id,
                ObligationApplicability.bank_id == principal.bank_id,
            )
        )
        lineage = (
            await session.scalars(
                select(ObligationLineage).where(
                    or_(
                        ObligationLineage.prev_obligation_id == obligation_id,
                        ObligationLineage.next_obligation_id == obligation_id,
                    )
                )
            )
        ).all()
        findings = (
            await session.scalars(
                select(ComplianceFinding).where(
                    ComplianceFinding.obligation_id == obligation_id, ComplianceFinding.bank_id == principal.bank_id
                )
            )
        ).all()
    return {
        **S.dump(obligation, S.OBLIGATION),
        "section": S.dump(section, S.SECTION) if section else None,
        "regulation": S.dump(regulation, S.REGULATION) if regulation else None,
        "version": S.dump(version, S.VERSION) if version else None,
        "mappings": [
            {**S.dump(m, S.MAPPING), "control": S.dump(c, ("id", "control_key", "name", "frequency", "owner"))}
            for m, c in mappings
        ],
        "applicability_decision": S.dump(
            applicability, ("decision", "decided_by", "rationale", "confidence", "review_status")
        )
        if applicability
        else None,
        "lineage": [
            S.dump(line, ("prev_obligation_id", "next_obligation_id", "change_type", "similarity")) for line in lineage
        ],
        "findings": [S.dump(f, S.FINDING) for f in findings],
    }
