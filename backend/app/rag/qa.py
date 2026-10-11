"""Grounded question answering.

query -> understanding (classify, rewrite, sub-queries, as-of date)       [LLM fast]
      -> hybrid retrieval per query (dense + lexical + graph, RRF)        [deterministic]
      -> rerank + score threshold (abstain below it)                      [cross-encoder]
      -> compliance facts for status questions (findings, controls)        [SQL]
      -> answer with per-claim citations                                   [LLM reasoning]
      -> citation closure + lexical support check; unsupported claims dropped
      -> "Insufficient evidence" if nothing grounded survives
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select

from app.agents.schemas import GroundedAnswer, QueryUnderstanding
from app.container import Services
from app.llm.errors import LLMError
from app.llm.prompts import DocumentBlock, load_prompt, spotlight
from app.llm.service import CallContext
from app.llm.types import ModelTier
from app.models import (
    ComplianceFinding,
    Control,
    ControlAssessment,
    ControlObligation,
    Obligation,
    RiskAssessment,
)
from app.retrieval.search import Hit, HybridSearcher, SearchFilters

_TOKEN = re.compile(r"[a-z0-9]+")
MIN_CLAIM_SUPPORT = 0.15


@dataclass
class QAResult:
    answer: str
    insufficient_evidence: bool
    claims: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    query_type: str
    rewritten_query: str
    dropped_claims: int
    retrieval: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0


def _support(claim: str, sources: list[str]) -> float:
    claim_tokens = {t for t in _TOKEN.findall(claim.lower()) if len(t) > 3}
    if not claim_tokens:
        return 0.0
    source_tokens = {t for s in sources for t in _TOKEN.findall(s.lower())}
    return len(claim_tokens & source_tokens) / len(claim_tokens)


async def answer_question(services: Services, bank_id: uuid.UUID, user_id: uuid.UUID, question: str) -> QAResult:
    started = time.perf_counter()
    ctx = CallContext(agent="qa")
    understanding = QueryUnderstanding(query_type="REGULATION_LOOKUP", rewritten_query=question)
    if services.llm.available:
        try:
            understanding = (
                await services.llm.structured(
                    ctx=ctx,
                    prompt=load_prompt("query_understanding"),
                    user_content=question,
                    schema=QueryUnderstanding,
                    tier=ModelTier.FAST,
                    max_output_tokens=500,
                )
            ).value
        except LLMError:
            pass
    as_of = None
    if understanding.as_of_date:
        try:
            as_of = date.fromisoformat(understanding.as_of_date[:10])
        except ValueError:
            as_of = None

    searcher = HybridSearcher(services.embedder, services.reranker)
    queries = [understanding.rewritten_query, *understanding.sub_queries][:3]
    hits: dict[uuid.UUID, Hit] = {}
    async with services.db.tenant_session(bank_id) as session:
        for query in queries:
            for hit in await searcher.search(
                session, query, SearchFilters(doc_classes=["REGULATION"], as_of=as_of), top_k=6
            ):
                current = hits.get(hit.chunk_id)
                if current is None or (hit.rerank_score or 0) > (current.rerank_score or 0):
                    hits[hit.chunk_id] = hit
        ranked = sorted(hits.values(), key=lambda h: h.rerank_score or 0.0, reverse=True)
        kept = [h for h in ranked if (h.rerank_score or 0.0) >= services.settings.retrieval_min_rerank_score][:8]

        blocks = [
            DocumentBlock(f"S{i + 1}", f"{h.document_title} para {h.section_path}", f"{h.header}\n{h.text}")
            for i, h in enumerate(kept)
        ]
        sources = [
            {"id": f"S{i + 1}", "kind": "regulation", **h.citation(), "rerank_score": h.rerank_score}
            for i, h in enumerate(kept)
        ]
        if understanding.query_type == "COMPLIANCE_STATUS" and kept:
            facts = await _compliance_facts(session, bank_id, [h.section_id for h in kept])
            for j, fact in enumerate(facts):
                fid = f"F{j + 1}"
                blocks.append(DocumentBlock(fid, "bank compliance record", fact["text"]))
                sources.append({"id": fid, "kind": "compliance_record", **fact})

    retrieval_trace = [
        {
            "section_path": h.section_path,
            "document": h.document_title,
            "rerank": h.rerank_score,
            "fused": round(h.fused_score, 4),
            "legs": h.legs,
        }
        for h in ranked[:10]
    ]
    if not kept:
        return QAResult(
            answer="Insufficient evidence: no regulatory text relevant enough to answer was found.",
            insufficient_evidence=True,
            claims=[],
            sources=[],
            query_type=understanding.query_type,
            rewritten_query=understanding.rewritten_query,
            dropped_claims=0,
            retrieval=retrieval_trace,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    if not services.llm.available:
        return QAResult(
            answer="No language model is configured; showing the most relevant sources only.",
            insufficient_evidence=True,
            claims=[],
            sources=sources,
            query_type=understanding.query_type,
            rewritten_query=understanding.rewritten_query,
            dropped_claims=0,
            retrieval=retrieval_trace,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    outcome = await services.llm.structured(
        ctx=ctx,
        prompt=load_prompt("grounded_answer"),
        user_content=f"QUESTION: {question}\n\nSOURCES:\n{spotlight(blocks)}",
        schema=GroundedAnswer,
        tier=ModelTier.REASONING,
        max_output_tokens=2000,
        redact=True,
    )
    text_by_id = {b.id: b.text for b in blocks}
    claims, dropped = [], 0
    for claim in outcome.value.claims:
        valid = [c for c in claim.citations if c in text_by_id]  # citation closure
        support = _support(claim.text, [text_by_id[c] for c in valid])
        if not valid or support < MIN_CLAIM_SUPPORT:
            dropped += 1
            continue
        claims.append({"text": claim.text, "citations": valid, "support": round(support, 2)})
    insufficient = outcome.value.insufficient_evidence or not claims
    answer = outcome.value.answer if claims else "Insufficient evidence to answer from the available sources."
    if dropped and claims:
        answer += f" ({dropped} unsupported statement(s) were removed by the grounding check.)"
    return QAResult(
        answer=answer,
        insufficient_evidence=insufficient,
        claims=claims,
        sources=sources,
        query_type=understanding.query_type,
        rewritten_query=understanding.rewritten_query,
        dropped_claims=dropped,
        retrieval=retrieval_trace,
        latency_ms=(time.perf_counter() - started) * 1000,
    )


async def _compliance_facts(session: Any, bank_id: uuid.UUID, section_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    obligations = (
        await session.scalars(
            select(Obligation).where(Obligation.section_id.in_(section_ids), Obligation.status == "ACTIVE")
        )
    ).all()
    facts: list[dict[str, Any]] = []
    for obligation in obligations[:6]:
        mappings = (
            await session.execute(
                select(ControlObligation, Control, ControlAssessment)
                .join(Control, Control.id == ControlObligation.control_id)
                .outerjoin(
                    ControlAssessment,
                    (ControlAssessment.control_id == Control.id)
                    & (ControlAssessment.obligation_id == obligation.id)
                    & ControlAssessment.is_current,
                )
                .where(
                    ControlObligation.obligation_id == obligation.id,
                    ControlObligation.bank_id == bank_id,
                    ControlObligation.mapping_type != "NONE",
                )
            )
        ).all()
        findings = (
            await session.execute(
                select(ComplianceFinding, RiskAssessment)
                .outerjoin(
                    RiskAssessment, (RiskAssessment.finding_id == ComplianceFinding.id) & RiskAssessment.is_current
                )
                .where(ComplianceFinding.obligation_id == obligation.id, ComplianceFinding.status == "OPEN")
            )
        ).all()
        lines = [f"Obligation {obligation.section_path}: {obligation.requirement_text}"]
        for mapping, control, assessment in mappings:
            lines.append(
                f"Mapped control {control.control_key} ({mapping.mapping_type}, coverage "
                f"{mapping.coverage_score:.0%}, status {mapping.review_status})"
                + (f": rating {assessment.rating}, score {assessment.score}" if assessment else "")
            )
        if not mappings:
            lines.append("No control is mapped to this obligation.")
        for finding, risk in findings:
            lines.append(
                f"Open gap: {finding.gap_type} - {finding.title}"
                + (f" (risk {risk.score}, {risk.priority})" if risk else "")
            )
        facts.append(
            {"text": "\n".join(lines), "obligation_id": str(obligation.id), "section_path": obligation.section_path}
        )
    return facts
