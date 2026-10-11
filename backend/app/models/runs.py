from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class WorkflowRun(UUIDPk, Timestamps, Base):
    """A durable workflow execution. Also acts as the job queue row (status=QUEUED)."""

    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_queue", "status", "available_at"),)

    bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    workflow_type: Mapped[str] = mapped_column(String(48), index=True)
    # QUEUED | RUNNING | WAITING_REVIEW | SUCCEEDED | FAILED | CANCELLED
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    plan: Mapped[list[str]] = mapped_column(JSONB, default=list)
    plan_rationale: Mapped[str | None] = mapped_column(Text)
    current_step: Mapped[str | None] = mapped_column(String(64))
    replans: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    llm_call_budget: Mapped[int] = mapped_column(Integer, default=300)
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    triggered_by: Mapped[str] = mapped_column(String(100), default="system")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)


class WorkflowStep(UUIDPk, Timestamps, Base):
    __tablename__ = "workflow_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    step_name: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # RUNNING | SUCCEEDED | FAILED | SKIPPED | WAITING_REVIEW | FALLBACK
    status: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str | None] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)


class ToolCall(UUIDPk, Timestamps, Base):
    __tablename__ = "tool_calls"

    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16))  # OK | ERROR | DENIED | TIMEOUT
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float)


class LLMCall(UUIDPk, Timestamps, Base):
    __tablename__ = "llm_calls"

    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    purpose: Mapped[str] = mapped_column(String(64), index=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    # SUCCESS | INVALID_OUTPUT | EXHAUSTED | BUDGET_EXCEEDED
    outcome: Mapped[str] = mapped_column(String(24))
    repairs: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    equivalent_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)


class LLMCacheEntry(Base):
    """Response cache: identical prompt version + input never costs quota twice."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(64))
    response_text: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hits: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
