"""Deterministic assessment engines wired to the database.

score_controls  : ControlAssessment per (control, mapped obligation) using the
                  obligation's required frequency, plus an overall row per control.
identify_gaps   : per applicable obligation, reconcile detected gaps with open
                  findings (new -> OPEN, no longer detected -> RESOLVED: monitoring).
score_risk      : RiskAssessment for every open finding.

`load_facts` / `evaluate` are pure-ish helpers reused by the what-if simulator, so
simulation and reality always share exactly the same logic.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scoring import (
    FORMULA_VERSION,
    ControlFacts,
    ControlTestFacts,
    EffectivenessInput,
    EffectivenessResult,
    EvidenceJudgement,
    GapDraft,
    MappedControl,
    ObligationFacts,
    RiskInput,
    assess_effectiveness,
    identify_gaps,
    obligation_coverage,
    score_risk,
)
from app.knowledge_graph.graph import upsert_edge
from app.models import (
    ComplianceFinding,
    Control,
    ControlAssessment,
    ControlObligation,
    ControlTest,
    Evidence,
    EvidenceAssessment,
    Obligation,
    ObligationApplicability,
    RegulatoryConflict,
    RiskAssessment,
)


@dataclass
class AssessmentFacts:
    obligations: dict[uuid.UUID, Obligation]
    applicability: dict[uuid.UUID, str]
    controls: dict[uuid.UUID, Control]
    mappings: dict[uuid.UUID, list[ControlObligation]]
    tests: dict[uuid.UUID, list[ControlTestFacts]]
    judgements: dict[uuid.UUID, list[EvidenceJudgement]]
    evidence_counts: dict[uuid.UUID, int]
    conflicts: dict[uuid.UUID, int]
    as_of: date = field(default_factory=date.today)


async def load_facts(
    session: AsyncSession, bank_id: uuid.UUID, obligation_ids: list[uuid.UUID] | None = None
) -> AssessmentFacts:
    query = select(Obligation).where(Obligation.status == "ACTIVE")
    if obligation_ids is not None:
        query = query.where(Obligation.id.in_(obligation_ids))
    obligations = {o.id: o for o in (await session.scalars(query)).all()}
    applicability = {
        a.obligation_id: a.decision
        for a in (
            await session.scalars(
                select(ObligationApplicability).where(
                    ObligationApplicability.bank_id == bank_id,
                    ObligationApplicability.obligation_id.in_(list(obligations)),
                )
            )
        ).all()
    }
    mappings: dict[uuid.UUID, list[ControlObligation]] = defaultdict(list)
    for m in (
        await session.scalars(
            select(ControlObligation).where(
                ControlObligation.bank_id == bank_id,
                ControlObligation.obligation_id.in_(list(obligations)),
                ControlObligation.review_status != "REJECTED",
            )
        )
    ).all():
        mappings[m.obligation_id].append(m)
    control_ids = {m.control_id for ms in mappings.values() for m in ms}
    controls = {c.id: c for c in (await session.scalars(select(Control).where(Control.id.in_(control_ids)))).all()}
    tests: dict[uuid.UUID, list[ControlTestFacts]] = defaultdict(list)
    for t in (await session.scalars(select(ControlTest).where(ControlTest.control_id.in_(control_ids)))).all():
        tests[t.control_id].append(ControlTestFacts(t.tested_on, t.result, t.sample_size, t.exceptions))
    evidence = {
        e.id: e for e in (await session.scalars(select(Evidence).where(Evidence.control_id.in_(control_ids)))).all()
    }
    evidence_counts: dict[uuid.UUID, int] = defaultdict(int)
    for e in evidence.values():
        if e.control_id:
            evidence_counts[e.control_id] += 1
    # Latest evidence-assessment run per control only.
    latest: dict[uuid.UUID, uuid.UUID | None] = {}
    judgements: dict[uuid.UUID, list[EvidenceJudgement]] = defaultdict(list)
    rows = (
        await session.scalars(
            select(EvidenceAssessment)
            .where(EvidenceAssessment.control_id.in_(control_ids))
            .order_by(EvidenceAssessment.created_at.desc())
        )
    ).all()
    for row in rows:
        latest.setdefault(row.control_id, row.run_id)
        if row.run_id != latest[row.control_id]:
            continue
        ev = evidence.get(row.evidence_id) if row.evidence_id else None
        judgements[row.control_id].append(
            EvidenceJudgement(
                verdict=row.verdict,
                authenticity=ev.authenticity if ev else "SELF_ATTESTATION",
                quote_verified=row.quote_verified,
                collected_on=ev.collected_on if ev else None,
            )
        )
    conflicts: dict[uuid.UUID, int] = defaultdict(int)
    for c in (
        await session.scalars(
            select(RegulatoryConflict).where(RegulatoryConflict.review_status.in_(("PENDING", "APPROVED")))
        )
    ).all():
        conflicts[c.obligation_a_id] += 1
        conflicts[c.obligation_b_id] += 1
    return AssessmentFacts(
        obligations, applicability, controls, mappings, tests, judgements, evidence_counts, conflicts
    )


def effectiveness_for(facts: AssessmentFacts, control: Control, required_period: str | None) -> EffectivenessResult:
    return assess_effectiveness(
        EffectivenessInput(
            control=ControlFacts(control.owner, control.frequency, control.automation, control.procedure_ref),
            required_period=required_period,
            tests=facts.tests.get(control.id, []),
            judgements=facts.judgements.get(control.id, []),
            evidence_count=facts.evidence_counts.get(control.id, 0),
            as_of=facts.as_of,
        )
    )


@dataclass
class ObligationEvaluation:
    obligation: Obligation
    coverage: float
    controls: list[tuple[ControlObligation, EffectivenessResult]]
    gaps: list[GapDraft]


def evaluate(
    facts: AssessmentFacts, period_override: dict[uuid.UUID, str | None] | None = None
) -> list[ObligationEvaluation]:
    """Pure evaluation of every obligation in `facts` (used by assessment AND simulation)."""
    results = []
    for oid, obligation in facts.obligations.items():
        period = (period_override or {}).get(oid, (obligation.frequency or {}).get("period"))
        mapped: list[tuple[ControlObligation, EffectivenessResult]] = []
        view: list[MappedControl] = []
        for mapping in facts.mappings.get(oid, []):
            control = facts.controls.get(mapping.control_id)
            if control is None or mapping.mapping_type == "NONE":
                continue
            result = effectiveness_for(facts, control, period)
            mapped.append((mapping, result))
            view.append(
                MappedControl(
                    control_id=control.id,
                    control_key=control.control_key,
                    mapping_type=mapping.mapping_type,
                    coverage=mapping.coverage_score,
                    rating=result.rating.value,
                    score=result.score,
                    evidence=result.evidence,
                    recency=result.recency,
                    frequency_adequate=result.frequency_adequate,
                    operating=result.operating,
                    provisional=mapping.review_status != "APPROVED",
                )
            )
        coverage = obligation_coverage([(m.coverage_score, r.score) for m, r in mapped])
        gaps = identify_gaps(
            ObligationFacts(
                obligation_id=oid,
                applicability=facts.applicability.get(oid, "APPLICABLE"),
                ambiguous=bool(obligation.ambiguity_note),
                open_conflicts=facts.conflicts.get(oid, 0),
                controls=view,
            ),
            coverage,
        )
        results.append(ObligationEvaluation(obligation, coverage, mapped, gaps))
    return results


async def score_controls(
    session: AsyncSession, bank_id: uuid.UUID, run_id: uuid.UUID | None, obligation_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    facts = await load_facts(session, bank_id, obligation_ids)
    evaluations = evaluate(facts)
    touched: set[uuid.UUID] = {m.control_id for e in evaluations for m, _ in e.controls}
    if touched:
        await session.execute(
            update(ControlAssessment)
            .where(ControlAssessment.control_id.in_(touched), ControlAssessment.is_current.is_(True))
            .values(is_current=False)
        )
    ratings: dict[str, int] = defaultdict(int)
    for evaluation in evaluations:
        for mapping, result in evaluation.controls:
            ratings[result.rating.value] += 1
            session.add(
                ControlAssessment(
                    bank_id=bank_id,
                    run_id=run_id,
                    control_id=mapping.control_id,
                    obligation_id=evaluation.obligation.id,
                    as_of=facts.as_of,
                    design_score=result.design,
                    operating_score=result.operating,
                    evidence_score=result.evidence,
                    recency_score=result.recency,
                    score=result.score,
                    rating=result.rating.value,
                    factors={
                        **result.factors,
                        "reasons": result.reasons,
                        "frequency_adequate": result.frequency_adequate,
                        "last_activity": result.last_activity.isoformat() if result.last_activity else None,
                        "required_period": (evaluation.obligation.frequency or {}).get("period"),
                    },
                    formula_version=FORMULA_VERSION,
                    is_current=True,
                )
            )
    return {"assessments": sum(ratings.values()), "controls": len(touched), "ratings": dict(ratings)}


async def reconcile_gaps(
    session: AsyncSession, bank_id: uuid.UUID, run_id: uuid.UUID | None, obligation_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    facts = await load_facts(session, bank_id, obligation_ids)
    evaluations = evaluate(facts)
    scope = list(facts.obligations)
    open_findings = (
        await session.scalars(
            select(ComplianceFinding).where(
                ComplianceFinding.bank_id == bank_id,
                ComplianceFinding.obligation_id.in_(scope),
                ComplianceFinding.status == "OPEN",
            )
        )
    ).all()
    existing = {(f.obligation_id, f.control_id, f.gap_type): f for f in open_findings}
    # Gaps open on obligations that a newer version superseded: a re-detected gap with the
    # same obligation lineage key, control and type is "carried over", not new.
    prior = (
        await session.execute(
            select(Obligation.obligation_key, ComplianceFinding.control_id, ComplianceFinding.gap_type)
            .join(Obligation, Obligation.id == ComplianceFinding.obligation_id)
            .where(
                ComplianceFinding.bank_id == bank_id,
                ComplianceFinding.status == "OPEN",
                Obligation.status.in_(("SUPERSEDED", "REMOVED")),
            )
        )
    ).all()
    carried_keys = {(k, c, g) for k, c, g in prior}
    carried = 0
    detected: set[tuple[uuid.UUID, uuid.UUID | None, str]] = set()
    created: list[uuid.UUID] = []
    by_type: dict[str, int] = defaultdict(int)
    for evaluation in evaluations:
        obligation = evaluation.obligation
        for gap in evaluation.gaps:
            key = (obligation.id, gap.control_id, gap.gap_type.value)
            detected.add(key)
            by_type[gap.gap_type.value] += 1
            details = {
                **gap.details,
                "coverage": evaluation.coverage,
                "obligation_key": obligation.obligation_key,
                "section_path": obligation.section_path,
            }
            if key in existing:
                existing[key].details, existing[key].provisional = details, gap.provisional
                continue
            is_carried = (obligation.obligation_key, gap.control_id, gap.gap_type.value) in carried_keys
            details["carried_over"] = is_carried
            finding = ComplianceFinding(
                bank_id=bank_id,
                obligation_id=obligation.id,
                control_id=gap.control_id,
                gap_type=gap.gap_type.value,
                status="OPEN",
                title=gap.title,
                description=f'{gap.description} Obligation {obligation.section_path}: "{obligation.source_quote[:300]}"',
                details=details,
                provisional=gap.provisional,
                detected_run_id=run_id,
            )
            session.add(finding)
            await session.flush()
            if is_carried:
                carried += 1
            else:
                created.append(finding.id)
            await upsert_edge(
                session, src=("finding", finding.id), rel="VIOLATES", dst=("obligation", obligation.id), bank_id=bank_id
            )
            if gap.control_id:
                await upsert_edge(
                    session,
                    src=("finding", finding.id),
                    rel="AFFECTS",
                    dst=("control", gap.control_id),
                    bank_id=bank_id,
                )
    resolved = 0
    for key, finding in existing.items():
        if key not in detected:
            finding.status, finding.resolved_run_id = "RESOLVED", run_id
            resolved += 1
    # Findings on obligations superseded by a newer version are closed as SUPERSEDED.
    superseded = (
        await session.scalars(
            select(ComplianceFinding)
            .join(Obligation, Obligation.id == ComplianceFinding.obligation_id)
            .where(
                ComplianceFinding.bank_id == bank_id,
                ComplianceFinding.status == "OPEN",
                Obligation.status.in_(("SUPERSEDED", "REMOVED")),
            )
        )
    ).all()
    for finding in superseded:
        finding.status = "SUPERSEDED"
    return {
        "new_findings": len(created),
        "carried_over": carried,
        "resolved": resolved,
        "superseded": len(superseded),
        "by_type": dict(by_type),
        "new_finding_ids": [str(i) for i in created],
        "obligations_evaluated": len(evaluations),
    }


async def score_open_risks(session: AsyncSession, bank_id: uuid.UUID, run_id: uuid.UUID | None) -> dict[str, Any]:
    findings = (
        await session.scalars(
            select(ComplianceFinding).where(ComplianceFinding.bank_id == bank_id, ComplianceFinding.status == "OPEN")
        )
    ).all()
    obligations = {
        o.id: o
        for o in (
            await session.scalars(select(Obligation).where(Obligation.id.in_([f.obligation_id for f in findings])))
        ).all()
    }
    if findings:
        await session.execute(
            update(RiskAssessment)
            .where(RiskAssessment.finding_id.in_([f.id for f in findings]), RiskAssessment.is_current.is_(True))
            .values(is_current=False)
        )
    priorities: dict[str, int] = defaultdict(int)
    for finding in findings:
        obligation = obligations[finding.obligation_id]
        result = score_risk(
            RiskInput(
                gap_type=finding.gap_type,
                obligation_type=obligation.obligation_type,
                modality=obligation.modality,
                coverage=float(finding.details.get("coverage", 0.0)),
                effective_from=obligation.effective_from,
                as_of=date.today(),
            )
        )
        priorities[result.priority] += 1
        session.add(
            RiskAssessment(
                bank_id=bank_id,
                finding_id=finding.id,
                score=result.score,
                priority=result.priority,
                factors=result.factors,
                reason=result.reason,
                formula_version=FORMULA_VERSION,
                is_current=True,
                run_id=run_id,
            )
        )
    return {"scored": len(findings), "priorities": dict(priorities)}
