from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, Timestamps, UUIDPk


class Regulation(UUIDPk, Timestamps, Base):
    """Logical regulation (e.g. 'KYC Directions'), independent of versions."""

    __tablename__ = "regulations"

    regulator: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    ref_no: Mapped[str | None] = mapped_column(String(200), index=True)
    topic: Mapped[str | None] = mapped_column(String(100))
    jurisdiction: Mapped[str] = mapped_column(String(50), default="IN")
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class RegulationVersion(UUIDPk, Timestamps, Base):
    __tablename__ = "regulation_versions"
    __table_args__ = (UniqueConstraint("regulation_id", "version_no"),)

    regulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regulations.id", ondelete="RESTRICT"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT"))
    version_no: Mapped[int] = mapped_column(Integer)
    issued_on: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)  # NULL = in force
    supersedes_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # ACTIVE | SUPERSEDED | PENDING_REVIEW
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    # How this version came to exist: ORIGINAL | FULL_REISSUE | AMENDMENT_APPLIED
    origin: Mapped[str] = mapped_column(String(32), default="ORIGINAL")


class Obligation(UUIDPk, Timestamps, Base):
    """An atomic regulatory requirement with a verified source quote."""

    __tablename__ = "obligations"
    __table_args__ = (
        Index("ix_obligations_key_version", "obligation_key", "version_id"),
        Index(
            "ix_obligations_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # Stable identity across versions (lineage).
    obligation_key: Mapped[str] = mapped_column(String(200))
    regulation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regulations.id", ondelete="RESTRICT"), index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regulation_versions.id", ondelete="RESTRICT"), index=True
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT"), index=True
    )
    section_path: Mapped[str] = mapped_column(String(100))
    requirement_text: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    modality: Mapped[str] = mapped_column(String(16))  # MUST | MUST_NOT | SHOULD | MAY
    obligation_type: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    action: Mapped[str] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(Text)
    applicability: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # {"kind": "PERIODIC|EVENT_DRIVEN|ONE_TIME|CONTINUOUS", "period": "P6M", "text": "..."}
    frequency: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deadline_text: Mapped[str | None] = mapped_column(Text)
    responsible_function: Mapped[str | None] = mapped_column(String(200))
    severity_hint: Mapped[int] = mapped_column(Integer, default=3)
    required_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    ambiguity_note: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_factors: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # PENDING | APPROVED | REJECTED | MODIFIED
    review_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    # ACTIVE | SUPERSEDED | REMOVED
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    effective_from: Mapped[date | None] = mapped_column(Date)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(100))


class ObligationLineage(UUIDPk, Timestamps, Base):
    __tablename__ = "obligation_lineage"

    prev_obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT"), index=True
    )
    next_obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT"), index=True
    )
    change_type: Mapped[str] = mapped_column(String(16))  # ADDED|REMOVED|MODIFIED|UNCHANGED
    change_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    similarity: Mapped[float | None] = mapped_column(Float)


class RegulatoryChange(UUIDPk, Timestamps, Base):
    """Section-level difference between two versions of a regulation."""

    __tablename__ = "regulatory_changes"

    regulation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    from_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    to_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    section_path: Mapped[str] = mapped_column(String(100))
    change_type: Mapped[str] = mapped_column(String(16))  # ADDED | REMOVED | MODIFIED
    semantic_category: Mapped[str | None] = mapped_column(String(40))
    is_material: Mapped[bool] = mapped_column(default=True)
    summary: Mapped[str | None] = mapped_column(Text)
    old_text: Mapped[str | None] = mapped_column(Text)
    new_text: Mapped[str | None] = mapped_column(Text)
    similarity: Mapped[float | None] = mapped_column(Float)
    diff: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    old_section_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    new_section_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ObligationApplicability(UUIDPk, Timestamps, Base):
    __tablename__ = "obligation_applicability"
    __table_args__ = (UniqueConstraint("bank_id", "obligation_id"),)

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[str] = mapped_column(String(20))  # APPLICABLE | NOT_APPLICABLE | UNCERTAIN
    decided_by: Mapped[str] = mapped_column(String(10))  # RULE | LLM | HUMAN
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(16), default="PENDING")


class ObligationCluster(UUIDPk, Timestamps, Base):
    """Equivalent obligations across regulations (cross-regulation intelligence)."""

    __tablename__ = "obligation_clusters"

    topic: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(String(10))  # LLM | SIMILARITY | HUMAN
    confidence: Mapped[float] = mapped_column(Float)
    shared_control_suggestion: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ObligationClusterMember(Base):
    __tablename__ = "obligation_cluster_members"

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("obligation_clusters.id", ondelete="CASCADE"),
        primary_key=True,
    )
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT"), primary_key=True
    )
    similarity: Mapped[float] = mapped_column(Float)


class RegulatoryConflict(UUIDPk, Timestamps, Base):
    """A *potential* conflict between two obligations. Always requires human review."""

    __tablename__ = "regulatory_conflicts"
    __table_args__ = (UniqueConstraint("obligation_a_id", "obligation_b_id"),)

    obligation_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT")
    )
    obligation_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligations.id", ondelete="RESTRICT")
    )
    conflict_type: Mapped[str] = mapped_column(String(40))
    signals: Mapped[list[str]] = mapped_column(JSONB, default=list)  # deterministic pre-checks
    reason: Mapped[str] = mapped_column(Text)
    resolution_hint: Mapped[str | None] = mapped_column(Text)
    applicability_overlap: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
