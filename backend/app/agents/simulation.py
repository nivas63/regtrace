"""What-if simulation.

"What if requirement X moves from annual to quarterly testing?"
  1. candidate obligations: hybrid search over obligation text           [retrieval]
  2. scenario parsing: which obligations, what change                       [LLM, fast]
  3. propagation on an in-memory copy using the SAME `evaluate()` as the real
     assessment: controls -> effectiveness -> coverage -> gaps -> risk          [deterministic]
  4. diff against baseline + workload estimate + impacted business units
Nothing in the baseline is modified.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select, text

from app.agents.schemas import ScenarioParse
from app.domain.frequency import normalize_period, period_days
from app.domain.scoring import RiskInput, score_risk
from app.llm.prompts import AliasMap, DocumentBlock, load_prompt, spotlight
from app.llm.types import ModelTier
from app.models import Obligation, Simulation
from app.services.assessment import evaluate, load_facts
from app.workflows.engine import StepContext

HOURS_PER_CONTROL_EXECUTION = {"MANUAL": 6.0, "SEMI_AUTOMATED": 3.0, "AUTOMATED": 0.5}


async def candidate_obligations(ctx: StepContext, question: str, limit: int = 8) -> list[Obligation]:
    services = ctx.services
    vector = await services.embedder.embed_query(question)
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        rows = (
            await session.execute(
                text(
                    """
            WITH dense AS (
                SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:v AS vector)) AS r
                FROM obligations WHERE status = 'ACTIVE' AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:v AS vector) LIMIT 20
            ), lex AS (
                SELECT id, row_number() OVER (ORDER BY ts_rank_cd(to_tsvector('english', requirement_text || ' ' || source_quote), q) DESC) AS r
                FROM obligations, plainto_tsquery('english', :q) q
                WHERE status = 'ACTIVE' AND to_tsvector('english', requirement_text || ' ' || source_quote) @@ q
                LIMIT 20
            )
            SELECT id, sum(1.0 / (60 + r)) AS s FROM (SELECT * FROM dense UNION ALL SELECT * FROM lex) u
            GROUP BY id ORDER BY s DESC LIMIT :limit
            """
                ),
                {"v": str(vector), "q": question, "limit": limit},
            )
        ).all()
        ids = [r[0] for r in rows]
        obligations = {o.id: o for o in (await session.scalars(select(Obligation).where(Obligation.id.in_(ids)))).all()}
    return [obligations[i] for i in ids if i in obligations]


async def run_simulation(ctx: StepContext, simulation_id: uuid.UUID) -> dict[str, Any]:
    services = ctx.services
    bank_id = ctx.run.bank_id
    async with services.db.tenant_session(bank_id) as session:
        simulation = await session.get(Simulation, simulation_id)
        assert simulation is not None
        question = simulation.scenario

    candidates = await candidate_obligations(ctx, question)
    ctx.decide(
        "candidates_retrieved",
        f"{len(candidates)} candidate obligations for the scenario",
        candidates=[o.section_path for o in candidates],
    )
    candidate_aliases: AliasMap[Obligation] = AliasMap("O")
    outcome = await services.llm.structured(
        ctx=ctx.llm_ctx("simulation"),
        prompt=load_prompt("simulation_parsing"),
        user_content=f"QUESTION: {question}\n\n"
        + spotlight(
            DocumentBlock(
                (alias := candidate_aliases.add(o, o.id, o.section_path)),
                f"obligation {alias} (para {o.section_path})",
                f"{o.source_quote} (current frequency: {(o.frequency or {}).get('period') or 'n/a'})",
            )
            for o in candidates
        ),
        schema=ScenarioParse,
        tier=ModelTier.FAST,
        max_output_tokens=1200,
    )
    parsed = outcome.value
    resolved_targets = [candidate_aliases.resolve(t) for t in parsed.target_obligation_ids]
    targets = list(dict.fromkeys(o.id for o in resolved_targets if o is not None))
    new_period = normalize_period(parsed.new_frequency_text) if parsed.change_kind == "FREQUENCY" else None

    result: dict[str, Any] = {
        "interpretation": parsed.interpretation,
        "confidence": parsed.confidence,
        "change_kind": parsed.change_kind,
        "new_period": new_period,
        "targets": [],
        "supported": True,
    }
    if not targets or (parsed.change_kind == "FREQUENCY" and new_period is None):
        result["supported"] = False
        result["message"] = (
            "Insufficient information to simulate: the scenario could not be mapped to "
            "specific obligations and a normalisable new value."
        )
    elif parsed.change_kind != "FREQUENCY":
        result["supported"] = False
        result["message"] = (
            f"Prototype limitation: {parsed.change_kind} scenarios are parsed but only "
            "FREQUENCY changes are propagated deterministically in this version."
        )
    else:
        async with services.db.tenant_session(bank_id) as session:
            facts = await load_facts(session, bank_id, targets)
        baseline = {e.obligation.id: e for e in evaluate(facts)}
        simulated = {e.obligation.id: e for e in evaluate(facts, {oid: new_period for oid in targets})}
        workload_hours = 0.0
        units: set[str] = set()
        for oid in targets:
            before, after = baseline[oid], simulated[oid]
            old_period = (before.obligation.frequency or {}).get("period")
            controls = []
            for (mapping, b_res), (_, a_res) in zip(before.controls, after.controls, strict=True):
                control = facts.controls[mapping.control_id]
                units.add(control.business_unit or "Unassigned")
                old_runs = 365 / (period_days(control.frequency) or 365)
                needed = 365 / (period_days(new_period) or 365)
                extra = max(0.0, needed - old_runs)
                hours = extra * HOURS_PER_CONTROL_EXECUTION.get(control.automation, 4.0)
                workload_hours += hours
                controls.append(
                    {
                        "control_key": control.control_key,
                        "control_frequency": control.frequency,
                        "before": {
                            "rating": b_res.rating.value,
                            "score": b_res.score,
                            "frequency_adequate": b_res.frequency_adequate,
                        },
                        "after": {
                            "rating": a_res.rating.value,
                            "score": a_res.score,
                            "frequency_adequate": a_res.frequency_adequate,
                            "reasons": a_res.reasons,
                        },
                        "extra_executions_per_year": round(extra, 1),
                        "extra_hours_per_year": round(hours, 1),
                    }
                )
            before_gaps = {g.gap_type.value for g in before.gaps}
            after_gaps = {g.gap_type.value for g in after.gaps}
            risks = [
                score_risk(
                    RiskInput(
                        g.gap_type.value,
                        before.obligation.obligation_type,
                        before.obligation.modality,
                        after.coverage,
                        None,
                        date.today(),
                    )
                )
                for g in after.gaps
                if g.gap_type.value not in before_gaps
            ]
            result["targets"].append(
                {
                    "obligation_id": str(oid),
                    "section_path": before.obligation.section_path,
                    "source_quote": before.obligation.source_quote,
                    "old_period": old_period,
                    "new_period": new_period,
                    "coverage_before": before.coverage,
                    "coverage_after": after.coverage,
                    "controls": controls,
                    "gaps_before": sorted(before_gaps),
                    "gaps_after": sorted(after_gaps),
                    "new_gaps": sorted(after_gaps - before_gaps),
                    "new_risks": [{"score": r.score, "priority": r.priority, "reason": r.reason} for r in risks],
                }
            )
        result["workload_hours_per_year"] = round(workload_hours, 1)
        result["impacted_business_units"] = sorted(units)
        result["summary"] = _summary(result)

    async with services.db.tenant_session(bank_id) as session:
        simulation = await session.get(Simulation, simulation_id)
        assert simulation is not None
        simulation.parsed = parsed.model_dump()
        simulation.result = result
        simulation.status = "COMPLETED" if result["supported"] else "INSUFFICIENT"
    return {"supported": result["supported"], "targets": len(result["targets"])}


def _summary(result: dict[str, Any]) -> str:
    new_gaps = sum(len(t["new_gaps"]) for t in result["targets"])
    affected = sum(1 for t in result["targets"] for c in t["controls"] if c["after"]["frequency_adequate"] is False)
    by_priority: dict[str, int] = defaultdict(int)
    for t in result["targets"]:
        for risk in t["new_risks"]:
            by_priority[risk["priority"]] += 1
    return (
        f"{len(result['targets'])} obligation(s) change to {result['new_period']}; {affected} mapped control(s) "
        f"would no longer run often enough; {new_gaps} new gap(s) "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(by_priority.items())) or 'no new risk'}); "
        f"estimated extra effort {result['workload_hours_per_year']} hours/year across "
        f"{', '.join(result['impacted_business_units']) or 'no'} unit(s)."
    )
