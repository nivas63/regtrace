"""Workflow definitions: which steps exist and how each step is implemented.

process_document (triggered by every upload - autonomous impact analysis):
    parse_and_index -> classify_and_link -> plan (supervisor) -> [planned steps] -> finalize

compliance_assessment : full re-assessment of the bank against all active obligations.
run_simulation        : what-if scenario (no baseline change).
cross_regulation      : clustering + conflict screening, then re-score gaps.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.agents import analysis, change, controls, regulatory, simulation, supervisor
from app.domain.types import DocClass
from app.models import Control, Document, Obligation, Regulation, RegulationVersion
from app.services.ingestion import IngestionService
from app.services.reporting import build_impact_report
from app.workflows.engine import (
    StepContext,
    StepResult,
    StepSpec,
    WorkflowDefinition,
    WorkflowEngine,
)


def _ids(values: list[Any] | None) -> list[uuid.UUID]:
    return [uuid.UUID(str(v)) for v in values or []]


def _doc_id(ctx: StepContext) -> uuid.UUID:
    return uuid.UUID(ctx.run.input["document_id"])


# -- process_document ----------------------------------------------------------------


async def step_parse(ctx: StepContext) -> StepResult:
    report = await IngestionService(ctx.services).parse_and_index(ctx.run.bank_id, _doc_id(ctx))
    ctx.decide(
        "parsed",
        f"{report['paragraphs']} paragraphs, {report['chunks']} chunks"
        + (
            f", {report['quarantined_chunks']} quarantined (possible prompt injection)"
            if report["quarantined_chunks"]
            else ""
        ),
    )
    return StepResult(output=report, state_updates={"parse": {k: v for k, v in report.items() if k != "warnings"}})


async def step_classify_and_link(ctx: StepContext) -> StepResult:
    doc_class = ctx.run.input["doc_class"]
    if doc_class in (DocClass.REGULATION, DocClass.AMENDMENT):
        link = await regulatory.link_regulation(ctx, _doc_id(ctx))
        state = {
            "situation": link.relationship,
            "relationship": link.relationship,
            "regulation_id": str(link.regulation_id),
            "version_id": str(link.version_id),
            "previous_version_id": str(link.previous_version_id) if link.previous_version_id else None,
        }
        return StepResult(
            output={**state, "reason": link.reason, "decided_by": link.decided_by, "confidence": link.confidence},
            state_updates=state,
        )
    ctx.decide("internal_document", f"Bank document of class {doc_class}; no regulation linking")
    return StepResult(output={"situation": doc_class}, state_updates={"situation": doc_class})


async def step_plan(ctx: StepContext) -> StepResult:
    situation = ctx.run.state["situation"]
    async with ctx.services.db.tenant_session(ctx.run.bank_id) as session:
        facts = {
            "situation": situation,
            "controls": await session.scalar(
                select(func.count())
                .select_from(Control)
                .where(Control.bank_id == ctx.run.bank_id, Control.status == "ACTIVE")
            )
            or 0,
            "obligations": await session.scalar(
                select(func.count()).select_from(Obligation).where(Obligation.status == "ACTIVE")
            )
            or 0,
            "regulations": await session.scalar(select(func.count()).select_from(Regulation)) or 0,
            "has_previous_version": bool(ctx.run.state.get("previous_version_id")),
        }
    decision = await supervisor.plan_document_processing(ctx, situation, facts)
    ctx.decide(
        "plan", decision.rationale, decided_by=decision.decided_by, steps=decision.steps, repaired=decision.repaired
    )
    steps = [*decision.steps, "finalize"]
    return StepResult(
        output={"plan": steps, "rationale": decision.rationale, "decided_by": decision.decided_by, "facts": facts},
        state_updates={"plan_rationale": decision.rationale},
        replan=steps,
        replan_reason=decision.rationale,
        stop=not decision.steps,
    )


async def step_diff(ctx: StepContext) -> StepResult:
    diff = await change.diff_versions(
        ctx, uuid.UUID(ctx.run.state["previous_version_id"]), uuid.UUID(ctx.run.state["version_id"])
    )
    out = {
        "added": diff.added,
        "removed": diff.removed,
        "modified": diff.modified,
        "material": diff.material,
        "editorial": diff.editorial,
    }
    return StepResult(
        output=out,
        state_updates={
            "changed_paths": sorted(diff.changed_paths()),
            "editorial_paths": diff.editorial,
            "material_changes": len(diff.material) + len(diff.added),
            "removed_paths": diff.removed,
        },
    )


async def step_apply_amendment(ctx: StepContext) -> StepResult:
    result = await change.apply_amendment(
        ctx, _doc_id(ctx), uuid.UUID(ctx.run.state["version_id"]), uuid.UUID(ctx.run.state["previous_version_id"])
    )
    ctx.decide(
        "amendment_applied",
        f"{len(result['applied'])} instruction(s) applied; consolidated text pending human confirmation",
        failed=len(result["failed"]),
    )
    return StepResult(output=result)


async def step_extract(ctx: StepContext) -> StepResult:
    version_id = uuid.UUID(ctx.run.state["version_id"])
    section_ids = None
    if ctx.run.state.get("previous_version_id"):
        changed = set(ctx.run.state.get("changed_paths", [])) | set(ctx.run.state.get("editorial_paths", []))
        async with ctx.services.db.tenant_session(ctx.run.bank_id) as session:
            from app.models import Section

            section_ids = list(
                (
                    await session.scalars(
                        select(Section.id).where(Section.version_id == version_id, Section.section_path.in_(changed))
                    )
                ).all()
            )
        ctx.decide(
            "incremental_extraction",
            f"Only {len(section_ids)} changed paragraph(s) re-extracted; unchanged obligations are carried forward",
        )
    created, stats = await regulatory.extract_obligations(ctx, version_id, section_ids)
    return StepResult(
        output=stats.as_dict(),
        state_updates={
            "obligation_ids": [str(i) for i in created],
            "new_obligation_ids": [str(i) for i in created],
            "extraction": stats.as_dict(),
        },
    )


async def step_carry_forward(ctx: StepContext) -> StepResult:
    state = ctx.run.state
    result = await regulatory.carry_forward(
        ctx,
        uuid.UUID(state["previous_version_id"]),
        uuid.UUID(state["version_id"]),
        set(state.get("changed_paths", [])) | set(state.get("editorial_paths", [])),
        set(state.get("editorial_paths", [])),
        _ids(state.get("new_obligation_ids")),
    )
    changed = result["modified_ids"] + result["added_ids"]
    ctx.decide("lineage", f"{result['summary']}")
    return StepResult(
        output=result["summary"],
        state_updates={
            "obligation_ids": result["active_obligation_ids"],
            "changed_obligation_ids": changed,
            "lineage": result["summary"],
        },
    )


async def step_applicability(ctx: StepContext) -> StepResult:
    counts = await regulatory.assess_applicability(ctx, _ids(ctx.run.state.get("obligation_ids")))
    return StepResult(output=counts)


async def _scope_obligations(ctx: StepContext) -> list[uuid.UUID]:
    state = ctx.run.state
    if state.get("changed_obligation_ids") is not None and state.get("previous_version_id"):
        return _ids(state["changed_obligation_ids"])
    if state.get("obligation_ids"):
        return _ids(state["obligation_ids"])
    async with ctx.services.db.tenant_session(ctx.run.bank_id) as session:
        return list((await session.scalars(select(Obligation.id).where(Obligation.status == "ACTIVE"))).all())


async def step_map(ctx: StepContext) -> StepResult:
    scope = await _scope_obligations(ctx)
    stats = await controls.map_obligations(ctx, scope)
    return StepResult(output=stats, state_updates={"mapping": stats})


async def step_assess_evidence(ctx: StepContext) -> StepResult:
    if ctx.run.state.get("control_ids"):
        control_ids = _ids(ctx.run.state["control_ids"])
    elif ctx.run.state.get("situation") == DocClass.CONTROL_MATRIX:
        control_ids = _ids(await ctx.tool("supervisor", "list_controls"))
    else:
        scope = await _scope_obligations(ctx)  # changed obligations only, for a new version
        control_ids = _ids(
            await ctx.tool("supervisor", "find_impacted_controls", obligation_ids=[str(o) for o in scope])
        )
    ctx.decide("evidence_scope", f"{len(control_ids)} control(s) in scope for evidence assessment")
    stats = await controls.assess_evidence(ctx, control_ids)
    return StepResult(output=stats, state_updates={"evidence": stats})


def _engine_scope(ctx: StepContext) -> list[str] | None:
    ids = ctx.run.state.get("obligation_ids")
    return (
        [str(i) for i in ids]
        if ids and ctx.run.state.get("situation") in ("NEW_REGULATION", "NEW_VERSION", "AMENDMENT")
        else None
    )


async def step_score_controls(ctx: StepContext) -> StepResult:
    result = await ctx.tool("assessment_engine", "score_controls", obligation_ids=_engine_scope(ctx))
    return StepResult(output=result)


async def step_identify_gaps(ctx: StepContext) -> StepResult:
    result = await ctx.tool("gap_engine", "identify_gaps", obligation_ids=_engine_scope(ctx))
    ctx.decide(
        "gaps", f"{result['new_findings']} new, {result['resolved']} resolved, {result['superseded']} superseded"
    )
    return StepResult(
        output={k: v for k, v in result.items() if k != "new_finding_ids"},
        state_updates={"new_finding_ids": result["new_finding_ids"], "resolved_findings": result["resolved"]},
    )


async def step_score_risk(ctx: StepContext) -> StepResult:
    return StepResult(output=await ctx.tool("risk_engine", "calculate_risk"))


async def step_remediate(ctx: StepContext) -> StepResult:
    new_ids = _ids(ctx.run.state.get("new_finding_ids"))
    stats = await analysis.recommend_remediation(ctx, new_ids or None)
    return StepResult(output=stats)


async def step_cross_regulation(ctx: StepContext) -> StepResult:
    return StepResult(output=await analysis.analyse_cross_regulation(ctx))


async def step_impact_report(ctx: StepContext) -> StepResult:
    report = await build_impact_report(ctx.services, ctx.run.bank_id, ctx.run.id, ctx.run.state)
    ctx.decide("report", report["summary"])
    return StepResult(
        output={"summary": report["summary"], "impact_assessment_id": report["impact_assessment_id"]},
        state_updates={"impact_assessment_id": report["impact_assessment_id"]},
    )


async def step_import_controls(ctx: StepContext) -> StepResult:
    result = await IngestionService(ctx.services).import_control_matrix(ctx.run.bank_id, _doc_id(ctx))
    return StepResult(
        output={k: v for k, v in result.items() if k != "control_ids"},
        state_updates={"control_ids": result["control_ids"]},
    )


async def step_policy_controls(ctx: StepContext) -> StepResult:
    return StepResult(output=await controls.extract_policy_controls(ctx, _doc_id(ctx)))


async def step_import_tests(ctx: StepContext) -> StepResult:
    result = await IngestionService(ctx.services).import_control_tests(ctx.run.bank_id, _doc_id(ctx))
    return StepResult(
        output={k: v for k, v in result.items() if k != "control_ids"},
        state_updates={"control_ids": result["control_ids"]},
    )


async def step_register_evidence(ctx: StepContext) -> StepResult:
    result = await IngestionService(ctx.services).register_evidence(ctx.run.bank_id, _doc_id(ctx))
    ids = [result["control_id"]] if result["control_id"] else []
    if not ids:
        ctx.decide("evidence_unlinked", "Evidence has no matching control; downstream re-assessment skipped")
    return StepResult(output=result, state_updates={"control_ids": ids}, stop=not ids)


async def step_finalize(ctx: StepContext) -> StepResult:
    if "document_id" in ctx.run.input:
        await IngestionService(ctx.services).mark(ctx.run.bank_id, _doc_id(ctx), "PROCESSED")
    return StepResult(output={"status": "PROCESSED"})


# -- other workflows ----------------------------------------------------------------------


async def step_simulate(ctx: StepContext) -> StepResult:
    return StepResult(output=await simulation.run_simulation(ctx, uuid.UUID(ctx.run.input["simulation_id"])))


def _spec(name: str, handler: Any, description: str, agent: str | None = None, **kw: Any) -> StepSpec:
    return StepSpec(name=name, handler=handler, description=description, agent=agent, **kw)


_STEPS = [
    _spec("parse_and_index", step_parse, "Validate, parse, detect structure, chunk, embed", "ingestion"),
    _spec(
        "classify_and_link",
        step_classify_and_link,
        "Document intelligence and version linking",
        "document_intelligence",
    ),
    _spec("plan", step_plan, "Supervisor chooses downstream steps", "supervisor"),
    _spec("diff_versions", step_diff, "Section alignment, diff and semantic change classification", "change_analyst"),
    _spec("apply_amendment", step_apply_amendment, "Apply amendment circular to previous version", "amendment_parser"),
    _spec("extract_obligations", step_extract, "Extract and verify obligations", "obligation_extractor"),
    _spec("carry_forward", step_carry_forward, "Obligation lineage across versions", "change_analyst"),
    _spec("assess_applicability", step_applicability, "Applicability to the bank", "applicability"),
    _spec("map_controls", step_map, "Obligation-to-control mapping", "control_mapper"),
    _spec("assess_evidence", step_assess_evidence, "Evidence sufficiency per criterion", "evidence_assessor"),
    _spec("score_controls", step_score_controls, "Deterministic effectiveness scoring", "assessment_engine"),
    _spec("identify_gaps", step_identify_gaps, "Deterministic gap engine", "gap_engine"),
    _spec("score_risk", step_score_risk, "Deterministic risk engine", "risk_engine"),
    _spec("recommend_remediation", step_remediate, "Remediation planning", "remediation_planner"),
    _spec("cross_regulation", step_cross_regulation, "Clusters and potential conflicts", "cross_regulation"),
    _spec("impact_report", step_impact_report, "Impact report and notification", "supervisor"),
    _spec("import_controls", step_import_controls, "Import control matrix", "ingestion"),
    _spec("extract_policy_controls", step_policy_controls, "Controls described in policy", "control_understanding"),
    _spec("import_tests", step_import_tests, "Import control tests", "ingestion"),
    _spec("register_evidence", step_register_evidence, "Register evidence artefact", "ingestion"),
    _spec("finalize", step_finalize, "Mark document processed", "supervisor"),
]
STEP_INDEX = {s.name: s for s in _STEPS}


def register_workflows(engine: WorkflowEngine) -> None:
    engine.register(
        WorkflowDefinition(
            name="process_document",
            description="Autonomous processing and impact analysis of an uploaded document",
            steps=STEP_INDEX,
            initial_plan=lambda _input: ["parse_and_index", "classify_and_link", "plan"],
            llm_call_budget=250,
        )
    )
    engine.register(
        WorkflowDefinition(
            name="compliance_assessment",
            description="Full assessment of the bank against all active obligations",
            steps=STEP_INDEX,
            initial_plan=lambda _input: [
                "map_controls",
                "assess_evidence",
                "score_controls",
                "identify_gaps",
                "score_risk",
                "recommend_remediation",
                "impact_report",
            ],
            llm_call_budget=250,
        )
    )
    engine.register(
        WorkflowDefinition(
            name="cross_regulation_analysis",
            description="Cross-regulation clustering and potential-conflict screening",
            steps=STEP_INDEX,
            initial_plan=lambda _input: ["cross_regulation", "score_controls", "identify_gaps", "score_risk"],
            llm_call_budget=40,
        )
    )
    engine.register(
        WorkflowDefinition(
            name="run_simulation",
            description="What-if simulation of a regulatory change",
            steps={
                "simulate": _spec("simulate", step_simulate, "Parse and propagate a what-if scenario", "simulation")
            },
            initial_plan=lambda _input: ["simulate"],
            llm_call_budget=5,
        )
    )


# Silence unused-import linters for modules referenced only through step handlers.
_ = (Document, RegulationVersion)
