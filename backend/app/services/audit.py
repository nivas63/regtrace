"""Append-only, hash-chained audit trail.

One chain per bank (plus one for system/public events). Each entry's hash covers
the previous hash and the canonical JSON of the entry, so any edit or deletion of a
past row breaks verification. A transaction-scoped advisory lock serialises writers
of the same chain so two concurrent inserts can never fork it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

GENESIS = "0" * 64


@dataclass(frozen=True)
class Actor:
    type: str  # USER | AGENT | SYSTEM
    id: str

    @classmethod
    def system(cls) -> Actor:
        return cls("SYSTEM", "system")

    @classmethod
    def agent(cls, name: str) -> Actor:
        return cls("AGENT", name)

    @classmethod
    def user(cls, user_id: uuid.UUID) -> Actor:
        return cls("USER", str(user_id))


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(entry)).encode()).hexdigest()


def _entry(row: AuditLog) -> dict[str, Any]:
    return {
        "bank_id": str(row.bank_id) if row.bank_id else None,
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "action": row.action,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "run_id": str(row.run_id) if row.run_id else None,
        "details": row.details,
    }


async def record(
    session: AsyncSession,
    *,
    bank_id: uuid.UUID | None,
    actor: Actor,
    action: str,
    subject_type: str | None = None,
    subject_id: uuid.UUID | str | None = None,
    run_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    chain = str(bank_id) if bank_id else "system"
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:chain))"), {"chain": f"audit:{chain}"})
    last = await session.scalar(
        select(AuditLog.hash)
        .where(AuditLog.bank_id.is_(None) if bank_id is None else AuditLog.bank_id == bank_id)
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    row = AuditLog(
        bank_id=bank_id,
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        subject_type=subject_type,
        subject_id=str(subject_id) if subject_id is not None else None,
        run_id=run_id,
        details=json.loads(_canonical(details or {})),
        prev_hash=last or GENESIS,
        hash="",
    )
    row.hash = compute_hash(row.prev_hash, _entry(row))
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    entries: int
    first_broken_id: int | None = None


async def verify_chain(session: AsyncSession, bank_id: uuid.UUID | None) -> ChainVerification:
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.bank_id.is_(None) if bank_id is None else AuditLog.bank_id == bank_id)
            .order_by(AuditLog.id)
        )
    ).all()
    prev = GENESIS
    for row in rows:
        if row.prev_hash != prev or compute_hash(prev, _entry(row)) != row.hash:
            return ChainVerification(valid=False, entries=len(rows), first_broken_id=row.id)
        prev = row.hash
    return ChainVerification(valid=True, entries=len(rows))
