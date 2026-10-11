"""Impact report builder (deterministic).

Assembles "what changed -> which obligations -> which controls -> which evidence ->
which gaps / risks / remediation -> which departments" from stored facts and graph
traversal. No LLM: every line of the report is a query result with its IDs.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import httpx
from sqlalchemy import select

from app.container import Services
from app.knowledge_graph.graph import traverse
from app.models import (
    ComplianceFinding,
    Control,
    Evidence,
    HumanReview,
    ImpactAssessment,
    Obligation,
    RegulatoryChange,
    RemediationAction,
    RiskAssessment,
)
from app.observability.logging import get_logger
from app.services import audit

_log = get_logger("regtrace.reporting")


async def build_impact_report(
    services: Services, bank_id: uuid.UUID, run_id: uuid.UUID, state: dict[str, Any]
) -> dict[str, Any]:
    regulation_id = _u(state.get("regulation_id"))
    version_id = _u(state.get("version_id"))
    previous_id = _u(state.get("previous_version_id"))
    obligation_ids = [uuid.UUID(i) for i in state.get("obligation_ids", [])]
    focus = [uuid.UUID(i) for i in state.get("changed_obligation_ids", [])] or obligation_ids

    async with services.db.tenant_session(bank_id) as session:
        changes = (
            (await session.scalars(select(RegulatoryChange).where(RegulatoryChange.to_version_id == version_id))).all()
            if version_id and previous_id
            else []
        )
        obligations = {
            o.id: o for o in (await session.scalars(select(Obligation).where(Obligation.id.in_(focus)))).all()
        }

        impacted_controls: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        impacted_evidence: set[uuid.UUID] = set()
        for oid in obligations:
            for edge in await traverse(
                session, ("obligation", oid), rels=["SATISFIED_BY", "GENERATES_EVIDENCE"], max_depth=2
            ):
                if edge.dst_type == "control":
                    impacted_controls[edge.dst_id].add(oid)
        controls = {
            c.id: c
            for c in (await session.scalars(select(Control).where(Control.id.in_(list(impacted_controls))))).all()
        }
        evidence = (await session.scalars(select(Evidence).where(Evidence.control_id.in_(list(controls))))).all()
        impacted_evidence = {e.id for e in evidence}

        new_ids = [uuid.UUID(i) for i in state.get("new_finding_ids", [])]
        findings = (
            await session.execute(
                select(ComplianceFinding, RiskAssessment)
                .outerjoin(
                    RiskAssessment, (RiskAssessment.finding_id == ComplianceFinding.id) & RiskAssessment.is_current
                )
                .where(
                    ComplianceFinding.bank_id == bank_id,
                    (ComplianceFinding.id.in_(new_ids)) | (ComplianceFinding.obligation_id.in_(list(obligations))),
                )
            )
        ).all()
        remediation = (
            await session.scalars(
                select(RemediationAction).where(
                    RemediationAction.bank_id == bank_id, RemediationAction.finding_id.in_([f.id for f, _ in findings])
                )
            )
        ).all()

        units = sorted(
            {c.business_unit or "Unassigned" for c in controls.values()}
            | {o.responsible_function for o in obligations.values() if o.responsible_function}
        )
        report = {
            "regulation_id": str(regulation_id) if regulation_id else None,
            "version_id": str(version_id) if version_id else None,
            "previous_version_id": str(previous_id) if previous_id else None,
            "relationship": state.get("relationship"),
            "changes": [
                {
                    "section_path": c.section_path,
                    "change_type": c.change_type,
                    "category": c.semantic_category,
                    "is_material": c.is_material,
                    "summary": c.summary,
                    "diff": c.diff[:10],
                }
                for c in changes
            ],
            "obligations": [
                {
                    "id": str(o.id),
                    "section_path": o.section_path,
                    "type": o.obligation_type,
                    "requirement": o.requirement_text,
                    "quote": o.source_quote,
                    "frequency": (o.frequency or {}).get("period"),
                    "confidence": o.confidence,
                }
                for o in obligations.values()
            ],
            "impacted_controls": [
                {
                    "id": str(c.id),
                    "control_key": c.control_key,
                    "name": c.name,
                    "owner": c.owner,
                    "business_unit": c.business_unit,
                    "via_obligations": [
                        obligations[o].section_path for o in impacted_controls[c.id] if o in obligations
                    ],
                }
                for c in controls.values()
            ],
            "impacted_evidence": len(impacted_evidence),
            "findings": [
                {
                    "id": str(f.id),
                    "gap_type": f.gap_type,
                    "title": f.title,
                    "status": f.status,
                    "section_path": f.details.get("section_path"),
                    "control_key": f.details.get("control_key"),
                    "risk_score": r.score if r else None,
                    "priority": r.priority if r else None,
                    "is_new": f.id in set(new_ids),
                }
                for f, r in sorted(findings, key=lambda fr: -(fr[1].score if fr[1] else 0))
            ],
            "remediation": [
                {
                    "id": str(a.id),
                    "finding_id": str(a.finding_id),
                    "priority": a.priority,
                    "status": a.status,
                    "owner": a.owner,
                    "due_date": a.due_date.isoformat() if a.due_date else None,
                    "requires_approval": a.requires_approval,
                    "root_cause": a.plan.get("root_cause"),
                }
                for a in remediation
            ],
            "impacted_business_units": units,
            "resolved_findings": state.get("resolved_findings", 0),
            "step_metrics": {
                k: state.get(k) for k in ("extraction", "mapping", "evidence", "lineage") if state.get(k) is not None
            },
        }
        material = [c for c in report["changes"] if c["is_material"]]
        new_findings = [f for f in report["findings"] if f["is_new"]]
        report["summary"] = (
            (
                f"{len(material)} material change(s) detected ({len(report['changes']) - len(material)} editorial). "
                if changes
                else f"{len(report['obligations'])} obligation(s) processed. "
            )
            + f"{len(report['impacted_controls'])} control(s) and {len(impacted_evidence)} evidence item(s) impacted; "
            + f"{len(new_findings)} new gap(s) "
            + f"({sum(1 for f in new_findings if f['priority'] == 'P1')} P1); "
            + f"{len(report['remediation'])} remediation plan(s) proposed; "
            + f"business units: {', '.join(units) or 'none'}."
        )
        assessment = ImpactAssessment(
            bank_id=bank_id,
            run_id=run_id,
            regulation_id=regulation_id,
            from_version_id=previous_id,
            to_version_id=version_id,
            report=report,
            summary=report["summary"],
        )
        session.add(assessment)
        await session.flush()
        if changes or new_findings:
            session.add(
                HumanReview(
                    bank_id=bank_id,
                    subject_type="IMPACT_REPORT",
                    subject_id=assessment.id,
                    reason=f"Regulatory impact analysis ready: {report['summary']}"[:2000],
                    priority="HIGH" if any(f["priority"] == "P1" for f in new_findings) else "NORMAL",
                    run_id=run_id,
                )
            )
        await audit.record(
            session,
            bank_id=bank_id,
            actor=audit.Actor.agent("supervisor"),
            action="impact.reported",
            subject_type="impact_assessment",
            subject_id=assessment.id,
            run_id=run_id,
            details={"summary": report["summary"]},
        )
        report["impact_assessment_id"] = str(assessment.id)

    await _notify(services, report)
    return report


async def _notify(services: Services, report: dict[str, Any]) -> None:
    url = services.settings.notify_webhook_url
    if not url:
        return
    try:
        await services.http.post(
            url,
            json={
                "text": f"RegTrace impact report: {report['summary']}",
                "impact_assessment_id": report.get("impact_assessment_id"),
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:  # notification failure must not fail the analysis
        _log.warning("notify.failed", error=str(exc))


def _u(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None
