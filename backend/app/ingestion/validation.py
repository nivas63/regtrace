"""Upload validation: the first trust boundary for untrusted documents.

Checks: size limit, type by magic bytes (never by extension alone), encrypted PDFs,
active PDF content (JavaScript / launch actions / embedded files), page limits, and
zip-bomb protection for OOXML containers.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from enum import StrEnum

import pymupdf

from app.core.errors import DomainValidationError


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    HTML = "html"
    TEXT = "text"  # .txt / .md
    CSV = "csv"


MIME = {
    FileKind.PDF: "application/pdf",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileKind.HTML: "text/html",
    FileKind.TEXT: "text/plain",
    FileKind.CSV: "text/csv",
}

_MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
_MAX_ZIP_RATIO = 100
_PDF_ACTIVE_MARKERS = (b"/JavaScript", b"/JS ", b"/Launch", b"/EmbeddedFile", b"/RichMedia")


@dataclass(frozen=True)
class ValidatedFile:
    kind: FileKind
    mime_type: str
    page_count: int | None
    warnings: list[str]


def validate_upload(filename: str, data: bytes, *, max_bytes: int, max_pdf_pages: int) -> ValidatedFile:
    if not data:
        raise DomainValidationError("Empty file")
    if len(data) > max_bytes:
        raise DomainValidationError(f"File exceeds {max_bytes // (1024 * 1024)} MB limit")

    name = filename.lower()
    if data.startswith(b"%PDF-"):
        return _validate_pdf(data, max_pdf_pages)
    if data.startswith(b"PK\x03\x04"):
        return _validate_ooxml(data)
    return _validate_text(name, data)


def _validate_pdf(data: bytes, max_pages: int) -> ValidatedFile:
    for marker in _PDF_ACTIVE_MARKERS:
        if marker in data:
            raise DomainValidationError(f"PDF contains active content ({marker.decode().strip()}); rejected")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise DomainValidationError("Encrypted PDFs are not accepted")
            pages = doc.page_count
    except DomainValidationError:
        raise
    except Exception as exc:
        raise DomainValidationError(f"Unreadable PDF: {type(exc).__name__}") from exc
    if pages > max_pages:
        raise DomainValidationError(f"PDF has {pages} pages; limit is {max_pages}")
    return ValidatedFile(FileKind.PDF, MIME[FileKind.PDF], pages, [])


def _validate_ooxml(data: bytes) -> ValidatedFile:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)
            compressed = max(1, sum(i.compress_size for i in infos))
            if total > _MAX_ZIP_UNCOMPRESSED or total / compressed > _MAX_ZIP_RATIO:
                raise DomainValidationError("Archive expands too much (possible zip bomb)")
            names = {i.filename for i in infos}
    except zipfile.BadZipFile as exc:
        raise DomainValidationError("Corrupt Office document") from exc

    if any(n.lower().endswith(".bin") and "vba" in n.lower() for n in names):
        raise DomainValidationError("Macro-enabled documents are not accepted")
    if "word/document.xml" in names:
        return ValidatedFile(FileKind.DOCX, MIME[FileKind.DOCX], None, [])
    if "xl/workbook.xml" in names:
        return ValidatedFile(FileKind.XLSX, MIME[FileKind.XLSX], None, [])
    raise DomainValidationError("Unsupported Office document type")


def _validate_text(name: str, data: bytes) -> ValidatedFile:
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DomainValidationError("Unsupported or binary file type") from exc
    if "\x00" in decoded:
        raise DomainValidationError("Binary content in text file")
    head = decoded.lstrip()[:500].lower()
    if name.endswith((".html", ".htm")) or head.startswith(("<!doctype html", "<html")):
        return ValidatedFile(FileKind.HTML, MIME[FileKind.HTML], None, [])
    if name.endswith(".csv"):
        return ValidatedFile(FileKind.CSV, MIME[FileKind.CSV], None, [])
    return ValidatedFile(FileKind.TEXT, MIME[FileKind.TEXT], None, [])
