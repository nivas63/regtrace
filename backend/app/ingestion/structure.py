"""Recover the logical structure (chapters, numbered paragraphs, annexes) of a
regulatory or policy document from its lines.

Regulatory texts are organised by hierarchical paragraph numbers ("5", "5.2",
"5.2.1") with inline sub-clauses ("(a)", "(i)"). A number at the start of a line is
only accepted as a new paragraph if it is a plausible *successor* of the previous
paragraph number - this rejects false positives such as a wrapped line beginning
"10 years ...".
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from app.ingestion.parsers import Line, ParsedDocument

_CHAPTER = re.compile(r"^(?:CHAPTER|Chapter|PART|Part)\s+([IVXLC]+|\d+)\b[\s:.\-–—]*(.*)$")
_ANNEX = re.compile(r"^(Annex(?:ure)?|Appendix|Schedule)\s*[-–—]?\s*([IVXLC]+|\d+|[A-Z])?\b[\s:.\-–—]*(.*)$")
_NUMBERED = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){0,3})\.?\s+(\S.*)$")
_SUBCLAUSE = re.compile(r"^\(?([a-z]|[ivx]{1,5})\)\s")
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)
_DIGITS = re.compile(r"\d+")
_HYPHEN_BREAK = re.compile(r"-\n(?=[a-z])")
_TITLE_MAX_WORDS = 12
# Lines in the top/bottom page margin set in a smaller font are running headers/footers.
_MARGIN_TOP, _MARGIN_BOTTOM = 0.06, 0.92


@dataclass
class SectionDraft:
    section_path: str
    title: str | None
    level: int
    section_type: str  # PREAMBLE | CHAPTER | PARAGRAPH | ANNEX
    lines: list[str] = field(default_factory=list)
    page_from: int = 1
    page_to: int = 1
    parent_path: str | None = None
    chapter_title: str | None = None

    @property
    def text(self) -> str:
        # Re-join words hyphenated across a line break ("multi-\nfactor" -> "multi-factor").
        return _HYPHEN_BREAK.sub("-", "\n".join(self.lines)).strip()

    @property
    def content_hash(self) -> str:
        normalized = " ".join(self.text.split()).lower()
        return hashlib.sha256(normalized.encode()).hexdigest()


def _parse_number(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split("."))


def is_successor(prev: tuple[int, ...] | None, cand: tuple[int, ...]) -> bool:
    """True if `cand` can follow `prev` in a hierarchical numbering scheme."""
    if prev is None:
        return cand[0] in (1, 2) and all(p == 1 for p in cand[1:])
    # One level deeper, starting at 1: 5.2 -> 5.2.1
    if len(cand) == len(prev) + 1 and cand[:-1] == prev and cand[-1] == 1:
        return True
    # Same or shallower level, incremented: 5.2 -> 5.3, 5.2.3 -> 5.3, 5.2 -> 6
    for depth in range(1, len(prev) + 1):
        if len(cand) == depth and cand[:-1] == prev[: depth - 1] and cand[-1] == prev[depth - 1] + 1:
            return True
    # A new top-level paragraph that also opens its first sub-level: 5.3 -> 6.1
    return len(cand) == 2 and cand[0] == prev[0] + 1 and cand[1] == 1


def _in_margin(line: Line, body_size: float) -> bool:
    if line.y_ratio is None or not body_size or line.font_size >= body_size:
        return False
    return line.y_ratio < _MARGIN_TOP or line.y_ratio > _MARGIN_BOTTOM


def _strip_running_headers(doc: ParsedDocument) -> list[Line]:
    body_size = doc.body_font_size
    candidates = [ln for ln in doc.lines if not _PAGE_NUMBER.match(ln.text) and not _in_margin(ln, body_size)]
    if doc.page_count < 3:
        return candidates
    pages_by_text: dict[str, set[int]] = {}
    for ln in doc.lines:
        pages_by_text.setdefault(_DIGITS.sub("#", ln.text.lower()), set()).add(ln.page)
    threshold = max(2, doc.page_count // 2)
    repeated = {t for t, pages in pages_by_text.items() if len(pages) >= threshold}
    return [ln for ln in candidates if _DIGITS.sub("#", ln.text.lower()) not in repeated]


def _looks_like_title(line: Line, body_size: float) -> bool:
    words = len(line.text.split())
    if words == 0 or words > _TITLE_MAX_WORDS or line.text.endswith((".", ";")):
        return False
    return line.heading_level is not None or line.bold or (body_size > 0 and line.font_size >= body_size + 1.0)


def detect_sections(doc: ParsedDocument) -> list[SectionDraft]:
    lines = _strip_running_headers(doc)
    body_size = doc.body_font_size
    sections: list[SectionDraft] = []
    current = SectionDraft("preamble", None, 0, "PREAMBLE")
    chapter: SectionDraft | None = None
    annex_prefix: str | None = None
    last_number: tuple[int, ...] | None = None
    pending_title: str | None = None
    title_font: float | None = None
    seen_paths: Counter[str] = Counter()

    def start(draft: SectionDraft, page: int) -> SectionDraft:
        nonlocal current
        if current.lines or current.title or current.section_type in ("CHAPTER", "ANNEX"):
            sections.append(current)
        seen_paths[draft.section_path] += 1
        if seen_paths[draft.section_path] > 1:
            draft.section_path = f"{draft.section_path}#{seen_paths[draft.section_path]}"
        draft.page_from = draft.page_to = page
        current = draft
        return draft

    for line in lines:
        text = line.text.strip()

        if match := _CHAPTER.match(text):
            chapter = start(
                SectionDraft(f"CH-{match.group(1)}", match.group(2) or None, 1, "CHAPTER"),
                line.page,
            )
            chapter.chapter_title = chapter.title
            annex_prefix, pending_title = None, None
            continue

        if (match := _ANNEX.match(text)) and (line.bold or line.heading_level or len(text) < 80):
            label = f"{match.group(1).upper()}-{match.group(2) or 'I'}"
            chapter = start(SectionDraft(label, match.group(3) or None, 1, "ANNEX"), line.page)
            chapter.chapter_title = chapter.title or label
            annex_prefix, last_number, pending_title = label, None, None
            continue

        match = _NUMBERED.match(text)
        # A paragraph starts with a capital letter or a sub-clause; a wrapped line
        # such as "5 years from the date..." does not.
        starts_ok = match is not None and (match.group(2)[0].isupper() or match.group(2)[0] == "(")
        if match and starts_ok and is_successor(last_number, _parse_number(match.group(1))):
            number = _parse_number(match.group(1))
            rest = match.group(2).strip()
            path = ".".join(str(n) for n in number)
            if annex_prefix:
                path = f"{annex_prefix}/{path}"
            title: str | None = None
            body = rest
            # "5.2 Risk Categorisation" on its own line -> title; otherwise body text. The
            # typography decides; without typographic hints (plain text) only very short
            # lines count as titles, so a wrapped first sentence is never mistaken for one.
            styled = (
                line.bold or line.heading_level is not None or (body_size > 0 and line.font_size >= body_size + 1.0)
            )
            short = len(rest.split()) <= (_TITLE_MAX_WORDS if styled else 6)
            if short and (styled or not line.font_size) and not rest.endswith((".", ";", ":")):
                title, body = rest, ""
            elif ":" in rest[:80] and len(rest.split(":", 1)[0].split()) <= _TITLE_MAX_WORDS:
                title, body = (part.strip() for part in rest.split(":", 1))
            parent_path = ".".join(str(n) for n in number[:-1]) or (chapter.section_path if chapter else None)
            draft = start(
                SectionDraft(
                    path,
                    title or pending_title,
                    len(number) + 1,
                    "PARAGRAPH",
                    parent_path=parent_path,
                    chapter_title=chapter.chapter_title if chapter else None,
                ),
                line.page,
            )
            if body:
                draft.lines.append(body)
            last_number, pending_title = number, None
            continue

        if _looks_like_title(line, body_size) and not _SUBCLAUSE.match(text):
            if current.section_type in ("CHAPTER", "ANNEX") and not current.lines and not current.title:
                current.title = current.chapter_title = text
            elif current.section_type == "PREAMBLE" and not sections and not current.lines:
                # Document title, possibly wrapped over several lines of the same size.
                if current.title and title_font == line.font_size:
                    current.title = f"{current.title} {text}"
                elif not current.title:
                    current.title, title_font = text, line.font_size
                else:
                    current.lines.append(text)
            else:
                pending_title = text
            continue

        if pending_title:
            current.lines.append(pending_title)
            pending_title = None
        current.lines.append(text)
        current.page_to = line.page

    if current.lines or current.title or current.section_type in ("CHAPTER", "ANNEX"):
        sections.append(current)
    return sections


def document_title(doc: ParsedDocument, sections: list[SectionDraft]) -> str | None:
    if sections and sections[0].section_type == "PREAMBLE" and sections[0].title:
        return sections[0].title
    first_page = [ln for ln in doc.lines if ln.page == 1]
    if not first_page:
        return None
    largest = max(first_page, key=lambda ln: (ln.heading_level == 0, ln.font_size))
    return largest.text[:300]
