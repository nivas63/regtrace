from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class KGEdge(UUIDPk, Timestamps, Base):
    """Typed, time-valid edge of the compliance knowledge graph.

    Nodes are rows of the domain tables (node type = table-level concept). Keeping
    edges in Postgres keeps graph updates in the same transaction as domain writes.
    """

    __tablename__ = "kg_edges"
    __table_args__ = (
        UniqueConstraint("src_type", "src_id", "rel", "dst_type", "dst_id", "bank_id"),
        Index("ix_kg_edges_src", "src_type", "src_id", "rel"),
        Index("ix_kg_edges_dst", "dst_type", "dst_id", "rel"),
    )

    bank_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    src_type: Mapped[str] = mapped_column(String(32))
    src_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    rel: Mapped[str] = mapped_column(String(32))
    dst_type: Mapped[str] = mapped_column(String(32))
    dst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    props: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
