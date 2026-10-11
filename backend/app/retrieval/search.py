"""Hybrid retrieval: dense (pgvector) + lexical (Postgres FTS) + graph expansion,
fused with Reciprocal Rank Fusion, then reranked by a cross-encoder.

All legs share the same pre-filters, applied inside SQL so they can never be
bypassed by a later stage:
- tenant: public rows (bank_id NULL) or the caller's bank (also enforced by RLS)
- temporal: effective_from <= as_of < effective_to (or open-ended)
- document class / regulation / document ids
- quarantine: chunks flagged by the injection scanner are excluded
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.embeddings import Embedder, Reranker

RRF_K = 60
LEG_WEIGHTS = {"dense": 1.0, "lexical": 0.8, "graph": 0.5}
DENSE_K = 40
LEXICAL_K = 40


@dataclass
class SearchFilters:
    doc_classes: Sequence[str] | None = None
    regulation_ids: Sequence[uuid.UUID] | None = None
    document_ids: Sequence[uuid.UUID] | None = None
    as_of: date | None = None
    current_only: bool = True


@dataclass
class Hit:
    chunk_id: uuid.UUID
    section_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID | None
    header: str
    text: str
    section_path: str
    doc_class: str
    document_title: str | None
    effective_from: date | None
    fused_score: float = 0.0
    rerank_score: float | None = None
    legs: dict[str, int] = field(default_factory=dict)  # leg -> rank

    def citation(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "section_id": str(self.section_id),
            "document_id": str(self.document_id),
            "section_path": self.section_path,
            "document_title": self.document_title,
            "header": self.header,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
        }


def _where(filters: SearchFilters, params: dict[str, Any]) -> str:
    clauses = ["c.quarantined = false"]
    if filters.current_only and filters.as_of is None:
        clauses.append("c.is_current = true")
    if filters.as_of is not None:
        params["as_of"] = filters.as_of
        clauses.append("(c.effective_from IS NULL OR c.effective_from <= :as_of)")
        clauses.append("(c.effective_to IS NULL OR c.effective_to > :as_of)")
    if filters.doc_classes:
        params["doc_classes"] = list(filters.doc_classes)
        clauses.append("c.doc_class = ANY(:doc_classes)")
    if filters.document_ids:
        params["document_ids"] = list(filters.document_ids)
        clauses.append("c.document_id = ANY(:document_ids)")
    if filters.regulation_ids:
        params["regulation_ids"] = list(filters.regulation_ids)
        clauses.append(
            "c.version_id IN (SELECT id FROM regulation_versions WHERE regulation_id = ANY(:regulation_ids))"
        )
    return " AND ".join(clauses)


_SELECT = """
    SELECT c.id, c.section_id, c.document_id, c.version_id, c.header, c.text, s.section_path,
           c.doc_class, d.title, c.effective_from
    FROM chunks c JOIN sections s ON s.id = c.section_id JOIN documents d ON d.id = c.document_id
"""


def _hit(row: Any) -> Hit:
    return Hit(
        chunk_id=row[0],
        section_id=row[1],
        document_id=row[2],
        version_id=row[3],
        header=row[4],
        text=row[5],
        section_path=row[6],
        doc_class=row[7],
        document_title=row[8],
        effective_from=row[9],
    )


class HybridSearcher:
    def __init__(self, embedder: Embedder, reranker: Reranker) -> None:
        self._embedder = embedder
        self._reranker = reranker

    async def dense(self, session: AsyncSession, query: str, filters: SearchFilters, k: int = DENSE_K) -> list[Hit]:
        vector = await self._embedder.embed_query(query)
        params: dict[str, Any] = {"vec": str(vector), "k": k}
        sql = f"{_SELECT} WHERE {_where(filters, params)} AND c.embedding IS NOT NULL ORDER BY c.embedding <=> CAST(:vec AS vector) LIMIT :k"
        rows = (await session.execute(text(sql), params)).all()
        return [_hit(r) for r in rows]

    async def lexical(self, session: AsyncSession, query: str, filters: SearchFilters, k: int = LEXICAL_K) -> list[Hit]:
        params: dict[str, Any] = {"q": query, "k": k}
        # OR semantics over the query's lexemes (recall); ts_rank_cd orders by density.
        sql = f"""
            WITH q AS (
                SELECT to_tsquery('english', COALESCE(NULLIF(array_to_string(
                    tsvector_to_array(to_tsvector('english', :q)), ' | '), ''), 'zzzznomatch')) AS tq
            )
            {_SELECT} CROSS JOIN q
            WHERE {_where(filters, params)} AND c.tsv @@ q.tq
            ORDER BY ts_rank_cd(c.tsv, q.tq) DESC LIMIT :k
        """
        rows = (await session.execute(text(sql), params)).all()
        return [_hit(r) for r in rows]

    async def graph_neighbours(self, session: AsyncSession, seeds: list[Hit], filters: SearchFilters) -> list[Hit]:
        """Chunks of sections linked to the seeds by REFERENCES edges or sharing a parent."""
        if not seeds:
            return []
        params: dict[str, Any] = {"seed_sections": [h.section_id for h in seeds]}
        sql = f"""
            {_SELECT}
            WHERE {_where(filters, params)} AND c.section_id IN (
                SELECT e.dst_id FROM kg_edges e
                WHERE e.src_type = 'section' AND e.rel = 'REFERENCES' AND e.src_id = ANY(:seed_sections)
                UNION
                SELECT e.src_id FROM kg_edges e
                WHERE e.dst_type = 'section' AND e.rel = 'REFERENCES' AND e.dst_id = ANY(:seed_sections)
            )
            LIMIT 20
        """
        rows = (await session.execute(text(sql), params)).all()
        return [_hit(r) for r in rows]

    async def search(
        self,
        session: AsyncSession,
        query: str,
        filters: SearchFilters | None = None,
        *,
        top_k: int = 8,
        rerank_candidates: int = 30,
        use_graph: bool = True,
    ) -> list[Hit]:
        filters = filters or SearchFilters()
        legs: dict[str, list[Hit]] = {
            "dense": await self.dense(session, query, filters),
            "lexical": await self.lexical(session, query, filters),
        }
        if use_graph:
            seeds = _fuse(legs)[:10]
            legs["graph"] = await self.graph_neighbours(session, seeds, filters)
        fused = _fuse(legs)[:rerank_candidates]
        if not fused:
            return []
        scores = await self._reranker.score(query, [f"{h.header}\n{h.text}" for h in fused])
        for hit, score in zip(fused, scores, strict=True):
            hit.rerank_score = round(float(score), 4)
        fused.sort(key=lambda h: h.rerank_score or 0.0, reverse=True)
        return fused[:top_k]


def _fuse(legs: dict[str, list[Hit]]) -> list[Hit]:
    merged: dict[uuid.UUID, Hit] = {}
    for leg, hits in legs.items():
        weight = LEG_WEIGHTS[leg]
        for rank, hit in enumerate(hits, start=1):
            existing = merged.setdefault(hit.chunk_id, hit)
            existing.fused_score += weight / (RRF_K + rank)
            existing.legs[leg] = rank
    return sorted(merged.values(), key=lambda h: h.fused_score, reverse=True)
