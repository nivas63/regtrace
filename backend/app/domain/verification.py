"""Deterministic grounding checks.

The single most effective hallucination control in RegTrace: every extracted
obligation, evidence judgement, mapping rationale and conflict must carry a quote
that is verified to exist in the source text. Verification normalises whitespace,
case, quotes and dashes, then tries an exact match and finally a fuzzy match over
candidate windows (threshold 0.92). Unverified outputs are rejected.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

FUZZY_THRESHOLD = 0.92
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


@dataclass(frozen=True)
class QuoteMatch:
    verified: bool
    score: float
    start: int  # offsets into the ORIGINAL text; -1 if not found
    end: int


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-cased, whitespace-collapsed text plus a map back to original offsets."""
    out: list[str] = []
    index_map: list[int] = []
    previous_space = True
    for i, ch in enumerate(unicodedata.normalize("NFKC", text).translate(_TRANSLATE)):
        if ch.isspace():
            if not previous_space:
                out.append(" ")
                index_map.append(i)
            previous_space = True
            continue
        out.append(ch.lower())
        index_map.append(i)
        previous_space = False
    return "".join(out).rstrip(), index_map


def normalize(text: str) -> str:
    return _normalize_with_map(text)[0]


def verify_quote(quote: str, source: str) -> QuoteMatch:
    needle = normalize(quote).strip(" .;,")
    if len(needle) < 8:
        return QuoteMatch(False, 0.0, -1, -1)
    haystack, index_map = _normalize_with_map(source)
    position = haystack.find(needle)
    if position >= 0:
        return QuoteMatch(True, 1.0, index_map[position], index_map[position + len(needle) - 1] + 1)

    # Fuzzy: slide a window of the quote's length over the source (step = 1 word).
    best, best_pos = 0.0, -1
    size = len(needle)
    starts = [0] + [m.end() for m in re.finditer(" ", haystack)]
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(needle)
    for start in starts:
        window = haystack[start : start + size]
        if len(window) < size * 0.8:
            break
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() < FUZZY_THRESHOLD or matcher.quick_ratio() < FUZZY_THRESHOLD:
            continue
        ratio = matcher.ratio()
        if ratio > best:
            best, best_pos = ratio, start
    if best >= FUZZY_THRESHOLD and best_pos >= 0:
        end = min(len(index_map) - 1, best_pos + size - 1)
        return QuoteMatch(True, round(best, 3), index_map[best_pos], index_map[end] + 1)
    return QuoteMatch(False, round(best, 3), -1, -1)


def text_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b), autojunk=False).ratio()
