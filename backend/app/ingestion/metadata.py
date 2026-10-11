"""Deterministic metadata extraction for regulatory documents.

Circular numbers, dates, effective-date clauses, supersession and amendment
language follow stable drafting conventions, so regexes are more reliable (and
free) here than an LLM. The Document Intelligence agent only resolves what these
rules leave ambiguous.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_TEXT = rf"(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS}),?\s+(\d{{4}})|({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}}))"
_DATE_NUMERIC = r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})"

_REF_PATTERNS = [
    re.compile(r"\bRBI/\d{4}-\d{2,4}/\d+\b"),
    re.compile(r"\b[A-Z]{2,}(?:\.[A-Za-z]+)*\.No\.[A-Za-z0-9./\-]+\b"),
    re.compile(r"\bSYN/[A-Z]+/\d{4}/\d+\b"),  # synthetic demo documents
]
_REGULATORS = {
    "Synthetic Regulatory Authority": "SRA",
    "Synthetic Data Protection Authority": "SDPA",
    "Reserve Bank of India": "RBI",
    "Securities and Exchange Board of India": "SEBI",
    "Financial Intelligence Unit": "FIU-IND",
    "Data Protection Board": "DPB",
    "CERT-In": "CERT-In",
}
_EFFECTIVE = re.compile(
    rf"(?:come into (?:force|effect)|be effective|take effect|effective)\s+(?:on|from|with effect from)?\s*{_DATE_TEXT}",
    re.I,
)
_IMMEDIATE = re.compile(r"with immediate effect|come into force (?:immediately|forthwith)", re.I)
_DATED = re.compile(rf"(?:dated|issued on|date:)\s*{_DATE_TEXT}", re.I)
_SUPERSEDES = re.compile(r"(?:in supersession of|supersedes?|stand(?:s)? repealed)", re.I)
_AMENDMENT = re.compile(
    r"shall be (?:substituted|inserted|deleted|amended)|is hereby amended|the following amendments",
    re.I,
)
_SYNTHETIC = re.compile(r"\bSYNTHETIC\b")


@dataclass
class DocumentMetadata:
    ref_no: str | None = None
    regulator: str | None = None
    issued_on: date | None = None
    effective_from: date | None = None
    effective_immediately: bool = False
    mentions_supersession: bool = False
    looks_like_amendment: bool = False
    is_synthetic: bool = False
    referenced_refs: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("issued_on", "effective_from"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data


def _to_date(groups: tuple[str | None, ...]) -> date | None:
    d1, m1, y1, m2, d2, y2 = groups[:6]
    try:
        if d1 and m1 and y1:
            return datetime.strptime(f"{d1} {m1} {y1}", "%d %B %Y").date()
        if m2 and d2 and y2:
            return datetime.strptime(f"{d2} {m2} {y2}", "%d %B %Y").date()
    except ValueError:
        return None
    return None


def first_date(text: str) -> date | None:
    match = re.search(_DATE_TEXT, text)
    if match:
        return _to_date(match.groups())
    numeric = re.search(_DATE_NUMERIC, text)
    if numeric:
        day, month, year = (int(g) for g in numeric.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def extract_metadata(text: str) -> DocumentMetadata:
    head = text[:6000]
    meta = DocumentMetadata()
    refs: list[str] = []
    for pattern in _REF_PATTERNS:
        refs.extend(pattern.findall(text))
    unique_refs = list(dict.fromkeys(refs))
    if unique_refs:
        first_in_head = [r for r in unique_refs if r in head]
        meta.ref_no = (first_in_head or unique_refs)[0]
        meta.referenced_refs = [r for r in unique_refs if r != meta.ref_no][:20]

    for name, code in _REGULATORS.items():
        if name.lower() in head.lower():
            meta.regulator = code
            break

    if dated := _DATED.search(head):
        meta.issued_on = _to_date(dated.groups())
    else:
        meta.issued_on = first_date(head)

    if effective := _EFFECTIVE.search(text):
        meta.effective_from = _to_date(effective.groups())
    elif _IMMEDIATE.search(text):
        meta.effective_immediately = True
        meta.effective_from = meta.issued_on

    meta.mentions_supersession = bool(_SUPERSEDES.search(text))
    meta.looks_like_amendment = len(_AMENDMENT.findall(text)) >= 1 and len(text) < 20_000
    meta.is_synthetic = bool(_SYNTHETIC.search(head))
    return meta
