"""Parse every synthetic document and compare recovered structure with the corpus.

Usage (from backend/):  python -m scripts.check_parsing
Exit code 1 if any expected paragraph or verbatim obligation quote is not recovered.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.ingestion.parsers import parse
from app.ingestion.structure import detect_sections, document_title
from app.ingestion.validation import validate_upload
from scripts import demo_corpus as corpus

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    failures = 0
    for reg in corpus.REGULATIONS:
        raw = (DATA / "regulations" / reg.filename).read_bytes()
        validated = validate_upload(reg.filename, raw, max_bytes=50 << 20, max_pdf_pages=500)
        doc = parse(validated.kind, raw)
        sections = detect_sections(doc)
        texts = {s.section_path: " ".join(s.text.split()) for s in sections}
        expected = {p.path for ch in reg.chapters for p in ch.paras}
        found = {s.section_path for s in sections if s.section_type == "PARAGRAPH"}
        quote_misses = [
            p.path
            for ch in reg.chapters
            for p in ch.paras
            for o in p.obligations
            if o.quote not in texts.get(p.path, "")
        ]
        leaks = [s.section_path for s in sections if "SYNTHETIC - not" in s.text]
        ok = not (expected - found) and not (found - expected) and not quote_misses and not leaks
        failures += not ok
        print(
            f"{'OK  ' if ok else 'FAIL'} {reg.doc_id}: {len(found)}/{len(expected)} paragraphs, "
            f"quote misses={quote_misses}, footer leaks={leaks}, title={document_title(doc, sections)!r}"
        )
    for name in ("Aravali_KYC_AML_Policy.pdf", "Aravali_Information_Security_Policy.pdf"):
        raw = (DATA / "bank" / name).read_bytes()
        doc = parse(validate_upload(name, raw, max_bytes=50 << 20, max_pdf_pages=500).kind, raw)
        sections = detect_sections(doc)
        print(f"     {name}: {len(sections)} sections, title={document_title(doc, sections)!r}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
