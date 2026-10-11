from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Identity, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class HumanReview(UUIDPk, Timestamps, Base):
    """A decision the system will not make on its own."""

    __tablename__ = "human_reviews"

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    # OBLIGATION | MAPPING | APPLICABILITY | CONFLICT | REMEDIATION | AMENDMENT | RUN_FAILURE
    subject_type: Mapped[str] = mapped_column(String(24), index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    reason: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    priority: Mapped[str] = mapped_column(String(8), default="NORMAL")  # HIGH | NORMAL | LOW
    # PENDING | APPROVED | REJECTED | MODIFIED
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    modification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proposed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class AuditLog(Base):
    """Append-only, hash-chained audit trail.

    `hash = sha256(prev_hash || canonical_json(entry))`. The application role has
    INSERT/SELECT only on this table (no UPDATE/DELETE), so tampering requires the
    owner role and is detectable by re-verifying the chain.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    bank_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # USER | AGENT | SYSTEM
    actor_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80), index=True)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(64), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class EvalRun(UUIDPk, Timestamps, Base):
    __tablename__ = "eval_runs"

    suite: Mapped[str] = mapped_column(String(64), index=True)
    dataset_version: Mapped[str] = mapped_column(String(32))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
