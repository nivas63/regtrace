from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class Bank(UUIDPk, Timestamps, Base):
    __tablename__ = "banks"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    # Applicability profile: entity_type, products[], jurisdictions[], customer_segments[],
    # size metrics. Used by the deterministic applicability rules.
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(default=True)


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("banks.id", ondelete="RESTRICT"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(default=True)
