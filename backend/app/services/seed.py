"""Load the SYNTHETIC demo bank and corpus through the real ingestion path.

Every file goes through upload -> validation -> workflow, exactly like a user upload;
nothing is inserted behind the pipeline's back except the bank and its users.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy import select

from app.container import Services
from app.domain.types import DocClass
from app.models import Bank, User, WorkflowRun
from app.observability.logging import get_logger
from app.security.passwords import hash_password
from app.services.ingestion import IngestionService

_log = get_logger("regtrace.seed")

BANK_NAME = "Aravali Bank Ltd (Synthetic)"
BANK_PROFILE: dict[str, Any] = {
    "entity_type": "scheduled commercial bank",
    "entity_type_aliases": ["scheduled commercial bank", "bank", "regulated entity", "data fiduciary", "re"],
    "jurisdictions": ["IN"],
    "products": ["retail deposits", "corporate accounts", "digital onboarding", "v-cip", "cards"],
    "offers_vcip": True,
    "customer_count": 2_400_000,
}
USERS = [
    ("admin@aravali.example", "Asha Admin", "admin"),
    ("analyst@aravali.example", "Ravi Analyst", "analyst"),
    ("reviewer@aravali.example", "Meera Reviewer", "reviewer"),
    ("auditor@aravali.example", "Kiran Auditor", "auditor"),
]


@dataclass
class SeedResult:
    bank_id: uuid.UUID
    runs: list[dict[str, Any]]


async def ensure_bank(services: Services, password: str) -> uuid.UUID:
    async with services.db.system_session() as session:
        bank = await session.scalar(select(Bank).where(Bank.name == BANK_NAME))
        if bank is None:
            bank = Bank(name=BANK_NAME, profile=BANK_PROFILE, is_synthetic=True)
            session.add(bank)
            await session.flush()
        for email, name, role in USERS:
            if await session.scalar(select(User.id).where(User.email == email)) is None:
                session.add(
                    User(bank_id=bank.id, email=email, full_name=name, role=role, password_hash=hash_password(password))
                )
        return bank.id


async def drain_queue(services: Services, worker_id: str = "seed") -> list[dict[str, Any]]:
    """Execute queued runs in this process until the queue is empty."""
    done = []
    while (run_id := await services.engine.claim_next(worker_id)) is not None:
        await services.engine.execute(run_id)
        async with services.db.system_session() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is not None:
                done.append(
                    {
                        "run_id": str(run.id),
                        "workflow": run.workflow_type,
                        "status": run.status,
                        "error": run.error,
                        "input": run.input,
                    }
                )
                _log.info("seed.run", workflow=run.workflow_type, status=run.status, error=run.error)
    return done


def _evidence_register(bank_dir: Path) -> dict[str, dict[str, Any]]:
    workbook = openpyxl.load_workbook(bank_dir / "evidence_register.xlsx", read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows = list(sheet.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    register = {}
    for row in rows[1:]:
        record = dict(zip(header, row, strict=False))
        register[str(record["File"])] = {
            "control_key": record["Control ID"],
            "title": record["Title"],
            "evidence_type": record["Evidence Type"],
            "collected_on": str(record["Collected On"]),
            "period_start": str(record["Period Start"]),
            "period_end": str(record["Period End"]),
            "source_system": record["Source System"],
            "authenticity": record["Authenticity"],
        }
    workbook.close()
    return register


async def seed_demo(
    services: Services,
    data_dir: Path,
    *,
    include_v2: bool = False,
    include_amendment: bool = False,
    password: str | None = None,
) -> SeedResult:
    password = password or os.environ.get("SEED_PASSWORD", "RegTrace!2026")
    bank_id = await ensure_bank(services, password)
    ingestion = IngestionService(services)
    synthetic = data_dir / "synthetic"
    bank_dir, reg_dir = synthetic / "bank", synthetic / "regulations"
    runs: list[dict[str, Any]] = []

    async def upload(path: Path, doc_class: DocClass, attributes: dict[str, Any] | None = None) -> None:
        outcome = await ingestion.upload(
            bank_id=bank_id,
            user_id=None,
            filename=path.name,
            data=path.read_bytes(),
            doc_class=doc_class,
            attributes=attributes,
        )
        _log.info("seed.upload", file=path.name, duplicate=outcome.duplicate)
        runs.extend(await drain_queue(services))

    await upload(bank_dir / "control_matrix.xlsx", DocClass.CONTROL_MATRIX)
    await upload(bank_dir / "control_tests.xlsx", DocClass.CONTROL_TESTS)
    for policy in ("Aravali_KYC_AML_Policy.pdf", "Aravali_Information_Security_Policy.pdf"):
        await upload(bank_dir / policy, DocClass.POLICY)
    register = _evidence_register(bank_dir)
    for filename, attributes in register.items():
        await upload(bank_dir / "evidence" / filename, DocClass.EVIDENCE, attributes)
    for name in (
        "SYN_IT_Governance_Master_Direction_2024.pdf",
        "SYN_Data_Retention_Guidelines_2025.pdf",
        "SYN_KYC_Master_Direction_2024_v1.pdf",
    ):
        await upload(reg_dir / name, DocClass.REGULATION)
    if include_v2:
        await upload(reg_dir / "SYN_KYC_Master_Direction_2025_v2.pdf", DocClass.REGULATION)
    if include_amendment:
        await upload(reg_dir / "SYN_IT_Governance_Amendment_2025.pdf", DocClass.AMENDMENT)
    return SeedResult(bank_id=bank_id, runs=runs)
