"""The tool catalogue: deterministic engines and data access exposed to agents.

Each tool: typed input, allow-listed agents, timeout, persisted invocation record.
Agents reason (LLM); tools compute (code). A tool call outside an agent's
allow-list is denied and logged - agents cannot call arbitrary functions.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.knowledge_graph.graph import traverse
from app.models import Bank, Control, Evidence
from app.retrieval.search import HybridSearcher, SearchFilters
from app.services import assessment
from app.workflows.tools import Tool, ToolEnv, ToolRegistry

SUPERVISOR = "supervisor"
ENGINES = frozenset({SUPERVISOR, "assessment_engine", "risk_engine", "gap_engine", "simulation"})


class SearchRegulationsInput(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    as_of: date | None = None
    top_k: int = Field(default=8, ge=1, le=20)


async def _search_regulations(env: ToolEnv, args: SearchRegulationsInput) -> list[dict[str, Any]]:
    searcher = HybridSearcher(env.services.embedder, env.services.reranker)
    async with env.services.db.tenant_session(env.bank_id) as session:
        hits = await searcher.search(
            session, args.query, SearchFilters(doc_classes=["REGULATION"], as_of=args.as_of), top_k=args.top_k
        )
    return [{**h.citation(), "text": h.text, "rerank_score": h.rerank_score} for h in hits]


class ScopeInput(BaseModel):
    obligation_ids: list[uuid.UUID] | None = Field(default=None, max_length=2000)


async def _score_controls(env: ToolEnv, args: ScopeInput) -> dict[str, Any]:
    async with env.services.db.tenant_session(env.bank_id) as session:
        return await assessment.score_controls(session, env.bank_id, env.run_id, args.obligation_ids)


async def _identify_gaps(env: ToolEnv, args: ScopeInput) -> dict[str, Any]:
    async with env.services.db.tenant_session(env.bank_id) as session:
        return await assessment.reconcile_gaps(session, env.bank_id, env.run_id, args.obligation_ids)


class EmptyInput(BaseModel):
    pass


async def _calculate_risk(env: ToolEnv, args: EmptyInput) -> dict[str, Any]:
    async with env.services.db.tenant_session(env.bank_id) as session:
        return await assessment.score_open_risks(session, env.bank_id, env.run_id)


class ImpactedControlsInput(BaseModel):
    obligation_ids: list[uuid.UUID] = Field(max_length=2000)


async def _find_impacted_controls(env: ToolEnv, args: ImpactedControlsInput) -> list[str]:
    found: set[uuid.UUID] = set()
    async with env.services.db.tenant_session(env.bank_id) as session:
        for oid in args.obligation_ids:
            for edge in await traverse(session, ("obligation", oid), rels=["SATISFIED_BY"], max_depth=1):
                if edge.dst_type == "control":
                    found.add(edge.dst_id)
    return sorted(str(c) for c in found)


async def _list_controls(env: ToolEnv, args: EmptyInput) -> list[str]:
    async with env.services.db.tenant_session(env.bank_id) as session:
        ids = (
            await session.scalars(select(Control.id).where(Control.bank_id == env.bank_id, Control.status == "ACTIVE"))
        ).all()
    return [str(i) for i in ids]


class ControlInput(BaseModel):
    control_id: uuid.UUID


async def _get_evidence(env: ToolEnv, args: ControlInput) -> list[dict[str, Any]]:
    async with env.services.db.tenant_session(env.bank_id) as session:
        rows = (await session.scalars(select(Evidence).where(Evidence.control_id == args.control_id))).all()
    return [
        {
            "id": str(e.id),
            "title": e.title,
            "type": e.evidence_type,
            "collected_on": e.collected_on.isoformat() if e.collected_on else None,
            "authenticity": e.authenticity,
        }
        for e in rows
    ]


async def _bank_profile(env: ToolEnv, args: EmptyInput) -> dict[str, Any]:
    async with env.services.db.tenant_session(env.bank_id) as session:
        bank = await session.get(Bank, env.bank_id)
    return dict(bank.profile) if bank else {}


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            "search_regulations",
            "Hybrid search over regulation text (temporal filter)",
            SearchRegulationsInput,
            _search_regulations,
            frozenset({SUPERVISOR, "qa", "simulation", "control_mapper"}),
            timeout_s=60,
        )
    )
    registry.register(
        Tool(
            "score_controls",
            "Deterministic control-effectiveness scoring",
            ScopeInput,
            _score_controls,
            ENGINES,
            timeout_s=300,
            side_effects=True,
        )
    )
    registry.register(
        Tool(
            "identify_gaps",
            "Deterministic gap identification and reconciliation",
            ScopeInput,
            _identify_gaps,
            ENGINES,
            timeout_s=300,
            side_effects=True,
        )
    )
    registry.register(
        Tool(
            "calculate_risk",
            "Deterministic risk scoring of open findings",
            EmptyInput,
            _calculate_risk,
            ENGINES,
            timeout_s=120,
            side_effects=True,
        )
    )
    registry.register(
        Tool(
            "find_impacted_controls",
            "Graph traversal obligation -> SATISFIED_BY -> control",
            ImpactedControlsInput,
            _find_impacted_controls,
            frozenset({SUPERVISOR, "change_analyst", "simulation"}),
            timeout_s=60,
        )
    )
    registry.register(
        Tool(
            "list_controls",
            "Active controls of the bank",
            EmptyInput,
            _list_controls,
            frozenset({SUPERVISOR, "control_mapper"}),
            timeout_s=30,
        )
    )
    registry.register(
        Tool(
            "get_evidence",
            "Evidence artefacts linked to a control",
            ControlInput,
            _get_evidence,
            frozenset({SUPERVISOR, "evidence_assessor", "remediation_planner"}),
            timeout_s=30,
        )
    )
    registry.register(
        Tool(
            "get_bank_profile",
            "Applicability profile of the bank",
            EmptyInput,
            _bank_profile,
            frozenset({SUPERVISOR, "applicability"}),
            timeout_s=10,
        )
    )
