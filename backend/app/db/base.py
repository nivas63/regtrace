"""Declarative base and shared column helpers."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic migrations are stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

EMBEDDING_DIM = 768  # BAAI/bge-base-en-v1.5


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Tables whose rows belong to one bank. Row-level security policies are generated
# for these in the migration: a row is visible only when bank_id matches the
# transaction-local setting app.bank_id. `nullable` = shared public rows allowed.
TENANT_TABLES: dict[str, bool] = {
    "documents": True,
    "sections": True,
    "chunks": True,
    "controls": False,
    "control_obligations": False,
    "evidence": False,
    "control_tests": False,
    "evidence_assessments": False,
    "control_assessments": False,
    "obligation_applicability": False,
    "compliance_findings": False,
    "risk_assessments": False,
    "remediation_actions": False,
    "impact_assessments": False,
    "simulations": False,
    "human_reviews": False,
    "kg_edges": True,
    "audit_log": True,
}
