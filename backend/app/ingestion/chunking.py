"""Structure-aware chunking.

One chunk per section when it fits (clauses stay intact, which makes citations
exact). Longer sections are split on sub-clause / sentence boundaries into windows
of ~450 tokens with ~64 tokens of overlap. Every chunk carries a contextual header
(document > chapter > paragraph) that is embedded with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_TOKENS = 450
MAX_TOKENS = 600
OVERLAP_TOKENS = 64
_BOUNDARY = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])|\n(?=\(?[a-z0-9ivx]{1,4}\)\s)")


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 1.3))


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    text: str
    char_start: int
    token_count: int


def build_header(doc_title: str | None, chapter_title: str | None, path: str, title: str | None) -> str:
    parts = [p for p in (doc_title, chapter_title) if p]
    label = f"{path} {title}".strip() if title else path
    parts.append(label)
    return " > ".join(parts)


def chunk_section(text: str) -> list[ChunkDraft]:
    if not text.strip():
        return []
    if estimate_tokens(text) <= MAX_TOKENS:
        return [ChunkDraft(0, text, 0, estimate_tokens(text))]

    # Split into units at clause/sentence boundaries, remembering offsets.
    units: list[tuple[int, str]] = []
    cursor = 0
    for match in _BOUNDARY.finditer(text):
        units.append((cursor, text[cursor : match.start()]))
        cursor = match.end()
    units.append((cursor, text[cursor:]))

    chunks: list[ChunkDraft] = []
    window: list[tuple[int, str]] = []
    tokens = 0
    for offset, unit in units:
        unit_tokens = estimate_tokens(unit)
        if window and tokens + unit_tokens > TARGET_TOKENS:
            chunks.append(_emit(len(chunks), text, window))
            # Carry trailing units forward as overlap.
            overlap: list[tuple[int, str]] = []
            carried = 0
            for item in reversed(window):
                carried += estimate_tokens(item[1])
                if carried > OVERLAP_TOKENS:
                    break
                overlap.insert(0, item)
            window, tokens = overlap, sum(estimate_tokens(u) for _, u in overlap)
        window.append((offset, unit))
        tokens += unit_tokens
    if window:
        chunks.append(_emit(len(chunks), text, window))
    return chunks


def _emit(ordinal: int, text: str, window: list[tuple[int, str]]) -> ChunkDraft:
    start = window[0][0]
    end = window[-1][0] + len(window[-1][1])
    body = text[start:end]
    return ChunkDraft(ordinal, body, start, estimate_tokens(body))
