"""Document ingestion: upload -> validate -> store -> (workflow) parse -> sections ->
chunks -> embeddings, plus deterministic importers for structured bank records.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Services
from app.core.errors import DomainValidationError, NotFoundError
from app.domain.frequency import normalize_control_frequency
from app.domain.types import BANK_DOC_CLASSES, DocClass
from app.ingestion.chunking import build_header, chunk_section
from app.ingestion.metadata import extract_metadata
from app.ingestion.parsers import ParsedDocument, parse
from app.ingestion.structure import SectionDraft, detect_sections, document_title
from app.ingestion.validation import FileKind, validate_upload
from app.models import Chunk, Control, ControlTest, Document, Evidence, Section
from app.observability.logging import get_logger
from app.security import injection
from app.services import audit

_log = get_logger("regtrace.ingestion")
_EXT = {
    FileKind.PDF: "pdf",
    FileKind.DOCX: "docx",
    FileKind.XLSX: "xlsx",
    FileKind.HTML: "html",
    FileKind.TEXT: "txt",
    FileKind.CSV: "csv",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


@dataclass(frozen=True)
class UploadOutcome:
    document: Document
    duplicate: bool
    run_id: uuid.UUID | None


def _kind_from_mime(mime: str) -> FileKind:
    for kind, value in {
        FileKind.PDF: "application/pdf",
        FileKind.DOCX: "wordprocessingml",
        FileKind.XLSX: "spreadsheetml",
        FileKind.HTML: "text/html",
        FileKind.CSV: "text/csv",
    }.items():
        if value in mime:
            return kind
    return FileKind.TEXT


class IngestionService:
    def __init__(self, services: Services) -> None:
        self.s = services

    # -- upload ---------------------------------------------------------------------

    async def upload(
        self,
        *,
        bank_id: uuid.UUID,
        user_id: uuid.UUID | None,
        filename: str,
        data: bytes,
        doc_class: DocClass,
        attributes: dict[str, Any] | None = None,
        source_url: str | None = None,
        start_workflow: bool = True,
    ) -> UploadOutcome:
        settings = self.s.settings
        validated = validate_upload(
            filename,
            data,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            max_pdf_pages=settings.max_pdf_pages,
        )
        sha = hashlib.sha256(data).hexdigest()
        # Regulatory documents are public (shared by all tenants); bank documents are private.
        owner = None if doc_class in (DocClass.REGULATION, DocClass.AMENDMENT) else bank_id
        safe_name = _SAFE_NAME.sub("_", PurePath(filename).name)[:300] or "upload"

        async with self.s.db.tenant_session(bank_id) as session:
            existing = await session.scalar(
                select(Document).where(
                    Document.sha256 == sha,
                    Document.bank_id.is_(None) if owner is None else Document.bank_id == owner,
                )
            )
            if existing is not None:
                await audit.record(
                    session,
                    bank_id=bank_id,
                    actor=_actor(user_id),
                    action="document.duplicate_upload",
                    subject_type="document",
                    subject_id=existing.id,
                    details={"filename": safe_name},
                )
                return UploadOutcome(existing, True, None)

            key = await self.s.store.put(f"{sha}.{_EXT[validated.kind]}", data)
            document = Document(
                bank_id=owner,
                sha256=sha,
                filename=safe_name,
                mime_type=validated.mime_type,
                size_bytes=len(data),
                storage_path=key,
                doc_class=doc_class.value,
                status="UPLOADED",
                source_url=source_url,
                page_count=validated.page_count,
                doc_metadata={"attributes": attributes or {}, "uploaded_by_bank": str(bank_id)},
                uploaded_by=user_id,
            )
            session.add(document)
            await session.flush()
            await audit.record(
                session,
                bank_id=bank_id,
                actor=_actor(user_id),
                action="document.uploaded",
                subject_type="document",
                subject_id=document.id,
                details={"filename": safe_name, "doc_class": doc_class.value, "sha256": sha, "size": len(data)},
            )

        run_id = None
        if start_workflow:
            run = await self.s.engine.enqueue(
                bank_id=bank_id,
                workflow_type="process_document",
                input={"document_id": str(document.id), "doc_class": doc_class.value},
                triggered_by=f"user:{user_id}" if user_id else "system",
                idempotency_key=f"process:{bank_id}:{document.id}",
            )
            run_id = run.id
        return UploadOutcome(document, False, run_id)

    # -- parse & index --------------------------------------------------------------

    async def parse_and_index(self, bank_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
        async with self.s.db.tenant_session(bank_id) as session:
            document = await session.get(Document, document_id)
            if document is None:
                raise NotFoundError(f"Document {document_id} not found")
            raw = await self.s.store.get(document.storage_path)
            kind = _kind_from_mime(document.mime_type)
            parsed = parse(kind, raw)
            drafts = self._sections_for(parsed)
            full_text = "\n".join(line.text for line in parsed.lines)
            metadata = extract_metadata(full_text)
            title = document_title(parsed, drafts) if kind is not FileKind.XLSX else PurePath(document.filename).stem

            # Idempotent re-run: clear previous sections/chunks of this document.
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await session.execute(delete(Section).where(Section.document_id == document_id))

            sections: list[Section] = []
            path_to_id: dict[str, uuid.UUID] = {}
            for ordinal, draft in enumerate(drafts):
                section = Section(
                    bank_id=document.bank_id,
                    document_id=document_id,
                    section_path=draft.section_path,
                    title=draft.title,
                    level=draft.level,
                    ordinal=ordinal,
                    section_type=draft.section_type,
                    text=draft.text,
                    content_hash=draft.content_hash,
                    page_from=draft.page_from,
                    page_to=draft.page_to,
                    parent_id=path_to_id.get(draft.parent_path) if draft.parent_path else None,
                )
                session.add(section)
                await session.flush()
                path_to_id[draft.section_path] = section.id
                sections.append(section)

            chunk_rows: list[tuple[Chunk, str]] = []
            quarantined = 0
            for draft, section in zip(drafts, sections, strict=True):
                header = build_header(title, draft.chapter_title, draft.section_path, draft.title)
                for piece in chunk_section(draft.text):
                    scan = injection.scan(piece.text)
                    quarantined += scan.quarantine
                    chunk = Chunk(
                        bank_id=document.bank_id,
                        document_id=document_id,
                        section_id=section.id,
                        ordinal=piece.ordinal,
                        header=header,
                        text=piece.text,
                        token_count=piece.token_count,
                        char_start=piece.char_start,
                        doc_class=document.doc_class,
                        injection_risk=scan.score,
                        quarantined=scan.quarantine,
                        is_current=True,
                        chunk_metadata={"injection_signals": scan.signals, "page_from": draft.page_from},
                    )
                    chunk_rows.append((chunk, f"{header}\n{piece.text}"))

            vectors = await self.s.embedder.embed_documents([t for _, t in chunk_rows])
            for (chunk, _), vector in zip(chunk_rows, vectors, strict=True):
                chunk.embedding = vector
                session.add(chunk)

            document.title = title
            document.status = "PARSED"
            document.is_synthetic = metadata.is_synthetic
            document.page_count = parsed.page_count
            document.doc_metadata = {**document.doc_metadata, **metadata.to_json()}
            document.parse_report = {
                "sections": len(sections),
                "paragraphs": sum(1 for d in drafts if d.section_type == "PARAGRAPH"),
                "chunks": len(chunk_rows),
                "tables": len(parsed.tables),
                "ocr_pages": parsed.ocr_pages,
                "quarantined_chunks": quarantined,
                "warnings": parsed.warnings,
                "embedding_model": self.s.embedder.name,
            }
            await audit.record(
                session,
                bank_id=bank_id,
                actor=audit.Actor.agent("ingestion"),
                action="document.parsed",
                subject_type="document",
                subject_id=document_id,
                details=document.parse_report,
            )
            report = dict(document.parse_report)
            report["records"] = {k: len(v) for k, v in parsed.records.items()}
        return report

    def _sections_for(self, parsed: ParsedDocument) -> list[SectionDraft]:
        if parsed.kind in (FileKind.XLSX, FileKind.CSV):
            drafts = []
            for index, table in enumerate(parsed.tables, start=1):
                # Row-aligned sections keep each record citable on its own.
                header = table.rows[0]
                for row_no, row in enumerate(table.rows[1:], start=1):
                    text = "; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False) if v)
                    drafts.append(
                        SectionDraft(f"S{index}/R{row_no}", row[0][:200] if row else None, 2, "PARAGRAPH", [text])
                    )
            return drafts
        return detect_sections(parsed)

    async def parsed_records(self, bank_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
        async with self.s.db.tenant_session(bank_id) as session:
            document = await session.get(Document, document_id)
            if document is None:
                raise NotFoundError(f"Document {document_id} not found")
            raw = await self.s.store.get(document.storage_path)
        return parse(_kind_from_mime(document.mime_type), raw).records

    # -- structured importers (deterministic) ------------------------------------------

    async def import_control_matrix(self, bank_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
        records = [r for rows in (await self.parsed_records(bank_id, document_id)).values() for r in rows]
        created, updated, skipped = 0, 0, []
        texts: list[tuple[Control, str]] = []
        async with self.s.db.tenant_session(bank_id) as session:
            for raw in records:
                row = _normalise_keys(raw, _CONTROL_HEADERS)
                key = (row.get("control_key") or "").strip()
                if not key or not row.get("name"):
                    skipped.append(raw)
                    continue
                attrs = {
                    "name": row["name"].strip(),
                    "description": (row.get("description") or row["name"]).strip(),
                    "control_type": _enum(
                        row.get("control_type"), ("PREVENTIVE", "DETECTIVE", "CORRECTIVE"), "PREVENTIVE"
                    ),
                    "automation": _enum(
                        (row.get("automation") or "").replace("-", "_"),
                        ("AUTOMATED", "SEMI_AUTOMATED", "MANUAL"),
                        "MANUAL",
                    ),
                    "frequency": normalize_control_frequency(row.get("frequency")),
                    "owner": (row.get("owner") or "").strip() or None,
                    "business_unit": (row.get("business_unit") or "").strip() or None,
                    "systems": [s.strip() for s in (row.get("systems") or "").split(",") if s.strip()],
                    "procedure_ref": (row.get("procedure_ref") or "").strip() or None,
                }
                current = await session.scalar(
                    select(Control)
                    .where(Control.bank_id == bank_id, Control.control_key == key, Control.status == "ACTIVE")
                    .order_by(Control.version_no.desc())
                )
                if current is not None and all(getattr(current, k) == v for k, v in attrs.items()):
                    continue
                version = 1
                if current is not None:
                    current.status, current.valid_to = "SUPERSEDED", date.today()
                    version = current.version_no + 1
                    updated += 1
                else:
                    created += 1
                control = Control(
                    bank_id=bank_id,
                    control_key=key,
                    version_no=version,
                    source_document_id=document_id,
                    valid_from=date.today(),
                    attributes={"raw": raw},
                    **attrs,
                )
                session.add(control)
                texts.append((control, _control_text(control)))
            vectors = await self.s.embedder.embed_documents([t for _, t in texts])
            for (control, _), vector in zip(texts, vectors, strict=True):
                control.embedding = vector
            await session.flush()
            await audit.record(
                session,
                bank_id=bank_id,
                actor=audit.Actor.agent("ingestion"),
                action="controls.imported",
                subject_type="document",
                subject_id=document_id,
                details={"created": created, "updated": updated, "skipped": len(skipped)},
            )
        return {
            "created": created,
            "updated": updated,
            "skipped": len(skipped),
            "control_ids": [str(c.id) for c, _ in texts],
        }

    async def import_control_tests(self, bank_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
        records = [r for rows in (await self.parsed_records(bank_id, document_id)).values() for r in rows]
        imported, unknown = 0, set()
        touched: set[uuid.UUID] = set()
        async with self.s.db.tenant_session(bank_id) as session:
            controls = {
                c.control_key: c.id
                for c in (
                    await session.scalars(select(Control).where(Control.bank_id == bank_id, Control.status == "ACTIVE"))
                ).all()
            }
            for raw in records:
                row = _normalise_keys(raw, _TEST_HEADERS)
                key = (row.get("control_key") or "").strip()
                control_id = controls.get(key)
                tested_on = _parse_date(row.get("tested_on"))
                if control_id is None or tested_on is None:
                    unknown.add(key)
                    continue
                result = _enum(row.get("result"), ("PASS", "FAIL", "PARTIAL"), "PARTIAL")
                exists = await session.scalar(
                    select(ControlTest.id).where(
                        ControlTest.control_id == control_id,
                        ControlTest.tested_on == tested_on,
                        ControlTest.result == result,
                    )
                )
                if exists:
                    continue
                session.add(
                    ControlTest(
                        bank_id=bank_id,
                        control_id=control_id,
                        tested_on=tested_on,
                        result=result,
                        sample_size=_int(row.get("sample_size")),
                        exceptions=_int(row.get("exceptions")),
                        tester=row.get("tester"),
                        notes=row.get("notes"),
                        source_document_id=document_id,
                    )
                )
                imported += 1
                touched.add(control_id)
            await audit.record(
                session,
                bank_id=bank_id,
                actor=audit.Actor.agent("ingestion"),
                action="control_tests.imported",
                subject_type="document",
                subject_id=document_id,
                details={"imported": imported, "unknown_controls": sorted(k for k in unknown if k)},
            )
        return {
            "imported": imported,
            "unknown_controls": sorted(k for k in unknown if k),
            "control_ids": [str(c) for c in touched],
        }

    async def register_evidence(self, bank_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
        """Create the evidence record for an uploaded evidence file (metadata from upload)."""
        async with self.s.db.tenant_session(bank_id) as session:
            document = await session.get(Document, document_id)
            if document is None:
                raise NotFoundError(f"Document {document_id} not found")
            attrs: dict[str, Any] = document.doc_metadata.get("attributes", {})
            control_id = None
            if key := attrs.get("control_key"):
                control_id = await session.scalar(
                    select(Control.id).where(
                        Control.bank_id == bank_id, Control.control_key == key, Control.status == "ACTIVE"
                    )
                )
            collected = _parse_date(attrs.get("collected_on")) or document.doc_metadata.get("issued_on")
            if isinstance(collected, str):
                collected = _parse_date(collected)
            existing = await session.scalar(select(Evidence).where(Evidence.document_id == document_id))
            evidence = existing or Evidence(bank_id=bank_id, document_id=document_id)
            evidence.control_id = control_id
            evidence.evidence_type = attrs.get("evidence_type", "TEST_REPORT")
            evidence.title = attrs.get("title") or document.title or document.filename
            evidence.collected_on = collected
            evidence.period_start = _parse_date(attrs.get("period_start"))
            evidence.period_end = _parse_date(attrs.get("period_end"))
            evidence.source_system = attrs.get("source_system")
            evidence.authenticity = attrs.get("authenticity", "SIGNED_REPORT")
            if existing is None:
                session.add(evidence)
            await session.flush()
            await audit.record(
                session,
                bank_id=bank_id,
                actor=audit.Actor.agent("ingestion"),
                action="evidence.registered",
                subject_type="evidence",
                subject_id=evidence.id,
                details={"control_key": attrs.get("control_key"), "linked": control_id is not None},
            )
            return {"evidence_id": str(evidence.id), "control_id": str(control_id) if control_id else None}

    async def mark(self, bank_id: uuid.UUID, document_id: uuid.UUID, status: str) -> None:
        async with self.s.db.tenant_session(bank_id) as session:
            await session.execute(update(Document).where(Document.id == document_id).values(status=status))


# -- helpers -------------------------------------------------------------------------

_CONTROL_HEADERS = {
    "control_key": ("control id", "control ref", "control reference", "id", "control no"),
    "name": ("control name", "name", "control title", "title"),
    "description": ("control description", "description", "control activity", "details"),
    "control_type": ("control type", "type", "nature"),
    "automation": ("automation", "automation level", "manual/automated"),
    "frequency": ("frequency", "periodicity", "control frequency"),
    "owner": ("control owner", "owner", "responsible"),
    "business_unit": ("business unit", "department", "function"),
    "systems": ("systems", "system", "applications"),
    "procedure_ref": ("procedure reference", "procedure", "sop", "policy reference"),
}
_TEST_HEADERS = {
    "control_key": ("control id", "control ref", "control"),
    "tested_on": ("test date", "tested on", "date"),
    "result": ("result", "outcome", "test result"),
    "sample_size": ("sample size", "sample", "population tested"),
    "exceptions": ("exceptions", "exception count", "deviations"),
    "tester": ("tester", "tested by"),
    "notes": ("notes", "comments", "remarks"),
}


def _normalise_keys(raw: dict[str, Any], mapping: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    lowered = {str(k).strip().lower(): v for k, v in raw.items()}
    out: dict[str, Any] = {}
    for field_name, synonyms in mapping.items():
        for synonym in synonyms:
            if synonym in lowered and lowered[synonym] not in (None, ""):
                out[field_name] = str(lowered[synonym])
                break
    return out


def _enum(value: str | None, allowed: tuple[str, ...], default: str) -> str:
    candidate = (value or "").strip().upper().replace(" ", "_")
    return candidate if candidate in allowed else default


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()[:19]
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _control_text(control: Control) -> str:
    return (
        f"{control.control_key} {control.name}. {control.description} "
        f"Owner: {control.owner or 'unassigned'}. Frequency: {control.frequency or 'unspecified'}."
    )


def _actor(user_id: uuid.UUID | None) -> audit.Actor:
    return audit.Actor.user(user_id) if user_id else audit.Actor.system()


async def get_document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"Document {document_id} not found")
    return document


def is_bank_content(doc_class: str) -> bool:
    try:
        return DocClass(doc_class) in BANK_DOC_CLASSES
    except ValueError:
        return True


def require_class(value: str) -> DocClass:
    try:
        return DocClass(value)
    except ValueError as exc:
        raise DomainValidationError(f"Unknown document class {value!r}") from exc
