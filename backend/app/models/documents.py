from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIM, Base, Timestamps, UUIDPk


class Document(UUIDPk, Timestamps, Base):
    """An uploaded file. Immutable content, addressed by SHA-256."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("bank_id", "sha256", name="uq_documents_bank_sha"),)

    # NULL = public regulatory document shared by all tenants.
    bank_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("banks.id", ondelete="RESTRICT"), index=True
    )
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(500))
    # REGULATION | AMENDMENT | POLICY | CONTROL_MATRIX | CONTROL_TESTS | EVIDENCE | OTHER
    doc_class: Mapped[str] = mapped_column(String(32), index=True)
    # UPLOADED | PARSED | PROCESSED | FAILED | DUPLICATE
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    title: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    is_synthetic: Mapped[bool] = mapped_column(default=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Extracted metadata: ref_no, issued_on, effective_from, regulator, supersedes, ...
    doc_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    parse_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Section(UUIDPk, Timestamps, Base):
    """A structural unit (chapter / paragraph / annex) of a parsed document."""

    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("document_id", "section_path", name="uq_sections_doc_path"),)

    bank_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT"), index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regulation_versions.id", ondelete="RESTRICT"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT")
    )
    section_path: Mapped[str] = mapped_column(String(100))  # e.g. "5.2(a)"
    title: Mapped[str | None] = mapped_column(String(500))
    level: Mapped[int] = mapped_column(Integer, default=1)
    ordinal: Mapped[int] = mapped_column(Integer)
    # CHAPTER | PARAGRAPH | ANNEX | TABLE | PREAMBLE
    section_type: Mapped[str] = mapped_column(String(32), default="PARAGRAPH")
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)


class Chunk(UUIDPk, Timestamps, Base):
    """Retrieval unit: clause-aligned text with a contextual header and embedding."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_validity", "effective_from", "effective_to"),
    )

    bank_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="RESTRICT"), index=True
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="RESTRICT"), index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    header: Mapped[str] = mapped_column(Text)  # "Regulation > Chapter > 5.2 Title"
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)  # offset within the section text
    doc_class: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(header, '') || ' ' || text)", persisted=True),
    )
    # Temporal validity copied from the regulation version for cheap filtering.
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(default=True)
    injection_risk: Mapped[float] = mapped_column(Float, default=0.0)
    quarantined: Mapped[bool] = mapped_column(default=False)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
