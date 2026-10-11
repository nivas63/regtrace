"""Deterministic normalisation of frequency / period language to ISO-8601 durations.

"at least once in six months", "half-yearly", "every quarter" -> "P6M", "P6M", "P3M".
The LLM reports the frequency *text*; this module - not the model - decides the
normalised value, so comparisons between obligations and controls are exact.
"""

from __future__ import annotations

import re

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "eighteen": 18,
    "twenty": 20,
    "thirty": 30,
    "a": 1,
    "an": 1,
}
_NAMED = [
    (re.compile(r"\b(daily|every day|each day)\b"), "P1D"),
    (re.compile(r"\b(weekly|every week)\b"), "P1W"),
    (re.compile(r"\b(fortnightly)\b"), "P2W"),
    (re.compile(r"\b(monthly|every month|each month)\b"), "P1M"),
    (re.compile(r"\b(quarterly|every quarter|each quarter|once a quarter)\b"), "P3M"),
    (re.compile(r"\b(half[- ]yearly|semi[- ]annual(ly)?|bi[- ]annual(ly)?)\b"), "P6M"),
    (re.compile(r"\b(annual(ly)?|yearly|every year|each year|once a year|per annum)\b"), "P1Y"),
]
_EVERY_N = re.compile(
    r"\b(?:once\s+)?(?:in|every|each|within|after|for)?\s*(?:a\s+period\s+of\s+)?"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|eighteen|twenty|thirty|a|an)"
    r"\s+(day|week|month|quarter|year)s?\b"
)
_UNIT = {"day": "D", "week": "W", "month": "M", "quarter": "M", "year": "Y"}
_ISO = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?$")
_DAYS = {"Y": 365, "M": 30, "W": 7, "D": 1}
_CONTROL_TEXT = {
    "daily": "P1D",
    "weekly": "P1W",
    "monthly": "P1M",
    "quarterly": "P3M",
    "half-yearly": "P6M",
    "half yearly": "P6M",
    "semi-annual": "P6M",
    "annual": "P1Y",
    "annually": "P1Y",
    "yearly": "P1Y",
    "event-driven": "EVENT",
    "event driven": "EVENT",
    "ad hoc": "EVENT",
    "per event": "EVENT",
    "continuous": "CONTINUOUS",
    "real-time": "CONTINUOUS",
}


def normalize_period(text: str | None) -> str | None:
    """Return an ISO-8601 duration for periodic language, else None."""
    if not text:
        return None
    lowered = text.lower()
    if _ISO.match(text.strip()):
        return text.strip()
    match = _EVERY_N.search(lowered)
    if match:
        raw, unit = match.group(1), match.group(2)
        count = int(raw) if raw.isdigit() else _WORD_NUMBERS[raw]
        if unit == "quarter":
            count *= 3
        return f"P{count}{_UNIT[unit]}"
    for pattern, iso in _NAMED:
        if pattern.search(lowered):
            return iso
    return None


def normalize_control_frequency(text: str | None) -> str | None:
    """Control-matrix frequency cells ("Quarterly", "Event-driven", "P3M")."""
    if not text:
        return None
    key = text.strip().lower()
    if key in _CONTROL_TEXT:
        return _CONTROL_TEXT[key]
    if text.strip().upper() in ("EVENT", "CONTINUOUS"):
        return text.strip().upper()
    return normalize_period(text)


def period_days(iso: str | None) -> int | None:
    """Approximate length in days (months = 30, years = 365). None for non-periodic."""
    if not iso:
        return None
    match = _ISO.match(iso)
    if not match:
        return None
    total = 0
    for value, unit in zip(match.groups(), ("Y", "M", "W", "D"), strict=True):
        if value:
            total += int(value) * _DAYS[unit]
    return total or None


def is_at_least_as_frequent(control_period: str | None, required_period: str | None) -> bool | None:
    """True if a control performed every `control_period` satisfies `required_period`.
    None when either side is not periodic (cannot be compared deterministically)."""
    control_days, required_days = period_days(control_period), period_days(required_period)
    if control_period == "CONTINUOUS" and required_days:
        return True
    if control_days is None or required_days is None:
        return None
    return control_days <= required_days
