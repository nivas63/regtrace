from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class ComplianceFinding(UUIDPk, Timestamps, Base):
    __tablename__ = "compliance_findings"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT"), index=True
    )
    control_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.id", ondelete="RESTRICT"), index=True
    )
    # MISSING_CONTROL | PARTIAL_CONTROL | INEFFECTIVE_CONTROL | STALE_EVIDENCE |
    # MISSING_EVIDENCE | AMBIGUOUS_REQUIREMENT | CONFLICTING_REGULATION | APPLICABILITY_UNCERTAIN
    gap_type: Mapped[str] = mapped_column(String(32), index=True)
    # OPEN | RESOLVED | SUPERSEDED | ACCEPTED_RISK
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    provisional: Mapped[bool] = mapped_column(default=False)  # based on unreviewed mappings
    detected_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolved_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RiskAssessment(UUIDPk, Timestamps, Base):
    __tablename__ = "risk_assessments"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compliance_findings.id", ondelete="RESTRICT"), index=True
    )
    score: Mapped[float] = mapped_column(Float)
    priority: Mapped[str] = mapped_column(String(4))  # P1..P4
    factors: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    formula_version: Mapped[str] = mapped_column(String(16))
    is_current: Mapped[bool] = mapped_column(default=True, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RemediationAction(UUIDPk, Timestamps, Base):
    __tablename__ = "remediation_actions"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compliance_findings.id", ondelete="RESTRICT"), index=True
    )
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB)  # validated RemediationPlan
    owner: Mapped[str | None] = mapped_column(String(200))
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str] = mapped_column(String(4))
    # PROPOSED | APPROVED | REJECTED | IN_PROGRESS | DONE
    status: Mapped[str] = mapped_column(String(16), default="PROPOSED", index=True)
    requires_approval: Mapped[bool] = mapped_column(default=True)
    approval_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ImpactAssessment(UUIDPk, Timestamps, Base):
    __tablename__ = "impact_assessments"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    regulation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    from_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)


class Simulation(UUIDPk, Timestamps, Base):
    __tablename__ = "simulations"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scenario: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
