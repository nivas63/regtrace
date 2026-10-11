"""Explicit field-list serialisation of ORM rows for API responses (no accidental
exposure of columns such as password hashes or embeddings)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any


def _value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def pairs(rows: Iterable[Any]) -> dict[Any, Any]:
    """Two-column query rows -> {first: second}."""
    return {row[0]: row[1] for row in rows}


def dump(obj: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {f: _value(getattr(obj, f)) for f in fields}


DOCUMENT = (
    "id",
    "filename",
    "doc_class",
    "status",
    "title",
    "mime_type",
    "size_bytes",
    "page_count",
    "is_synthetic",
    "source_url",
    "created_at",
    "parse_report",
    "sha256",
)
SECTION = ("id", "section_path", "title", "level", "section_type", "text", "page_from", "page_to", "ordinal")
REGULATION = ("id", "regulator", "title", "ref_no", "topic", "jurisdiction", "is_synthetic", "created_at")
VERSION = (
    "id",
    "regulation_id",
    "document_id",
    "version_no",
    "issued_on",
    "effective_from",
    "effective_to",
    "status",
    "origin",
    "supersedes_version_id",
)
OBLIGATION = (
    "id",
    "obligation_key",
    "regulation_id",
    "version_id",
    "section_id",
    "section_path",
    "requirement_text",
    "source_quote",
    "char_start",
    "char_end",
    "modality",
    "obligation_type",
    "subject",
    "action",
    "condition",
    "applicability",
    "frequency",
    "deadline_text",
    "responsible_function",
    "severity_hint",
    "required_evidence",
    "ambiguity_note",
    "confidence",
    "confidence_factors",
    "review_status",
    "status",
    "effective_from",
    "prompt_version",
    "model",
    "created_at",
)
CONTROL = (
    "id",
    "control_key",
    "version_no",
    "name",
    "description",
    "control_type",
    "automation",
    "frequency",
    "owner",
    "business_unit",
    "systems",
    "procedure_ref",
    "status",
    "attributes",
)
MAPPING = (
    "id",
    "obligation_id",
    "control_id",
    "mapping_type",
    "coverage_score",
    "confidence",
    "retrieval_score",
    "reason",
    "control_quote",
    "evidence",
    "review_status",
    "proposed_by",
    "run_id",
    "updated_at",
)
EVIDENCE = (
    "id",
    "control_id",
    "document_id",
    "evidence_type",
    "title",
    "description",
    "period_start",
    "period_end",
    "collected_on",
    "source_system",
    "authenticity",
)
TEST = ("id", "control_id", "tested_on", "result", "sample_size", "exceptions", "tester", "notes")
EVIDENCE_ASSESSMENT = (
    "id",
    "run_id",
    "obligation_id",
    "control_id",
    "evidence_id",
    "criterion",
    "verdict",
    "quote",
    "quote_verified",
    "rationale",
    "confidence",
    "created_at",
)
CONTROL_ASSESSMENT = (
    "id",
    "control_id",
    "obligation_id",
    "as_of",
    "design_score",
    "operating_score",
    "evidence_score",
    "recency_score",
    "score",
    "rating",
    "factors",
    "formula_version",
    "run_id",
)
FINDING = (
    "id",
    "obligation_id",
    "control_id",
    "gap_type",
    "status",
    "title",
    "description",
    "details",
    "provisional",
    "detected_run_id",
    "resolved_run_id",
    "created_at",
    "updated_at",
)
RISK = ("id", "finding_id", "score", "priority", "factors", "reason", "formula_version")
REMEDIATION = (
    "id",
    "finding_id",
    "plan",
    "owner",
    "due_date",
    "priority",
    "status",
    "requires_approval",
    "approval_review_id",
    "run_id",
    "created_at",
)
CHANGE = (
    "id",
    "regulation_id",
    "from_version_id",
    "to_version_id",
    "section_path",
    "change_type",
    "semantic_category",
    "is_material",
    "summary",
    "old_text",
    "new_text",
    "similarity",
    "diff",
    "confidence",
)
CONFLICT = (
    "id",
    "obligation_a_id",
    "obligation_b_id",
    "conflict_type",
    "signals",
    "reason",
    "resolution_hint",
    "applicability_overlap",
    "confidence",
    "review_status",
    "created_at",
)
CLUSTER = ("id", "topic", "summary", "confirmed_by", "confidence", "shared_control_suggestion", "created_at")
RUN = (
    "id",
    "workflow_type",
    "status",
    "input",
    "state",
    "plan",
    "plan_rationale",
    "current_step",
    "replans",
    "llm_calls_used",
    "llm_call_budget",
    "error",
    "summary",
    "created_at",
    "started_at",
    "finished_at",
    "triggered_by",
    "parent_run_id",
)
STEP = (
    "id",
    "step_name",
    "ordinal",
    "attempt",
    "status",
    "agent",
    "output",
    "decisions",
    "error",
    "started_at",
    "finished_at",
    "duration_ms",
)
TOOL_CALL = ("id", "step_id", "agent", "tool", "args", "result_summary", "status", "error", "latency_ms", "created_at")
LLM_CALL = (
    "id",
    "step_id",
    "purpose",
    "prompt_version",
    "tier",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "cache_hit",
    "outcome",
    "repairs",
    "attempts",
    "equivalent_cost_usd",
    "error",
    "created_at",
)
REVIEW = (
    "id",
    "subject_type",
    "subject_id",
    "reason",
    "recommendation",
    "confidence",
    "priority",
    "status",
    "decided_by",
    "decided_at",
    "comment",
    "modification",
    "run_id",
    "created_at",
)
AUDIT = (
    "id",
    "ts",
    "actor_type",
    "actor_id",
    "action",
    "subject_type",
    "subject_id",
    "run_id",
    "details",
    "prev_hash",
    "hash",
)
IMPACT = ("id", "run_id", "regulation_id", "from_version_id", "to_version_id", "summary", "created_at")
SIMULATION = ("id", "run_id", "scenario", "parsed", "result", "status", "created_at")
