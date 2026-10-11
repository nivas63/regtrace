from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.domain.frequency import is_at_least_as_frequent, normalize_control_frequency, normalize_period
from app.domain.scoring import (
    ControlFacts,
    ControlTestFacts,
    EffectivenessInput,
    EvidenceJudgement,
    MappedControl,
    ObligationFacts,
    RiskInput,
    assess_effectiveness,
    identify_gaps,
    obligation_coverage,
    score_risk,
)
from app.domain.types import GapType, Rating
from app.domain.verification import verify_quote

TODAY = date(2026, 9, 1)


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("at least once in six months", "P6M"),
        ("at least once every quarter", "P3M"),
        ("once every two years for high risk customers", "P2Y"),
        ("half-yearly", "P6M"),
        ("at least once a year", "P1Y"),
        ("monthly", "P1M"),
        ("within 30 days", "P30D"),
        ("taken daily", "P1D"),
        ("once every ten years", "P10Y"),
        ("no periodic language here", None),
    ],
)
def test_normalize_period(text: str, iso: str | None) -> None:
    assert normalize_period(text) == iso


def test_control_frequency_and_comparison() -> None:
    assert normalize_control_frequency("Quarterly") == "P3M"
    assert normalize_control_frequency("Event-driven") == "EVENT"
    assert is_at_least_as_frequent("P6M", "P3M") is False
    assert is_at_least_as_frequent("P1M", "P2Y") is True
    assert is_at_least_as_frequent("CONTINUOUS", "P1D") is True
    assert is_at_least_as_frequent("EVENT", "P1Y") is None


def test_quote_verification_exact_fuzzy_and_rejection() -> None:
    source = "6.2 The risk categorisation of customers shall be\nreviewed at least once in six months."
    exact = verify_quote("The risk categorisation of customers shall be reviewed", source)
    assert exact.verified and exact.score == 1.0 and source[exact.start : exact.end].startswith("The risk")
    fuzzy = verify_quote("The risk categorization of customers shall be reviewed", source)
    assert fuzzy.verified and fuzzy.score < 1.0
    assert not verify_quote("The bank shall review risk every quarter at board level", source).verified
    assert not verify_quote("shall", source).verified  # too short to count as evidence


def _controls(frequency: str = "P3M", owner: str | None = "Head - AML") -> ControlFacts:
    return ControlFacts(owner=owner, frequency=frequency, automation="SEMI_AUTOMATED", procedure_ref="SOP")


def test_effective_control() -> None:
    result = assess_effectiveness(
        EffectivenessInput(
            control=_controls(),
            required_period="P3M",
            tests=[ControlTestFacts(TODAY - timedelta(days=30), "PASS", 20, 0)],
            judgements=[EvidenceJudgement("MET", "SYSTEM_GENERATED", True, TODAY - timedelta(days=30))],
            evidence_count=1,
            as_of=TODAY,
        )
    )
    assert result.rating is Rating.EFFECTIVE and result.score >= 0.75


def test_stale_evidence_caps_rating_and_lowers_recency() -> None:
    result = assess_effectiveness(
        EffectivenessInput(
            control=_controls(),
            required_period="P3M",
            tests=[ControlTestFacts(TODAY - timedelta(days=275), "PASS", 50, 2)],
            judgements=[],
            evidence_count=0,
            as_of=TODAY,
        )
    )
    assert result.recency == 0.0
    assert result.rating is not Rating.EFFECTIVE
    assert any("days old" in r for r in result.reasons)


def test_no_evidence_means_insufficient_evidence_never_compliant() -> None:
    result = assess_effectiveness(
        EffectivenessInput(
            control=_controls(), required_period=None, tests=[], judgements=[], evidence_count=0, as_of=TODAY
        )
    )
    assert result.rating is Rating.INSUFFICIENT_EVIDENCE
    assert result.operating is None


def test_frequency_inadequate_design() -> None:
    result = assess_effectiveness(
        EffectivenessInput(
            control=_controls("P1Y"),
            required_period="P6M",
            tests=[ControlTestFacts(TODAY - timedelta(days=20), "PASS", 1, 0)],
            judgements=[],
            evidence_count=0,
            as_of=TODAY,
        )
    )
    assert result.frequency_adequate is False and result.rating is not Rating.EFFECTIVE


def test_coverage_is_probabilistic_or() -> None:
    assert obligation_coverage([]) == 0.0
    assert obligation_coverage([(1.0, 0.8)]) == 0.8
    assert obligation_coverage([(0.5, 1.0), (0.5, 1.0)]) == 0.75


def _mapped(**kw: object) -> MappedControl:
    base = dict(
        control_id=uuid.uuid4(),
        control_key="C-1",
        mapping_type="FULL",
        coverage=1.0,
        rating="EFFECTIVE",
        score=0.9,
        evidence=0.9,
        recency=1.0,
        frequency_adequate=True,
        provisional=False,
    )
    base.update(kw)
    return MappedControl(**base)  # type: ignore[arg-type]


def test_gap_rules() -> None:
    oid = uuid.uuid4()
    none = identify_gaps(ObligationFacts(oid, "APPLICABLE", False, 0, []), 0.0)
    assert [g.gap_type for g in none] == [GapType.MISSING_CONTROL]

    healthy = identify_gaps(ObligationFacts(oid, "APPLICABLE", False, 0, [_mapped()]), 0.9)
    assert healthy == []

    stale = identify_gaps(
        ObligationFacts(oid, "APPLICABLE", False, 0, [_mapped(recency=0.0, rating="PARTIALLY_EFFECTIVE")]), 0.5
    )
    assert GapType.STALE_EVIDENCE in {g.gap_type for g in stale}

    missing_ev = identify_gaps(
        ObligationFacts(oid, "APPLICABLE", False, 1, [_mapped(rating="INSUFFICIENT_EVIDENCE", evidence=0.0)]), 0.1
    )
    assert {GapType.MISSING_EVIDENCE, GapType.CONFLICTING_REGULATION} <= {g.gap_type for g in missing_ev}

    assert identify_gaps(ObligationFacts(oid, "NOT_APPLICABLE", True, 1, []), 0.0) == []


def test_risk_is_deterministic_and_prioritised() -> None:
    high = score_risk(RiskInput("MISSING_CONTROL", "INCIDENT_MANAGEMENT", "MUST", 0.0, None, TODAY))
    low = score_risk(RiskInput("STALE_EVIDENCE", "TRAINING", "MUST", 0.7, None, TODAY))
    assert high.score > low.score
    assert high.priority == "P1" and low.priority in ("P3", "P4")
    again = score_risk(RiskInput("MISSING_CONTROL", "INCIDENT_MANAGEMENT", "MUST", 0.0, None, TODAY))
    assert again.score == high.score
    assert "severity 5/5" in high.reason
