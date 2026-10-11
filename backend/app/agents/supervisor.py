"""Supervisor planner for document processing (autonomous regulatory impact analysis).

The supervisor chooses the downstream steps from a fixed catalogue. The LLM
proposes an ordered plan with a rationale; code then enforces the catalogue,
preconditions and the canonical order, and inserts any required step the model
omitted (recorded as a decision). If no model is available, the deterministic
template for the document class is used - the system degrades, it does not stop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agents.schemas import Plan
from app.llm.errors import LLMError
from app.llm.prompts import load_prompt
from app.llm.types import ModelTier
from app.workflows.engine import StepContext

CATALOGUE: dict[str, str] = {
    "extract_obligations": "Extract and verify obligations from the new/changed paragraphs",
    "diff_versions": "Compare with the previous version (requires previous version)",
    "apply_amendment": "Apply an amendment circular to the previous version (amendments only)",
    "carry_forward": "Link obligations across versions (requires previous version)",
    "assess_applicability": "Decide applicability of obligations to the bank",
    "map_controls": "Map obligations to controls (requires controls)",
    "assess_evidence": "Assess evidence for affected controls (requires mappings)",
    "score_controls": "Deterministic control effectiveness scoring",
    "identify_gaps": "Deterministic gap identification and reconciliation",
    "score_risk": "Deterministic risk prioritisation of open gaps",
    "recommend_remediation": "Propose remediation for highest-risk gaps",
    "cross_regulation": "Cluster equivalent obligations and screen for conflicts across regulations",
    "impact_report": "Build the impact report and notify",
    "import_controls": "Import a control matrix",
    "extract_policy_controls": "Extract controls described in a policy document",
    "import_tests": "Import control test results",
    "register_evidence": "Register an evidence artefact against its control",
}

TEMPLATES: dict[str, list[str]] = {
    "NEW_REGULATION": [
        "extract_obligations",
        "assess_applicability",
        "map_controls",
        "assess_evidence",
        "score_controls",
        "identify_gaps",
        "score_risk",
        "recommend_remediation",
        "cross_regulation",
        "impact_report",
    ],
    "NEW_VERSION": [
        "diff_versions",
        "extract_obligations",
        "carry_forward",
        "assess_applicability",
        "map_controls",
        "assess_evidence",
        "score_controls",
        "identify_gaps",
        "score_risk",
        "recommend_remediation",
        "cross_regulation",
        "impact_report",
    ],
    "AMENDMENT": [
        "apply_amendment",
        "diff_versions",
        "extract_obligations",
        "carry_forward",
        "assess_applicability",
        "map_controls",
        "assess_evidence",
        "score_controls",
        "identify_gaps",
        "score_risk",
        "recommend_remediation",
        "impact_report",
    ],
    "DUPLICATE": [],
    "CONTROL_MATRIX": [
        "import_controls",
        "map_controls",
        "assess_evidence",
        "score_controls",
        "identify_gaps",
        "score_risk",
        "recommend_remediation",
        "impact_report",
    ],
    "POLICY": ["extract_policy_controls"],
    "CONTROL_TESTS": ["import_tests", "score_controls", "identify_gaps", "score_risk", "impact_report"],
    "EVIDENCE": [
        "register_evidence",
        "assess_evidence",
        "score_controls",
        "identify_gaps",
        "score_risk",
        "impact_report",
    ],
}
# Steps a plan may never drop for a given situation (the propagation spine).
REQUIRED: dict[str, set[str]] = {
    "NEW_REGULATION": {"extract_obligations", "assess_applicability", "identify_gaps", "impact_report"},
    "NEW_VERSION": {"diff_versions", "carry_forward", "identify_gaps", "impact_report"},
    "AMENDMENT": {"apply_amendment", "diff_versions", "carry_forward", "identify_gaps", "impact_report"},
    "CONTROL_MATRIX": {"import_controls"},
    "CONTROL_TESTS": {"import_tests", "score_controls", "identify_gaps"},
    "EVIDENCE": {"register_evidence", "score_controls", "identify_gaps"},
    "POLICY": {"extract_policy_controls"},
    "DUPLICATE": set(),
}


@dataclass
class PlanDecision:
    steps: list[str]
    rationale: str
    decided_by: str
    repaired: list[str]


def _preconditions_ok(step: str, facts: dict[str, Any]) -> bool:
    """Facts are taken *before* the planned steps run, so each check reasons about the
    state the step will see: a control-matrix upload creates controls, a regulation
    upload creates obligations."""
    situation = facts.get("situation")
    controls = int(facts.get("controls", 0))
    obligations = int(facts.get("obligations", 0))
    if step in ("map_controls", "assess_evidence"):
        if situation == "CONTROL_MATRIX":
            return obligations > 0  # controls are imported by this very plan
        return controls > 0  # obligations are extracted by this very plan
    if step == "recommend_remediation":
        return controls > 0 or situation in ("NEW_REGULATION", "NEW_VERSION", "CONTROL_MATRIX")
    if step == "cross_regulation":
        return int(facts.get("regulations", 0)) >= 2
    return True


async def plan_document_processing(ctx: StepContext, situation: str, facts: dict[str, Any]) -> PlanDecision:
    template = [s for s in TEMPLATES.get(situation, []) if _preconditions_ok(s, facts)]
    required = REQUIRED.get(situation, set())
    if not template:
        return PlanDecision([], f"Nothing to do for situation {situation}", "RULE", [])
    catalogue = {k: v for k, v in CATALOGUE.items() if k in TEMPLATES.get(situation, [])}
    proposed: list[str] | None = None
    rationale = "Deterministic template (no model available)"
    decided_by = "RULE"
    # Bank-record uploads have a fixed propagation path; spend model calls only on
    # regulatory documents, where the plan genuinely depends on the change.
    if ctx.services.llm.available and situation in ("NEW_REGULATION", "NEW_VERSION", "AMENDMENT"):
        try:
            outcome = await ctx.services.llm.structured(
                ctx=ctx.llm_ctx("supervisor"),
                prompt=load_prompt("impact_planning"),
                user_content=json.dumps({"situation": situation, "facts": facts, "catalogue": catalogue}),
                schema=Plan,
                tier=ModelTier.FAST,
                max_output_tokens=900,
            )
            proposed, rationale, decided_by = outcome.value.steps, outcome.value.rationale, "LLM"
        except LLMError as exc:
            ctx.decide("planner_fallback", f"{exc.code}: using deterministic template")
    if proposed is None:
        return PlanDecision(template, rationale, decided_by, [])
    allowed = set(template)
    chosen = {s for s in proposed if s in allowed}
    repaired = sorted((required & allowed) - chosen)
    if set(proposed) - set(CATALOGUE):
        ctx.decide(
            "plan_invalid_steps_dropped", f"Model proposed unknown steps: {sorted(set(proposed) - set(CATALOGUE))}"
        )
    if repaired:
        ctx.decide("plan_repaired", f"Required steps re-inserted: {repaired}")
    final = [s for s in template if s in chosen or s in repaired]  # canonical order enforced
    return PlanDecision(final, rationale, decided_by, repaired)
