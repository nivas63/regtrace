from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, Timestamps, UUIDPk


class Control(UUIDPk, Timestamps, Base):
    __tablename__ = "controls"
    __table_args__ = (
        UniqueConstraint("bank_id", "control_key", "version_no"),
        Index(
            "ix_controls_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    control_key: Mapped[str] = mapped_column(String(50))  # e.g. C-KYC-02
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    control_type: Mapped[str] = mapped_column(String(16))  # PREVENTIVE|DETECTIVE|CORRECTIVE
    automation: Mapped[str] = mapped_column(String(16))  # MANUAL|SEMI_AUTOMATED|AUTOMATED
    frequency: Mapped[str | None] = mapped_column(String(16))  # ISO-8601 duration or EVENT
    owner: Mapped[str | None] = mapped_column(String(200))
    business_unit: Mapped[str | None] = mapped_column(String(200))
    systems: Mapped[list[str]] = mapped_column(JSONB, default=list)
    procedure_ref: Mapped[str | None] = mapped_column(String(300))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


class ControlObligation(UUIDPk, Timestamps, Base):
    """Many-to-many obligation <-> control mapping with coverage and review state."""

    __tablename__ = "control_obligations"
    __table_args__ = (UniqueConstraint("bank_id", "obligation_id", "control_id"),)

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT"), index=True
    )
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.id", ondelete="RESTRICT"), index=True
    )
    mapping_type: Mapped[str] = mapped_column(String(16))  # FULL | PARTIAL | NONE
    coverage_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    retrieval_score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    control_quote: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # PROPOSED | APPROVED | REJECTED | MODIFIED | STALE (obligation changed)
    review_status: Mapped[str] = mapped_column(String(16), default="PROPOSED", index=True)
    proposed_by: Mapped[str] = mapped_column(String(16), default="AGENT")
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Evidence(UUIDPk, Timestamps, Base):
    __tablename__ = "evidence"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    control_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.id", ondelete="RESTRICT"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT")
    )
    # AUDIT_REPORT | TEST_REPORT | SYSTEM_LOG | CONFIG_EXPORT | APPROVAL_RECORD |
    # SCREENSHOT | ATTESTATION | POLICY_DOCUMENT | TICKET
    evidence_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    collected_on: Mapped[date | None] = mapped_column(Date)
    source_system: Mapped[str | None] = mapped_column(String(200))
    # SYSTEM_GENERATED | SIGNED_REPORT | SCREENSHOT | SELF_ATTESTATION
    authenticity: Mapped[str] = mapped_column(String(24), default="SIGNED_REPORT")


class ControlTest(UUIDPk, Timestamps, Base):
    __tablename__ = "control_tests"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    control_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controls.id", ondelete="RESTRICT"), index=True
    )
    tested_on: Mapped[date] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(16))  # PASS | FAIL | PARTIAL
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    exceptions: Mapped[int] = mapped_column(Integer, default=0)
    tester: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class EvidenceAssessment(UUIDPk, Timestamps, Base):
    """Per-criterion judgement: does the evidence satisfy a required-evidence item?"""

    __tablename__ = "evidence_assessments"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    criterion: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(16))  # MET | NOT_MET | INSUFFICIENT
    quote: Mapped[str | None] = mapped_column(Text)
    quote_verified: Mapped[bool] = mapped_column(default=False)
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)


class ControlAssessment(UUIDPk, Timestamps, Base):
    """Deterministic effectiveness score for a control as of a date."""

    __tablename__ = "control_assessments"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    design_score: Mapped[float] = mapped_column(Float)
    operating_score: Mapped[float | None] = mapped_column(Float)
    evidence_score: Mapped[float] = mapped_column(Float)
    recency_score: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    # EFFECTIVE | PARTIALLY_EFFECTIVE | INEFFECTIVE | INSUFFICIENT_EVIDENCE
    rating: Mapped[str] = mapped_column(String(24))
    factors: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    formula_version: Mapped[str] = mapped_column(String(16))
    is_current: Mapped[bool] = mapped_column(default=True, index=True)
