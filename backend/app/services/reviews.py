"""Human-in-the-loop decisions.

Agent -> recommendation (+confidence, evidence) -> HumanReview -> Approve / Reject /
Modify -> effect on the subject -> audit log. Segregation of duties: the user who
proposed or triggered an item cannot approve it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from app.knowledge_graph.graph import upsert_edge
from app.models import (
    ControlObligation,
    HumanReview,
    Obligation,
    ObligationApplicability,
    RegulationVersion,
    RegulatoryConflict,
    RemediationAction,
    WorkflowRun,
)
from app.security.tokens import Principal
from app.services import audit

Decision = Literal["APPROVE", "REJECT", "MODIFY"]
_STATUS = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "MODIFY": "MODIFIED"}


async def decide(
    session: AsyncSession,
    principal: Principal,
    review_id: uuid.UUID,
    decision: Decision,
    comment: str | None,
    modification: dict[str, Any] | None,
) -> HumanReview:
    review = await session.get(HumanReview, review_id)
    if review is None or review.bank_id != principal.bank_id:
        raise NotFoundError("Review not found")
    if review.status != "PENDING":
        raise ConflictError(f"Review already {review.status.lower()}")
    if review.proposed_by is not None and review.proposed_by == principal.user_id:
        raise AuthorizationError("Segregation of duties: you cannot approve an item you proposed")
    if review.run_id is not None:
        run = await session.get(WorkflowRun, review.run_id)
        if run is not None and run.triggered_by == f"user:{principal.user_id}" and review.subject_type == "REMEDIATION":
            raise AuthorizationError(
                "Segregation of duties: the user who triggered the analysis cannot approve its remediation"
            )
    if decision == "MODIFY" and not modification:
        raise DomainValidationError("MODIFY requires a modification payload")

    status = _STATUS[decision]
    effect = await _apply(session, review, decision, modification or {})
    review.status = status
    review.decided_by = principal.user_id
    review.decided_at = datetime.now(UTC)
    review.comment = comment
    review.modification = modification
    await audit.record(
        session,
        bank_id=principal.bank_id,
        actor=audit.Actor.user(principal.user_id),
        action=f"review.{status.lower()}",
        subject_type=review.subject_type.lower(),
        subject_id=review.subject_id,
        run_id=review.run_id,
        details={"review_id": str(review.id), "comment": comment, "effect": effect},
    )
    return review


async def _apply(session: AsyncSession, review: HumanReview, decision: Decision, mod: dict[str, Any]) -> str:
    kind, sid = review.subject_type, review.subject_id
    if kind == "OBLIGATION":
        obligation = await session.get(Obligation, sid)
        if obligation is None:
            return "subject missing"
        obligation.review_status = _STATUS[decision]
        if decision == "REJECT":
            obligation.status = "REJECTED"
            return "obligation rejected and excluded from assessment"
        if decision == "MODIFY":
            for field in ("requirement_text", "obligation_type", "modality", "responsible_function"):
                if field in mod:
                    setattr(obligation, field, mod[field])
            if "period" in mod:
                obligation.frequency = {
                    **(obligation.frequency or {}),
                    "period": mod["period"],
                    "text": "set by reviewer",
                }
        return f"obligation {obligation.review_status.lower()}"
    if kind == "MAPPING":
        mapping = await session.get(ControlObligation, sid)
        if mapping is None:
            return "subject missing"
        mapping.review_status = _STATUS[decision]
        if decision == "MODIFY":
            mapping.mapping_type = mod.get("mapping_type", mapping.mapping_type)
            mapping.coverage_score = float(mod.get("coverage_score", mapping.coverage_score))
        await upsert_edge(
            session,
            src=("obligation", mapping.obligation_id),
            rel="SATISFIED_BY",
            dst=("control", mapping.control_id),
            bank_id=review.bank_id,
            props={
                "mapping_type": mapping.mapping_type,
                "coverage": mapping.coverage_score,
                "status": mapping.review_status,
            },
        )
        return f"mapping {mapping.review_status.lower()}"
    if kind == "APPLICABILITY":
        applicability = await session.scalar(
            select(ObligationApplicability).where(
                ObligationApplicability.obligation_id == sid, ObligationApplicability.bank_id == review.bank_id
            )
        )
        if applicability is None:
            return "subject missing"
        if decision == "MODIFY" and mod.get("decision") in ("APPLICABLE", "NOT_APPLICABLE", "UNCERTAIN"):
            applicability.decision = mod["decision"]
        elif decision == "REJECT":
            applicability.decision = "NOT_APPLICABLE" if applicability.decision == "APPLICABLE" else "APPLICABLE"
        applicability.decided_by, applicability.review_status = "HUMAN", "APPROVED"
        return f"applicability set to {applicability.decision} by reviewer"
    if kind == "CONFLICT":
        conflict = await session.get(RegulatoryConflict, sid)
        if conflict is None:
            return "subject missing"
        conflict.review_status = _STATUS[decision]
        return "confirmed as potential conflict" if decision == "APPROVE" else "dismissed as not a conflict"
    if kind == "REMEDIATION":
        action = await session.get(RemediationAction, sid)
        if action is None:
            return "subject missing"
        action.status = "APPROVED" if decision in ("APPROVE", "MODIFY") else "REJECTED"
        if decision == "MODIFY":
            action.plan = {
                **action.plan,
                **{k: v for k, v in mod.items() if k in ("actions", "suggested_owner", "target_days")},
            }
            action.owner = mod.get("suggested_owner", action.owner)
        return f"remediation {action.status.lower()} (execution remains a separate, tracked action)"
    if kind == "AMENDMENT":
        version = await session.get(RegulationVersion, sid)
        if version is None:
            return "subject missing"
        version.status = "ACTIVE" if decision == "APPROVE" else "REJECTED"
        return f"consolidated version {version.status.lower()}"
    return "acknowledged"
