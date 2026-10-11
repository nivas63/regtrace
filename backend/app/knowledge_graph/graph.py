"""Compliance knowledge graph on Postgres (typed edges + recursive traversal).

Node types: regulation, version, section, obligation, control, evidence, test,
finding, remediation, business_unit, system.

Relationships (src -[rel]-> dst):
  regulation HAS_VERSION version; version SUPERSEDES version; version AMENDS version
  version HAS_SECTION section; section HAS_OBLIGATION obligation; section REFERENCES section
  obligation SATISFIED_BY control; obligation EQUIVALENT_TO obligation;
  obligation POTENTIAL_CONFLICT obligation; obligation EVOLVED_TO obligation
  control GENERATES_EVIDENCE evidence; control TESTED_BY test; control OWNED_BY business_unit
  control RUNS_ON system; finding VIOLATES obligation; finding AFFECTS control;
  finding MITIGATED_BY remediation

Why a graph as well as vectors: vectors answer "what text is similar"; impact
analysis asks "what is *connected*" (section -> obligation -> control -> evidence),
which is a traversal, not a similarity search.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KGEdge


async def upsert_edge(
    session: AsyncSession,
    *,
    src: tuple[str, uuid.UUID],
    rel: str,
    dst: tuple[str, uuid.UUID],
    bank_id: uuid.UUID | None = None,
    props: dict[str, Any] | None = None,
    valid_from: date | None = None,
) -> None:
    stmt = insert(KGEdge).values(
        id=uuid.uuid4(),
        bank_id=bank_id,
        src_type=src[0],
        src_id=src[1],
        rel=rel,
        dst_type=dst[0],
        dst_id=dst[1],
        props=props or {},
        valid_from=valid_from,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_kg_edges_src_type",
        set_={"props": stmt.excluded.props, "valid_to": None, "updated_at": text("now()")},
    )
    await session.execute(stmt)


async def close_edges(
    session: AsyncSession,
    *,
    src: tuple[str, uuid.UUID] | None = None,
    dst: tuple[str, uuid.UUID] | None = None,
    rel: str,
    as_of: date,
) -> None:
    clauses, params = ["rel = :rel", "valid_to IS NULL"], {"rel": rel, "as_of": as_of}
    if src:
        clauses += ["src_type = :st", "src_id = :si"]
        params |= {"st": src[0], "si": src[1]}
    if dst:
        clauses += ["dst_type = :dt", "dst_id = :di"]
        params |= {"dt": dst[0], "di": dst[1]}
    await session.execute(text(f"UPDATE kg_edges SET valid_to = :as_of WHERE {' AND '.join(clauses)}"), params)


@dataclass(frozen=True)
class GraphEdge:
    src_type: str
    src_id: uuid.UUID
    rel: str
    dst_type: str
    dst_id: uuid.UUID
    depth: int


async def traverse(
    session: AsyncSession,
    start: tuple[str, uuid.UUID],
    *,
    rels: list[str],
    max_depth: int = 4,
    direction: str = "out",
) -> list[GraphEdge]:
    """Breadth-first traversal over current (valid_to IS NULL) edges of the given types.
    Cycle-safe: a node is never expanded twice (path array check)."""
    if direction == "out":
        step = "e.src_type = w.node_type AND e.src_id = w.node_id"
        nxt = "e.dst_type, e.dst_id"
    else:
        step = "e.dst_type = w.node_type AND e.dst_id = w.node_id"
        nxt = "e.src_type, e.src_id"
    sql = f"""
        WITH RECURSIVE walk(node_type, node_id, src_type, src_id, rel, depth, path) AS (
            SELECT CAST(:t AS varchar), CAST(:i AS uuid), CAST(NULL AS varchar), CAST(NULL AS uuid),
                   CAST(NULL AS varchar), 0, ARRAY[CAST(:i AS uuid)]
            UNION ALL
            SELECT {nxt}, w.node_type, w.node_id, e.rel, w.depth + 1,
                   w.path || {nxt.split(", ")[1]}
            FROM walk w JOIN kg_edges e ON {step}
            WHERE w.depth < :max_depth AND e.valid_to IS NULL AND e.rel = ANY(:rels)
              AND NOT ({nxt.split(", ")[1]} = ANY(w.path))
        )
        SELECT src_type, src_id, rel, node_type, node_id, depth FROM walk WHERE depth > 0
    """
    rows = (
        await session.execute(text(sql), {"t": start[0], "i": start[1], "rels": rels, "max_depth": max_depth})
    ).all()
    return [GraphEdge(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]


async def subgraph(session: AsyncSession, node_ids: list[uuid.UUID], limit: int = 400) -> list[dict[str, Any]]:
    """Edges touching any of the given nodes (for the UI graph view)."""
    rows = (
        await session.execute(
            text(
                "SELECT src_type, src_id, rel, dst_type, dst_id, props FROM kg_edges "
                "WHERE valid_to IS NULL AND (src_id = ANY(:ids) OR dst_id = ANY(:ids)) LIMIT :limit"
            ),
            {"ids": node_ids, "limit": limit},
        )
    ).all()
    return [
        {"src_type": r[0], "src_id": str(r[1]), "rel": r[2], "dst_type": r[3], "dst_id": str(r[4]), "props": r[5]}
        for r in rows
    ]
