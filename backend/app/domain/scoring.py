"""Deterministic scoring engines: control effectiveness, obligation coverage, gap
classification and risk prioritisation.

No LLM output is used as a number here. LLMs contribute *categorical, quote-backed
judgements* (e.g. "evidence criterion MET, quote verified"); these functions turn
facts into scores with published weights. Every result carries its factor vector
and human-readable reasons so an auditor can recompute it by hand.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.domain.frequency import is_at_least_as_frequent, period_days
from app.domain.types import EvidenceVerdict, GapType, MappingType, ObligationType, Rating

FORMULA_VERSION = "2026.1"

# ---------------------------------------------------------------------------
# Control effectiveness
# ---------------------------------------------------------------------------

DESIGN_WEIGHTS = {
    "owner_defined": 0.20,
    "frequency_defined": 0.15,
    "frequency_adequate": 0.30,
    "procedure_documented": 0.15,
    "automation": 0.20,
}
AUTOMATION_SCORE = {"AUTOMATED": 1.0, "SEMI_AUTOMATED": 0.7, "MANUAL": 0.5}
AUTHENTICITY_WEIGHT = {
    "SYSTEM_GENERATED": 1.0,
    "SIGNED_REPORT": 0.9,
    "SCREENSHOT": 0.7,
    "SELF_ATTESTATION": 0.4,
}
RESULT_SCORE = {"PASS": 1.0, "PARTIAL": 0.5, "FAIL": 0.0}
EXPONENTS = {"design": 0.30, "operating": 0.30, "evidence": 0.25, "recency": 0.15}
UNKNOWN_OPERATING = 0.6  # neutral stand-in when a control was never tested (also caps rating)
TESTS_AS_EVIDENCE = 0.6  # test records without supporting artefacts
EVIDENCE_FLOOR = 0.3
EFFECTIVE_AT, PARTIAL_AT = 0.75, 0.50
DEFAULT_INTERVAL_DAYS = 365


@dataclass(frozen=True)
class ControlFacts:
    owner: str | None
    frequency: str | None
    automation: str
    procedure_ref: str | None


@dataclass(frozen=True)
class ControlTestFacts:
    tested_on: date
    result: str
    sample_size: int
    exceptions: int


@dataclass(frozen=True)
class EvidenceJudgement:
    verdict: str
    authenticity: str
    quote_verified: bool
    collected_on: date | None = None


@dataclass(frozen=True)
class EffectivenessInput:
    control: ControlFacts
    required_period: str | None
    tests: list[ControlTestFacts]
    judgements: list[EvidenceJudgement]
    evidence_count: int
    as_of: date


@dataclass
class EffectivenessResult:
    design: float
    operating: float | None
    evidence: float
    recency: float
    score: float
    rating: Rating
    frequency_adequate: bool | None
    last_activity: date | None
    factors: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def assess_effectiveness(inp: EffectivenessInput) -> EffectivenessResult:
    c = inp.control
    reasons: list[str] = []

    # Design (deterministic checklist)
    adequate = is_at_least_as_frequent(c.frequency, inp.required_period)
    checklist = {
        "owner_defined": 1.0 if c.owner else 0.0,
        "frequency_defined": 1.0 if c.frequency else 0.0,
        "frequency_adequate": 1.0 if adequate in (True, None) else 0.0,
        "procedure_documented": 1.0 if c.procedure_ref else 0.0,
        "automation": AUTOMATION_SCORE.get(c.automation, 0.5),
    }
    design = sum(DESIGN_WEIGHTS[k] * v for k, v in checklist.items())
    if adequate is False:
        reasons.append(f"Control frequency {c.frequency} is less frequent than required {inp.required_period}")
    if not c.owner:
        reasons.append("No control owner defined")

    # Operating effectiveness from the most recent tests
    tests = sorted(inp.tests, key=lambda t: t.tested_on, reverse=True)[:3]
    operating: float | None = None
    if tests:
        result_avg = sum(RESULT_SCORE.get(t.result, 0.0) for t in tests) / len(tests)
        samples = sum(t.sample_size for t in tests) or 1
        exception_rate = min(0.5, sum(t.exceptions for t in tests) / samples)
        operating = _clamp(result_avg * (1.0 - exception_rate))
        if any(t.result == "FAIL" for t in tests):
            reasons.append("At least one recent control test failed")
    else:
        reasons.append("No control test on record: operating effectiveness unknown")

    # Evidence quality
    if inp.judgements:
        values = []
        for j in inp.judgements:
            base = {EvidenceVerdict.MET: 1.0, EvidenceVerdict.INSUFFICIENT: 0.3}.get(EvidenceVerdict(j.verdict), 0.0)
            weight = AUTHENTICITY_WEIGHT.get(j.authenticity, 0.6)
            values.append(base * weight * (1.0 if j.quote_verified else 0.5))
        evidence = sum(values) / len(values)
    elif inp.evidence_count:
        evidence = 0.4  # artefacts exist but were not assessed against criteria
    else:
        evidence = 0.0
    if tests and evidence < TESTS_AS_EVIDENCE:
        evidence = max(evidence, TESTS_AS_EVIDENCE)
    if evidence < EVIDENCE_FLOOR:
        reasons.append("No evidence artefact supports this control")

    # Recency relative to the required (or the control's own) interval
    activity_dates = [t.tested_on for t in tests] + [
        j.collected_on for j in inp.judgements if j.collected_on is not None
    ]
    last_activity = max(activity_dates) if activity_dates else None
    interval = period_days(inp.required_period) or period_days(c.frequency) or DEFAULT_INTERVAL_DAYS
    if last_activity is None:
        recency = 0.0
    else:
        age = (inp.as_of - last_activity).days
        recency = 1.0 if age <= interval else _clamp(1.0 - (age - interval) / interval)
        if recency < 1.0:
            reasons.append(f"Most recent test/evidence is {age} days old; expected at least every {interval} days")

    op_for_score = operating if operating is not None else UNKNOWN_OPERATING
    score = math.prod(
        max(value, 0.01) ** EXPONENTS[name]
        for name, value in (
            ("design", design),
            ("operating", op_for_score),
            ("evidence", evidence),
            ("recency", recency),
        )
    )

    if evidence < EVIDENCE_FLOOR:
        rating = Rating.INSUFFICIENT_EVIDENCE
    elif score >= EFFECTIVE_AT:
        rating = Rating.EFFECTIVE
    elif score >= PARTIAL_AT:
        rating = Rating.PARTIALLY_EFFECTIVE
    else:
        rating = Rating.INEFFECTIVE
    # Caps: never "Effective" without a test, with stale evidence, with inadequate design,
    # or when the most recent test did not pass.
    latest_not_passed = bool(tests) and tests[0].result != "PASS"
    if rating is Rating.EFFECTIVE and (operating is None or recency < 0.5 or adequate is False or latest_not_passed):
        rating = Rating.PARTIALLY_EFFECTIVE
        reasons.append(
            "Rating capped at Partially Effective (untested, stale, under-designed or latest test not passed)"
        )

    return EffectivenessResult(
        design=round(design, 3),
        operating=round(operating, 3) if operating is not None else None,
        evidence=round(evidence, 3),
        recency=round(recency, 3),
        score=round(score, 3),
        rating=rating,
        frequency_adequate=adequate,
        last_activity=last_activity,
        factors={
            "design_checklist": checklist,
            "exponents": EXPONENTS,
            "interval_days": interval,
            "tests_considered": len(tests),
            "judgements": len(inp.judgements),
        },
        reasons=reasons,
    )


def obligation_coverage(pairs: list[tuple[float, float]]) -> float:
    """Probabilistic OR over mapped controls: 1 - prod(1 - coverage_i * effectiveness_i)."""
    remaining = 1.0
    for coverage, effectiveness in pairs:
        remaining *= 1.0 - _clamp(coverage) * _clamp(effectiveness)
    return round(1.0 - remaining, 3)


# ---------------------------------------------------------------------------
# Gap identification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappedControl:
    control_id: uuid.UUID
    control_key: str
    mapping_type: str
    coverage: float
    rating: str
    score: float
    evidence: float
    recency: float
    frequency_adequate: bool | None
    provisional: bool
    operating: float | None = None


@dataclass(frozen=True)
class ObligationFacts:
    obligation_id: uuid.UUID
    applicability: str  # APPLICABLE | NOT_APPLICABLE | UNCERTAIN
    ambiguous: bool
    open_conflicts: int
    controls: list[MappedControl]


@dataclass
class GapDraft:
    gap_type: GapType
    control_id: uuid.UUID | None
    title: str
    description: str
    details: dict[str, Any]
    provisional: bool = False


def identify_gaps(facts: ObligationFacts, coverage: float) -> list[GapDraft]:
    if facts.applicability == "NOT_APPLICABLE":
        return []
    gaps: list[GapDraft] = []
    if facts.applicability == "UNCERTAIN":
        gaps.append(
            GapDraft(
                GapType.APPLICABILITY_UNCERTAIN,
                None,
                "Applicability to the bank is uncertain",
                "Applicability could not be decided by rules; human confirmation required.",
                {},
            )
        )
    if facts.ambiguous:
        gaps.append(
            GapDraft(
                GapType.AMBIGUOUS_REQUIREMENT,
                None,
                "Requirement wording is ambiguous",
                "The extraction agent flagged ambiguity; interpretation requires review.",
                {},
            )
        )
    if facts.open_conflicts:
        gaps.append(
            GapDraft(
                GapType.CONFLICTING_REGULATION,
                None,
                "Potential conflict with another regulation",
                f"{facts.open_conflicts} potential conflict(s) under review.",
                {},
            )
        )

    mapped = [c for c in facts.controls if c.mapping_type != MappingType.NONE]
    if not mapped:
        gaps.append(
            GapDraft(
                GapType.MISSING_CONTROL,
                None,
                "No control addresses this obligation",
                "No mapped control was found for this obligation.",
                {"coverage": coverage},
            )
        )
        return gaps

    control_gap = False
    for c in mapped:
        base = {
            "control_key": c.control_key,
            "score": c.score,
            "rating": c.rating,
            "coverage": c.coverage,
            "evidence": c.evidence,
            "recency": c.recency,
        }
        if c.rating == Rating.INSUFFICIENT_EVIDENCE:
            gaps.append(
                GapDraft(
                    GapType.MISSING_EVIDENCE,
                    c.control_id,
                    f"No evidence that {c.control_key} operates",
                    "The control exists but no test or evidence artefact supports it.",
                    base,
                    c.provisional,
                )
            )
            control_gap = True
            continue
        if c.rating == Rating.INEFFECTIVE:
            gaps.append(
                GapDraft(
                    GapType.INEFFECTIVE_CONTROL,
                    c.control_id,
                    f"{c.control_key} is not operating effectively",
                    "Tests, evidence or design indicate the control is ineffective.",
                    base,
                    c.provisional,
                )
            )
            control_gap = True
        if c.recency < 0.5:
            gaps.append(
                GapDraft(
                    GapType.STALE_EVIDENCE,
                    c.control_id,
                    f"Evidence for {c.control_key} is out of date",
                    "The control exists but its latest test/evidence is older than required.",
                    base,
                    c.provisional,
                )
            )
            control_gap = True
        if c.rating == Rating.PARTIALLY_EFFECTIVE and c.operating is not None and c.operating < 0.75:
            gaps.append(
                GapDraft(
                    GapType.PARTIAL_CONTROL,
                    c.control_id,
                    f"{c.control_key} operates with exceptions",
                    "Recent tests of the control found exceptions; it only partially meets the obligation.",
                    {**base, "operating": c.operating},
                    c.provisional,
                )
            )
            control_gap = True
        if c.frequency_adequate is False:
            gaps.append(
                GapDraft(
                    GapType.PARTIAL_CONTROL,
                    c.control_id,
                    f"{c.control_key} runs less often than required",
                    "Control frequency is weaker than the regulatory requirement.",
                    base,
                    c.provisional,
                )
            )
            control_gap = True

    all_partial = all(c.mapping_type == MappingType.PARTIAL for c in mapped)
    if not control_gap and (coverage < 0.5 or all_partial):
        gaps.append(
            GapDraft(
                GapType.PARTIAL_CONTROL,
                mapped[0].control_id,
                "Obligation only partially covered by controls",
                f"Combined coverage is {coverage:.0%}; mapped controls cover it partially.",
                {"coverage": coverage, "controls": [c.control_key for c in mapped]},
                any(c.provisional for c in mapped),
            )
        )

    unique: dict[tuple[str, uuid.UUID | None], GapDraft] = {}
    for gap in gaps:
        unique.setdefault((gap.gap_type.value, gap.control_id), gap)
    return list(unique.values())


# ---------------------------------------------------------------------------
# Risk prioritisation
# ---------------------------------------------------------------------------

# (severity, financial, customer, operational) on a 1-5 scale per obligation type.
IMPACT_PROFILE: dict[str, tuple[int, int, int, int]] = {
    ObligationType.CUSTOMER_DUE_DILIGENCE: (5, 4, 4, 4),
    ObligationType.RISK_ASSESSMENT: (4, 3, 3, 3),
    ObligationType.RECORD_RETENTION: (4, 3, 3, 3),
    ObligationType.REPORTING: (5, 5, 2, 3),
    ObligationType.GOVERNANCE: (3, 2, 1, 3),
    ObligationType.MONITORING: (4, 4, 3, 4),
    ObligationType.TESTING_AUDIT: (3, 2, 2, 3),
    ObligationType.TRAINING: (2, 1, 2, 2),
    ObligationType.TECHNICAL_CONTROL: (5, 4, 5, 5),
    ObligationType.DATA_PROTECTION: (4, 4, 5, 3),
    ObligationType.BUSINESS_CONTINUITY: (4, 4, 4, 5),
    ObligationType.INCIDENT_MANAGEMENT: (5, 4, 4, 5),
    ObligationType.OTHER: (3, 3, 3, 3),
}
IMPACT_WEIGHTS = (0.35, 0.25, 0.20, 0.20)
GAP_LIKELIHOOD: dict[str, float] = {
    GapType.MISSING_CONTROL: 0.9,
    GapType.INEFFECTIVE_CONTROL: 0.8,
    GapType.MISSING_EVIDENCE: 0.6,
    GapType.PARTIAL_CONTROL: 0.6,
    GapType.STALE_EVIDENCE: 0.5,
    GapType.CONFLICTING_REGULATION: 0.4,
    GapType.AMBIGUOUS_REQUIREMENT: 0.3,
    GapType.APPLICABILITY_UNCERTAIN: 0.3,
}
PRIORITY_BANDS = ((55.0, "P1"), (35.0, "P2"), (18.0, "P3"))
_MAX_URGENCY = 1.35
_TIME_CRITICAL_TYPES = {ObligationType.REPORTING, ObligationType.INCIDENT_MANAGEMENT}


@dataclass(frozen=True)
class RiskInput:
    gap_type: str
    obligation_type: str
    modality: str
    coverage: float
    effective_from: date | None
    as_of: date


@dataclass
class RiskResult:
    score: float
    priority: str
    factors: dict[str, Any]
    reason: str


def score_risk(inp: RiskInput) -> RiskResult:
    profile = IMPACT_PROFILE.get(inp.obligation_type, IMPACT_PROFILE[ObligationType.OTHER])
    inherent = sum(w * v for w, v in zip(IMPACT_WEIGHTS, profile, strict=True))
    likelihood = GAP_LIKELIHOOD.get(inp.gap_type, 0.5) * (1.0 - 0.5 * _clamp(inp.coverage))

    urgency = 1.0
    urgency_reasons = []
    if inp.effective_from is not None:
        days = (inp.effective_from - inp.as_of).days
        if -90 <= days <= 90:
            urgency += 0.25
            urgency_reasons.append("effective date within 90 days")
    if inp.obligation_type in _TIME_CRITICAL_TYPES:
        urgency += 0.10
        urgency_reasons.append("time-critical obligation type")
    urgency = min(urgency, _MAX_URGENCY)

    score = round(100.0 * (inherent / 5.0) * likelihood * (urgency / _MAX_URGENCY), 1)
    priority = next((p for threshold, p in PRIORITY_BANDS if score >= threshold), "P4")
    factors = {
        "severity": profile[0],
        "financial_impact": profile[1],
        "customer_impact": profile[2],
        "operational_impact": profile[3],
        "impact_weights": IMPACT_WEIGHTS,
        "inherent": round(inherent, 2),
        "gap_likelihood": GAP_LIKELIHOOD.get(inp.gap_type, 0.5),
        "coverage": inp.coverage,
        "likelihood": round(likelihood, 3),
        "urgency": urgency,
        "urgency_reasons": urgency_reasons,
        "formula": "100 * inherent/5 * likelihood * urgency/1.35",
    }
    reason = (
        f"{inp.gap_type.replace('_', ' ').lower()} on a {inp.obligation_type.replace('_', ' ').lower()} "
        f"obligation (severity {profile[0]}/5); likelihood {likelihood:.2f} given coverage "
        f"{inp.coverage:.0%}" + (f"; {', '.join(urgency_reasons)}" if urgency_reasons else "")
    )
    return RiskResult(score=score, priority=priority, factors=factors, reason=reason)


def as_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
