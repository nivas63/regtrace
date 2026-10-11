from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import Database
from app.models import AuditLog, Control
from app.services import audit
from tests.integration.conftest import make_bank


def _control(bank_id: object, key: str) -> Control:
    return Control(
        bank_id=bank_id,
        control_key=key,
        name=key,
        description="test control",
        control_type="PREVENTIVE",
        automation="MANUAL",
    )


async def test_rls_isolates_banks(database: Database, owner_exec: object) -> None:
    bank_a = await make_bank(owner_exec)
    bank_b = await make_bank(owner_exec)
    async with database.tenant_session(bank_a) as s:
        s.add(_control(bank_a, "C-A"))
    async with database.tenant_session(bank_b) as s:
        s.add(_control(bank_b, "C-B"))

    async with database.tenant_session(bank_a) as s:
        keys = (await s.scalars(select(Control.control_key))).all()
    assert keys == ["C-A"]

    # Without tenant context no bank's rows are visible at all.
    async with database.system_session() as s:
        assert await s.scalar(select(func.count()).select_from(Control)) == 0


async def test_rls_blocks_cross_tenant_writes(database: Database, owner_exec: object) -> None:
    bank_a = await make_bank(owner_exec)
    bank_b = await make_bank(owner_exec)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with database.tenant_session(bank_a) as s:
            s.add(_control(bank_b, "C-SMUGGLED"))


async def test_audit_chain_verifies_and_detects_tampering(database: Database, owner_exec: object) -> None:
    bank = await make_bank(owner_exec)
    async with database.tenant_session(bank) as s:
        for i in range(3):
            await audit.record(s, bank_id=bank, actor=audit.Actor.system(), action="test.event", details={"i": i})
    async with database.tenant_session(bank) as s:
        result = await audit.verify_chain(s, bank)
    assert result.valid and result.entries == 3

    # The app role cannot rewrite history...
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with database.tenant_session(bank) as s:
            await s.execute(AuditLog.__table__.update().values(action="edited"))

    # ...and if the owner does, verification pinpoints the broken entry.
    rows = await owner_exec(  # type: ignore[operator]
        "SELECT id FROM audit_log WHERE bank_id = :b ORDER BY id", b=bank
    )
    await owner_exec(  # type: ignore[operator]
        "UPDATE audit_log SET details = '{\"i\": 99}'::jsonb WHERE id = :id", id=rows[1][0]
    )
    async with database.tenant_session(bank) as s:
        tampered = await audit.verify_chain(s, bank)
    assert not tampered.valid and tampered.first_broken_id == rows[1][0]
