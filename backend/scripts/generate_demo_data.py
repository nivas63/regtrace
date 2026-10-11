"""Generate the SYNTHETIC demo corpus (PDF/XLSX) and golden evaluation labels.

Usage (from backend/):  python -m scripts.generate_demo_data [--out ../data]
Dates of tests and evidence are relative to today, so recency results stay stable
whenever the corpus is regenerated.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pymupdf

from scripts import demo_corpus as corpus

_FREQ_TEXT = {
    "P1D": "Daily",
    "P1M": "Monthly",
    "P3M": "Quarterly",
    "P6M": "Half-yearly",
    "P1Y": "Annual",
    "EVENT": "Event-driven",
    "CONTINUOUS": "Continuous",
}
_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; }
h1 { font-size: 16pt; font-weight: bold; }
h2 { font-size: 12.5pt; font-weight: bold; margin-top: 10pt; }
p { margin-top: 4pt; margin-bottom: 4pt; }
.meta { font-size: 10pt; }
.disclaimer { font-size: 9pt; font-style: italic; }
"""


def _render_pdf(body_html: str, footer: str, path: Path) -> None:
    story = pymupdf.Story(html=f"<body>{body_html}</body>", user_css=_CSS)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 54, -54, -72)
    writer = pymupdf.DocumentWriter(str(path))
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    # Running footer on every page (exercises header/footer stripping in ingestion).
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            page.insert_text((54, mediabox.height - 40), footer, fontsize=7.5)
            page.insert_text(
                (mediabox.width - 110, mediabox.height - 40), f"Page {number} of {doc.page_count}", fontsize=7.5
            )
        doc.saveIncr()


def _regulation_html(reg: corpus.RegulationDoc) -> str:
    esc = html.escape
    parts = [
        f"<h1>{esc(reg.title)}</h1>",
        f"<p class='disclaimer'>{esc(corpus.DISCLAIMER)}</p>",
        f"<p class='meta'>{esc(reg.regulator)}</p>",
        f"<p class='meta'>{esc(reg.ref_no)}</p>",
        f"<p class='meta'>Dated: {esc(reg.dated)}</p>",
        f"<p>{esc(reg.preamble)}</p>",
    ]
    for chapter in reg.chapters:
        parts.append(f"<h2>CHAPTER {chapter.number} - {esc(chapter.title)}</h2>")
        for para in chapter.paras:
            if not para.text:
                parts.append(f"<p><b>{esc(para.path)}. {esc(para.title or '')}</b></p>")
            else:
                parts.append(f"<p>{esc(para.path)} {esc(para.text)}</p>")
    return "".join(parts)


def _policy_html(title: str, intro: str, controls: list[corpus.ControlSpec]) -> str:
    esc = html.escape
    parts = [
        f"<h1>{esc(title)}</h1>",
        f"<p class='disclaimer'>{esc(corpus.DISCLAIMER)}</p>",
        f"<p class='meta'>{esc(corpus.BANK_NAME)}</p>",
        "<p><b>1. Purpose</b></p>",
        f"<p>1.1 {esc(intro)}</p>",
    ]
    for index, control in enumerate(controls, start=2):
        parts.append(f"<p><b>{index}. {esc(control.name)}</b></p>")
        parts.append(
            f"<p>{index}.1 {esc(control.description)} Control reference: {control.key}. "
            f"Owner: {esc(control.owner)}.</p>"
        )
    return "".join(parts)


def _write_xlsx(path: Path, header: list[str], rows: list[list[object]]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = path.stem[:30]
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def generate(out: Path) -> dict[str, object]:
    today = date.today()
    reg_dir = out / "synthetic" / "regulations"
    bank_dir = out / "synthetic" / "bank"
    evidence_dir = bank_dir / "evidence"
    golden_dir = out / "golden"
    for directory in (reg_dir, bank_dir, evidence_dir, golden_dir):
        directory.mkdir(parents=True, exist_ok=True)

    footer = "SYNTHETIC - not an actual regulatory instrument"
    for reg in corpus.REGULATIONS:
        _render_pdf(_regulation_html(reg), footer, reg_dir / reg.filename)
    amendment_html = "".join(f"<p>{html.escape(line)}</p>" for line in corpus.ITG_AMENDMENT_TEXT.splitlines())
    _render_pdf(
        f"<p class='disclaimer'>{html.escape(corpus.DISCLAIMER)}</p>{amendment_html}",
        footer,
        reg_dir / "SYN_IT_Governance_Amendment_2025.pdf",
    )

    kyc_controls = [c for c in corpus.CONTROLS if c.key.startswith("C-KYC")]
    it_controls = [c for c in corpus.CONTROLS if c.key.startswith("C-IT")]
    bank_footer = "SYNTHETIC - Aravali Bank Ltd is a fictional bank"
    _render_pdf(
        _policy_html(
            "KYC and AML Policy",
            "This policy sets out how the Bank meets its Know Your Customer and anti-money laundering obligations.",
            kyc_controls,
        ),
        bank_footer,
        bank_dir / "Aravali_KYC_AML_Policy.pdf",
    )
    _render_pdf(
        _policy_html(
            "Information Security Policy",
            "This policy sets out the Bank's information security and IT resilience controls.",
            it_controls,
        ),
        bank_footer,
        bank_dir / "Aravali_Information_Security_Policy.pdf",
    )

    _write_xlsx(
        bank_dir / "control_matrix.xlsx",
        [
            "Control ID",
            "Control Name",
            "Control Description",
            "Control Type",
            "Automation",
            "Frequency",
            "Control Owner",
            "Business Unit",
            "Systems",
            "Procedure Reference",
        ],
        [
            [
                c.key,
                c.name,
                c.description,
                c.control_type.title(),
                c.automation.replace("_", "-").title(),
                _FREQ_TEXT.get(c.frequency, c.frequency),
                c.owner,
                c.business_unit,
                ", ".join(c.systems),
                c.procedure_ref,
            ]
            for c in corpus.CONTROLS
        ],
    )
    _write_xlsx(
        bank_dir / "control_tests.xlsx",
        ["Control ID", "Test Date", "Result", "Sample Size", "Exceptions", "Tester", "Notes"],
        [
            [key, (today - timedelta(days=days)).isoformat(), result, sample, exc, "Internal Controls Testing", notes]
            for key, days, result, sample, exc, notes in corpus.CONTROL_TESTS
        ],
    )
    register_rows = []
    for ev in corpus.EVIDENCE:
        filename = f"{ev.key}_{ev.control_key}.pdf"
        collected = today - timedelta(days=ev.days_ago)
        body = "".join(f"<p>{html.escape(line)}</p>" for line in ev.body)
        _render_pdf(
            f"<h1>{html.escape(ev.title)}</h1><p class='disclaimer'>{html.escape(corpus.DISCLAIMER)}</p>"
            f"<p class='meta'>{html.escape(corpus.BANK_NAME)} - Date: {collected.strftime('%B %d, %Y')}</p>{body}",
            bank_footer,
            evidence_dir / filename,
        )
        register_rows.append(
            [
                ev.key,
                ev.control_key,
                ev.title,
                ev.evidence_type,
                filename,
                collected.isoformat(),
                (collected - timedelta(days=90)).isoformat(),
                collected.isoformat(),
                ev.source_system,
                ev.authenticity,
            ]
        )
    _write_xlsx(
        bank_dir / "evidence_register.xlsx",
        [
            "Evidence ID",
            "Control ID",
            "Title",
            "Evidence Type",
            "File",
            "Collected On",
            "Period Start",
            "Period End",
            "Source System",
            "Authenticity",
        ],
        register_rows,
    )

    _write_golden(golden_dir)
    manifest = {
        "generated_on": today.isoformat(),
        "synthetic": True,
        "disclaimer": corpus.DISCLAIMER,
        "bank": corpus.BANK_NAME,
        "regulations": [
            {"doc_id": r.doc_id, "file": f"synthetic/regulations/{r.filename}", "ref_no": r.ref_no}
            for r in corpus.REGULATIONS
        ],
    }
    (out / "synthetic" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _write_golden(golden_dir: Path) -> None:
    obligations = []
    for reg in corpus.REGULATIONS:
        for chapter in reg.chapters:
            for para in chapter.paras:
                assert all(o.quote in para.text for o in para.obligations), (reg.doc_id, para.path)
                obligations.extend(
                    {
                        "doc_id": reg.doc_id,
                        "section_path": para.path,
                        "quote": o.quote,
                        "obligation_type": o.obligation_type,
                        "modality": o.modality,
                        "period": o.period,
                    }
                    for o in para.obligations
                )
    _jsonl(golden_dir / "obligations.jsonl", obligations)
    _jsonl(
        golden_dir / "mappings.jsonl",
        [{"doc_id": d, "section_path": p, "control_keys": list(keys)} for (d, p), keys in corpus.GOLD_MAPPINGS.items()],
    )
    _jsonl(
        golden_dir / "gaps.jsonl",
        [{"doc_id": d, "section_path": p, "gap_types": list(types)} for d, p, types in corpus.GOLD_GAPS],
    )
    _jsonl(
        golden_dir / "changes.jsonl",
        [
            {"regulation": "SYN/KYC", "section_path": p, "change_type": t, "is_material": m}
            for p, t, m in corpus.GOLD_CHANGES
        ],
    )
    _jsonl(
        golden_dir / "conflicts.jsonl",
        [
            {"a": {"doc_id": a[0], "section_path": a[1]}, "b": {"doc_id": b[0], "section_path": b[1]}}
            for a, b in corpus.GOLD_CONFLICTS
        ],
    )
    _jsonl(
        golden_dir / "clusters.jsonl",
        [{"members": [{"doc_id": d, "section_path": p} for d, p in members]} for members in corpus.GOLD_CLUSTERS],
    )
    _jsonl(
        golden_dir / "retrieval.jsonl",
        [
            {"query": q, "relevant": [{"doc_id": d, "section_path": p} for d, p in rel]}
            for q, rel in corpus.GOLD_RETRIEVAL
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data"))
    args = parser.parse_args()
    result = generate(Path(args.out))
    print(json.dumps(result, indent=2))
