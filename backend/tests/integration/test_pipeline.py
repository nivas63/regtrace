"""End-to-end pipeline test on real Postgres with the TEST-ONLY oracle LLM.

Covers: ingestion of every document class, version linking, obligation extraction
with quote verification, applicability, mapping, evidence assessment, scoring, gap
detection, risk, remediation, cross-regulation analysis, v1 -> v2 change detection
with lineage, impact report, and simulation - all through the workflow engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.container import build_services
from app.llm.factory import LLMStack
from app.llm.rate_limiter import RateLimiter
from app.llm.router import LLMRouter, RouterPolicy
from app.llm.service import LLMService
from app.llm.structured import StructuredLLM
from app.llm.types import ModelTier
from app.models import (
    ComplianceFinding,
    ControlObligation,
    Obligation,
    RegulatoryChange,
    RegulatoryConflict,
    RemediationAction,
    Simulation,
    WorkflowRun,
    WorkflowStep,
)
from app.services.seed import drain_queue, seed_demo
from tests.integration.oracle import OracleProvider

DATA = Path(__file__).resolve().parents[3] / "data"


@pytest.fixture
async def services(db_settings: Settings):  # type: ignore[no-untyped-def]
    async with build_services(db_settings) as svc:
        oracle = OracleProvider()
        router = LLMRouter(
            {"oracle": oracle}, {t: ["oracle"] for t in ModelTier}, RateLimiter({}), RouterPolicy(max_retries=0)
        )
        svc.llm_stack = LLMStack(router=router, structured=StructuredLLM(router))
        svc.llm = LLMService(svc.llm_stack, svc.db, db_settings)
        yield svc


@pytest.mark.skipif(not (DATA / "synthetic" / "manifest.json").exists(), reason="demo corpus not generated")
async def test_full_pipeline(services) -> None:  # type: ignore[no-untyped-def]
    result = await seed_demo(services, DATA, password="test-password")
    failed = [r for r in result.runs if r["status"] != "SUCCEEDED"]
    assert not failed, failed
    bank = result.bank_id

    async with services.db.tenant_session(bank) as s:
        obligations = await s.scalar(select(func.count()).select_from(Obligation).where(Obligation.status == "ACTIVE"))
        mappings = await s.scalar(
            select(func.count()).select_from(ControlObligation).where(ControlObligation.mapping_type != "NONE")
        )
        gap_types = set(
            (await s.scalars(select(ComplianceFinding.gap_type).where(ComplianceFinding.status == "OPEN"))).all()
        )
        remediations = await s.scalar(select(func.count()).select_from(RemediationAction))
        conflicts = await s.scalar(select(func.count()).select_from(RegulatoryConflict))
    # Golden v1 corpus: KYC v1 (23) + IT governance (13) + data retention (5) = 41 obligations.
    assert obligations == 41
    # The scripted mid-extraction outage forced a step retry; it must not duplicate rows.
    async with services.db.system_session() as s2:
        failed_attempts = await s2.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(WorkflowStep.step_name == "extract_obligations", WorkflowStep.status == "FAILED")
        )
    assert failed_attempts >= 1
    assert mappings >= 20
    assert {"MISSING_CONTROL", "MISSING_EVIDENCE"} <= gap_types
    assert remediations >= 1
    assert conflicts >= 1

    # --- re-adjudication must persist updates to EXISTING mapping rows ---------------
    async with services.db.tenant_session(bank) as s:
        tampered = await s.scalar(select(ControlObligation).where(ControlObligation.mapping_type == "FULL").limit(1))
        assert tampered is not None
        tampered_id = tampered.id
        tampered.mapping_type, tampered.reason = "PARTIAL", "tampered by test"
    await services.engine.enqueue(
        bank_id=bank, workflow_type="compliance_assessment", input={"scope": "all"}, triggered_by="test"
    )
    runs = await drain_queue(services)
    assert runs and runs[-1]["status"] == "SUCCEEDED", runs
    async with services.db.tenant_session(bank) as s:
        restored = await s.get(ControlObligation, tampered_id)
    assert restored is not None and restored.mapping_type == "FULL" and restored.reason != "tampered by test"

    # --- regulatory change: upload v2 -----------------------------------------------
    from app.domain.types import DocClass
    from app.services.ingestion import IngestionService

    v2 = DATA / "synthetic" / "regulations" / "SYN_KYC_Master_Direction_2025_v2.pdf"
    await IngestionService(services).upload(
        bank_id=bank, user_id=None, filename=v2.name, data=v2.read_bytes(), doc_class=DocClass.REGULATION
    )
    runs = await drain_queue(services)
    assert runs and runs[-1]["status"] == "SUCCEEDED", runs
    async with services.db.tenant_session(bank) as s:
        changes = {c.section_path: c for c in (await s.scalars(select(RegulatoryChange))).all()}
        run = await s.get(WorkflowRun, __import__("uuid").UUID(runs[-1]["run_id"]))
    assert {"6.2", "7.1", "11.2"} <= {p for p, c in changes.items() if c.is_material}
    assert "8.3" in changes and changes["8.3"].change_type == "ADDED"
    assert not changes["3.2"].is_material
    assert run is not None and "diff_versions" in run.plan and run.state.get("impact_assessment_id")

    # --- what-if -------------------------------------------------------------------
    async with services.db.tenant_session(bank) as s:
        sim = Simulation(bank_id=bank, scenario="What if DR drills must be performed every quarter?")
        s.add(sim)
        await s.flush()
        sim_id = sim.id
    await services.engine.enqueue(
        bank_id=bank, workflow_type="run_simulation", input={"simulation_id": str(sim_id)}, triggered_by="test"
    )
    await drain_queue(services)
    async with services.db.tenant_session(bank) as s:
        sim = await s.get(Simulation, sim_id)
    assert sim is not None and sim.status in ("COMPLETED", "INSUFFICIENT")
