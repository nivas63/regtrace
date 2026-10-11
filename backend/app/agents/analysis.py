"""Analysis agents: remediation planning, cross-regulation clustering, conflict detection."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select, text

from app.agents.schemas import ClusterBatch, ConflictBatch, RemediationBatch
from app.domain.types import ObligationType
from app.knowledge_graph.graph import upsert_edge
from app.llm.errors import LLMError
from app.llm.prompts import AliasMap, DocumentBlock, load_prompt, spotlight
from app.llm.types import ModelTier
from app.models import (
    ComplianceFinding,
    Control,
    ControlAssessment,
    HumanReview,
    Obligation,
    ObligationCluster,
    ObligationClusterMember,
    Regulation,
    RegulatoryConflict,
    RemediationAction,
    RiskAssessment,
)
from app.services import audit
from app.workflows.engine import StepContext

MAX_REMEDIATIONS_PER_RUN = 10
FINDINGS_PER_CALL = 2
PRIORITY_MAX_DAYS = {"P1": 30, "P2": 60, "P3": 90, "P4": 180}


async def recommend_remediation(ctx: StepContext, finding_ids: list[uuid.UUID] | None = None) -> dict[str, Any]:
    """LLM proposes plans; code decides due dates, priority and approval requirements."""
    services = ctx.services
    bank_id = ctx.run.bank_id
    async with services.db.tenant_session(bank_id) as session:
        query = (
            select(ComplianceFinding, RiskAssessment)
            .join(RiskAssessment, (RiskAssessment.finding_id == ComplianceFinding.id) & RiskAssessment.is_current)
            .where(ComplianceFinding.bank_id == bank_id, ComplianceFinding.status == "OPEN")
            .order_by(RiskAssessment.score.desc())
        )
        if finding_ids is not None:
            query = query.where(ComplianceFinding.id.in_(finding_ids))
        rows = (await session.execute(query)).all()
        active = set(
            (
                await session.scalars(
                    select(RemediationAction.finding_id).where(
                        RemediationAction.bank_id == bank_id,
                        RemediationAction.status.in_(("PROPOSED", "APPROVED", "IN_PROGRESS")),
                    )
                )
            ).all()
        )
        pending = [(f, r) for f, r in rows if f.id not in active][:MAX_REMEDIATIONS_PER_RUN]
        obligations = {
            o.id: o
            for o in (
                await session.scalars(
                    select(Obligation).where(Obligation.id.in_([f.obligation_id for f, _ in pending]))
                )
            ).all()
        }
        controls = {
            c.id: c
            for c in (
                await session.scalars(
                    select(Control).where(Control.id.in_([f.control_id for f, _ in pending if f.control_id]))
                )
            ).all()
        }
        assessments = {
            a.control_id: a
            for a in (
                await session.scalars(
                    select(ControlAssessment).where(
                        ControlAssessment.control_id.in_(list(controls)), ControlAssessment.is_current.is_(True)
                    )
                )
            ).all()
        }

    stats = {"findings": len(pending), "plans": 0, "requires_approval": 0, "llm_calls": 0}
    prompt = load_prompt("remediation")
    for start in range(0, len(pending), FINDINGS_PER_CALL):
        batch = pending[start : start + FINDINGS_PER_CALL]
        blocks = []
        finding_aliases: AliasMap[ComplianceFinding] = AliasMap("F")
        for finding, risk in batch:
            obligation = obligations[finding.obligation_id]
            control = controls.get(finding.control_id) if finding.control_id else None
            assessment = assessments.get(control.id) if control else None
            finding_alias = finding_aliases.add(finding, finding.id)
            blocks.append(
                DocumentBlock(
                    finding_alias,
                    f"finding {finding_alias} ({finding.gap_type})",
                    (
                        f"GAP: {finding.gap_type} - {finding.title}\n"
                        f"OBLIGATION ({obligation.section_path}, {obligation.obligation_type}): {obligation.source_quote}\n"
                        f"REQUIRED FREQUENCY: {(obligation.frequency or {}).get('period') or 'n/a'}; "
                        f"DEADLINE: {obligation.deadline_text or 'n/a'}; RESPONSIBLE: {obligation.responsible_function or 'n/a'}\n"
                        + (
                            f"CONTROL {control.control_key}: {control.description} (frequency {control.frequency}, "
                            f"owner {control.owner}, systems {', '.join(control.systems)})\n"
                            if control
                            else "CONTROL: none mapped\n"
                        )
                        + (
                            f"CONTROL SCORE: {assessment.score} ({assessment.rating}); reasons: "
                            f"{'; '.join(assessment.factors.get('reasons', []))}\n"
                            if assessment
                            else ""
                        )
                        + f"RISK: {risk.score} {risk.priority} - {risk.reason}"
                    ),
                )
            )
        try:
            outcome = await services.llm.structured(
                ctx=ctx.llm_ctx("remediation_planner"),
                prompt=prompt,
                user_content=spotlight(blocks),
                schema=RemediationBatch,
                tier=ModelTier.REASONING,
                max_output_tokens=3500,
                redact=True,
            )
            stats["llm_calls"] += 1
        except LLMError as exc:
            ctx.decide(
                "remediation_llm_unavailable",
                f"{exc.code}; findings left without a plan",
                findings=[str(f.id) for f, _ in batch],
            )
            continue
        plans: dict[uuid.UUID, Any] = {}
        for candidate_plan in outcome.value.plans:
            resolved = finding_aliases.resolve(candidate_plan.finding_id)
            if resolved is not None:
                plans[resolved.id] = candidate_plan
        async with services.db.tenant_session(bank_id) as session:
            for finding, risk in batch:
                plan = plans.get(finding.id)
                if plan is None:
                    continue
                obligation = obligations[finding.obligation_id]
                cap = PRIORITY_MAX_DAYS[risk.priority]
                due = date.today() + timedelta(days=min(plan.target_days, cap))
                if obligation.effective_from and obligation.effective_from > date.today():
                    due = min(due, obligation.effective_from)
                kinds = {a.kind for a in plan.actions}
                requires_approval = (
                    risk.priority in ("P1", "P2") or plan.touches_production or "TECHNOLOGY_CHANGE" in kinds
                )
                action = RemediationAction(
                    bank_id=bank_id,
                    finding_id=finding.id,
                    plan=plan.model_dump(),
                    owner=plan.suggested_owner,
                    due_date=due,
                    priority=risk.priority,
                    status="PROPOSED",
                    requires_approval=requires_approval,
                    run_id=ctx.run.id,
                )
                session.add(action)
                await session.flush()
                stats["plans"] += 1
                await upsert_edge(
                    session,
                    src=("finding", finding.id),
                    rel="MITIGATED_BY",
                    dst=("remediation", action.id),
                    bank_id=bank_id,
                )
                if requires_approval:
                    stats["requires_approval"] += 1
                    review = HumanReview(
                        bank_id=bank_id,
                        subject_type="REMEDIATION",
                        subject_id=action.id,
                        reason=f"{risk.priority} remediation for {finding.gap_type} on {obligation.section_path}"
                        + (" (touches production systems)" if plan.touches_production else ""),
                        recommendation={
                            "root_cause": plan.root_cause,
                            "actions": [a.model_dump() for a in plan.actions],
                        },
                        priority="HIGH" if risk.priority == "P1" else "NORMAL",
                        run_id=ctx.run.id,
                    )
                    session.add(review)
                    await session.flush()
                    action.approval_review_id = review.id
            await audit.record(
                session,
                bank_id=bank_id,
                actor=audit.Actor.agent("remediation_planner"),
                action="remediation.proposed",
                run_id=ctx.run.id,
                details={"findings": [str(f.id) for f, _ in batch]},
            )
    return stats


# ----------------------------------------------------------------------------------
# Cross-regulation intelligence
# ----------------------------------------------------------------------------------

_DURATION = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|thirty|ninety)\s+(working\s+)?(hour|day|month|year)s?\b",
    re.I,
)
_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "thirty": 30,
    "ninety": 90,
}
_KEEP = re.compile(r"\b(retain|retained|preserve|maintain|kept|stored?)\b", re.I)
_DISPOSE = re.compile(r"\b(erase|delete|destroy|not be retained|shall not retain|purge|dispose)\b", re.I)
_RELATED_TYPES = {
    frozenset({ObligationType.RECORD_RETENTION, ObligationType.DATA_PROTECTION}),
    frozenset({ObligationType.REPORTING, ObligationType.INCIDENT_MANAGEMENT}),
}
CLUSTER_SIMILARITY = 0.80
CONFLICT_SIMILARITY = 0.55


def durations_in_days(value: str) -> list[int]:
    out = []
    for number, _, unit in _DURATION.findall(value):
        n = int(number) if number.isdigit() else _WORDS[number.lower()]
        out.append(n * {"hour": 0, "day": 1, "month": 30, "year": 365}[unit.lower()] or 1)
    return out


def conflict_signals(a: Obligation, b: Obligation) -> list[str]:
    """Deterministic pre-checks. A pair is sent to the LLM only if at least one fires."""
    signals = []
    text_a, text_b = a.source_quote, b.source_quote
    if (_KEEP.search(text_a) and _DISPOSE.search(text_b)) or (_DISPOSE.search(text_a) and _KEEP.search(text_b)):
        signals.append("retention_polarity: one text requires keeping data, the other requires disposal")
    da, db = durations_in_days(text_a), durations_in_days(text_b)
    if da and db and set(da) != set(db):
        signals.append(f"duration_mismatch: {da} vs {db} days")
    if {a.modality, b.modality} == {"MUST", "MUST_NOT"}:
        signals.append("modality_opposition: MUST vs MUST_NOT")
    return signals


async def analyse_cross_regulation(ctx: StepContext) -> dict[str, Any]:
    services = ctx.services
    bank_id = ctx.run.bank_id
    async with services.db.tenant_session(bank_id) as session:
        rows = (
            await session.execute(
                text(
                    """
            SELECT a.id, b.id, 1 - (a.embedding <=> b.embedding) AS sim
            FROM obligations a JOIN obligations b
              ON a.regulation_id <> b.regulation_id AND a.id < b.id
            WHERE a.status = 'ACTIVE' AND b.status = 'ACTIVE'
              AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) >= :min_sim
            ORDER BY sim DESC LIMIT 400
            """
                ),
                {"min_sim": CONFLICT_SIMILARITY},
            )
        ).all()
        # Second, embedding-independent candidate path: obligations of related types
        # (e.g. retention vs data protection) from different regulations are always
        # screened by the deterministic pre-checks, whatever their vector similarity.
        related_rows = (
            await session.execute(
                text(
                    """
            SELECT a.id, b.id, COALESCE(1 - (a.embedding <=> b.embedding), 0) AS sim
            FROM obligations a JOIN obligations b
              ON a.regulation_id <> b.regulation_id AND a.id < b.id
            WHERE a.status = 'ACTIVE' AND b.status = 'ACTIVE'
              AND (a.obligation_type, b.obligation_type) IN (
                ('RECORD_RETENTION','DATA_PROTECTION'), ('DATA_PROTECTION','RECORD_RETENTION'),
                ('DATA_PROTECTION','DATA_PROTECTION'), ('RECORD_RETENTION','RECORD_RETENTION'),
                ('REPORTING','INCIDENT_MANAGEMENT'), ('INCIDENT_MANAGEMENT','REPORTING'),
                ('INCIDENT_MANAGEMENT','INCIDENT_MANAGEMENT'))
              OR a.obligation_type = b.obligation_type
            LIMIT 600
            """
                )
            )
        ).all()
        seen_pairs = {(r[0], r[1]) for r in rows}
        rows = [*rows, *(r for r in related_rows if (r[0], r[1]) not in seen_pairs)]
        obligations = {
            o.id: o
            for o in (
                await session.scalars(
                    select(Obligation).where(Obligation.id.in_({r[0] for r in rows} | {r[1] for r in rows}))
                )
            ).all()
        }
        regulators = {r.id: r.regulator for r in (await session.scalars(select(Regulation))).all()}

    # --- clusters (union-find over high-similarity or same-type-same-period pairs) ----
    parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(x: uuid.UUID) -> uuid.UUID:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    similarity: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    for a_id, b_id, sim in rows:
        similarity[(a_id, b_id)] = float(sim)
        a, b = obligations[a_id], obligations[b_id]
        # Equivalent duty: same obligation type AND the same required period (e.g. two
        # "annual training" obligations), or very high semantic similarity.
        period_a, period_b = (a.frequency or {}).get("period"), (b.frequency or {}).get("period")
        same_kind = a.obligation_type == b.obligation_type and period_a is not None and period_a == period_b
        similar_kind = a.obligation_type == b.obligation_type and float(sim) >= CONFLICT_SIMILARITY
        if float(sim) >= CLUSTER_SIMILARITY or same_kind or similar_kind:
            parent[find(a_id)] = find(b_id)
    groups: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for node in list(parent):
        groups[find(node)].add(node)
    candidate_groups = [sorted(g, key=str)[:6] for g in groups.values() if len(g) >= 2]

    cluster_stats = {"candidate_groups": len(candidate_groups), "confirmed": 0}
    confirmed: list[tuple[list[uuid.UUID], Any]] = []
    if candidate_groups:
        blocks = [
            DocumentBlock(
                f"G{i}",
                "candidate group",
                "\n".join(
                    f"- [{regulators.get(obligations[o].regulation_id, '?')} {obligations[o].section_path}] "
                    f"{obligations[o].source_quote}"
                    for o in group
                ),
            )
            for i, group in enumerate(candidate_groups)
        ]
        try:
            cluster_outcome = await services.llm.structured(
                ctx=ctx.llm_ctx("cross_regulation"),
                prompt=load_prompt("cluster_confirmation"),
                user_content=spotlight(blocks),
                schema=ClusterBatch,
                tier=ModelTier.FAST,
                max_output_tokens=2500,
            )
            for group in cluster_outcome.value.groups:
                index = int(group.group_id.lstrip("G")) if group.group_id.lstrip("G").isdigit() else -1
                if group.equivalent and 0 <= index < len(candidate_groups):
                    confirmed.append((candidate_groups[index], group))
        except LLMError as exc:
            ctx.decide("cluster_llm_unavailable", f"{exc.code}; clusters kept as similarity-only")
            confirmed = [(g, None) for g in candidate_groups]

    # --- conflicts -----------------------------------------------------------------
    candidates: list[tuple[Obligation, Obligation, list[str]]] = []
    for (a_id, b_id), sim in similarity.items():
        a, b = obligations[a_id], obligations[b_id]
        related = (
            a.obligation_type == b.obligation_type
            or frozenset({a.obligation_type, b.obligation_type}) in _RELATED_TYPES
        )
        if not related or (
            regulators.get(a.regulation_id) == regulators.get(b.regulation_id) and sim < CLUSTER_SIMILARITY
        ):
            continue
        signals = conflict_signals(a, b)
        if signals:
            candidates.append((a, b, signals))
    # Strongest evidence first: more deterministic signals, then higher similarity.
    candidates.sort(key=lambda c: (len(c[2]), similarity.get((c[0].id, c[1].id), 0.0)), reverse=True)
    candidates = candidates[:12]
    conflict_stats = {"candidate_pairs": len(candidates), "potential_conflicts": 0}
    judgements: dict[str, Any] = {}
    for start in range(0, len(candidates), 3):
        batch = candidates[start : start + 3]
        blocks = [
            DocumentBlock(
                f"P{start + i}",
                "obligation pair",
                (
                    f"A [{regulators.get(a.regulation_id)} {a.section_path}]: {a.source_quote}\n"
                    f"B [{regulators.get(b.regulation_id)} {b.section_path}]: {b.source_quote}\n"
                    f"Pre-check signals: {'; '.join(signals)}"
                ),
            )
            for i, (a, b, signals) in enumerate(batch)
        ]
        try:
            conflict_outcome = await services.llm.structured(
                ctx=ctx.llm_ctx("conflict_detector"),
                prompt=load_prompt("conflict_detection"),
                user_content=spotlight(blocks),
                schema=ConflictBatch,
                tier=ModelTier.REASONING,
                max_output_tokens=2500,
            )
            judgements.update({p.pair_id: p for p in conflict_outcome.value.pairs})
        except LLMError as exc:
            ctx.decide("conflict_llm_unavailable", f"{exc.code}; pairs with signals recorded for human review")

    async with services.db.tenant_session(bank_id) as session:
        await session.execute(delete(ObligationCluster).where(ObligationCluster.confirmed_by != "HUMAN"))
        for members, group_judgement in confirmed:
            cluster = ObligationCluster(
                topic=group_judgement.topic if group_judgement else "Similar obligations",
                summary=group_judgement.summary if group_judgement else "Grouped by semantic similarity (unconfirmed)",
                confirmed_by="LLM" if group_judgement else "SIMILARITY",
                confidence=group_judgement.confidence if group_judgement else 0.5,
                shared_control_suggestion=group_judgement.shared_control_suggestion if group_judgement else None,
                run_id=ctx.run.id,
            )
            session.add(cluster)
            await session.flush()
            cluster_stats["confirmed"] += 1
            for member in members:
                sims = [s for (x, y), s in similarity.items() if member in (x, y) and (x in members and y in members)]
                session.add(
                    ObligationClusterMember(
                        cluster_id=cluster.id, obligation_id=member, similarity=round(max(sims, default=0.0), 3)
                    )
                )
            for x in members:
                for y in members:
                    if str(x) < str(y):
                        await upsert_edge(
                            session,
                            src=("obligation", x),
                            rel="EQUIVALENT_TO",
                            dst=("obligation", y),
                            props={"cluster_id": str(cluster.id)},
                        )

        for index, (a, b, signals) in enumerate(candidates):
            judgement = judgements.get(f"P{index}")
            if judgement is not None and not judgement.is_potential_conflict:
                continue
            first, second = sorted((a, b), key=lambda o: str(o.id))
            existing = await session.scalar(
                select(RegulatoryConflict).where(
                    RegulatoryConflict.obligation_a_id == first.id, RegulatoryConflict.obligation_b_id == second.id
                )
            )
            if existing is not None and existing.review_status != "PENDING":
                continue  # human already decided
            conflict = existing or RegulatoryConflict(obligation_a_id=first.id, obligation_b_id=second.id)
            conflict.conflict_type = judgement.conflict_type.value if judgement else "SCOPE_OVERLAP"
            conflict.signals = signals
            conflict.reason = judgement.reason if judgement else "Deterministic signals only: " + "; ".join(signals)
            conflict.resolution_hint = judgement.resolution_hint if judgement else None
            conflict.applicability_overlap = judgement.applicability_overlap if judgement else None
            conflict.confidence = judgement.confidence if judgement else 0.4
            conflict.run_id = ctx.run.id
            if existing is None:
                session.add(conflict)
                await session.flush()
                session.add(
                    HumanReview(
                        bank_id=bank_id,
                        subject_type="CONFLICT",
                        subject_id=conflict.id,
                        reason=f"Potential conflict ({conflict.conflict_type}) between {regulators.get(a.regulation_id)} "
                        f"{a.section_path} and {regulators.get(b.regulation_id)} {b.section_path}",
                        recommendation={"reason": conflict.reason, "resolution_hint": conflict.resolution_hint},
                        confidence=conflict.confidence,
                        priority="HIGH",
                        run_id=ctx.run.id,
                    )
                )
            conflict_stats["potential_conflicts"] += 1
            await upsert_edge(
                session,
                src=("obligation", first.id),
                rel="POTENTIAL_CONFLICT",
                dst=("obligation", second.id),
                props={"conflict_type": conflict.conflict_type},
            )
        await audit.record(
            session,
            bank_id=bank_id,
            actor=audit.Actor.agent("cross_regulation"),
            action="cross_regulation.analysed",
            run_id=ctx.run.id,
            details={**cluster_stats, **conflict_stats},
        )
    return {**cluster_stats, **conflict_stats}
