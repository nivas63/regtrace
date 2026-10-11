from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from app.api import serializers as S
from app.api.deps import Ingestor, Reader, ServicesDep
from app.core.errors import DomainValidationError, NotFoundError
from app.models import Document, Section, WorkflowRun
from app.services.ingestion import IngestionService, require_class

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOWED_ATTRIBUTES = {
    "control_key",
    "evidence_type",
    "title",
    "collected_on",
    "period_start",
    "period_end",
    "source_system",
    "authenticity",
}


@router.post("/upload", status_code=202)
async def upload(
    services: ServicesDep,
    principal: Ingestor,
    file: Annotated[UploadFile, File()],
    doc_class: Annotated[str, Form()],
    attributes: Annotated[str | None, Form()] = None,
    source_url: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict[str, Any]:
    """Upload a document. Returns 202 with the processing run id; processing
    (parsing, extraction, mapping, assessment, impact analysis) is asynchronous.
    Re-uploading identical content returns the existing document (content-addressed)."""
    data = await file.read(services.settings.max_upload_mb * 1024 * 1024 + 1)
    try:
        attrs = json.loads(attributes) if attributes else {}
    except json.JSONDecodeError as exc:
        raise DomainValidationError("attributes must be a JSON object") from exc
    if not isinstance(attrs, dict) or set(attrs) - _ALLOWED_ATTRIBUTES:
        raise DomainValidationError(f"attributes may only contain {sorted(_ALLOWED_ATTRIBUTES)}")
    outcome = await IngestionService(services).upload(
        bank_id=principal.bank_id,
        user_id=principal.user_id,
        filename=file.filename or "upload",
        data=data,
        doc_class=require_class(doc_class),
        attributes=attrs,
        source_url=source_url,
    )
    return {
        "document": S.dump(outcome.document, S.DOCUMENT),
        "duplicate": outcome.duplicate,
        "run_id": str(outcome.run_id) if outcome.run_id else None,
        "idempotency_key": idempotency_key,
    }


@router.get("")
async def list_documents(
    services: ServicesDep, principal: Reader, doc_class: str | None = None
) -> list[dict[str, Any]]:
    async with services.db.tenant_session(principal.bank_id) as session:
        query = select(Document).order_by(Document.created_at.desc()).limit(500)
        if doc_class:
            query = query.where(Document.doc_class == doc_class)
        documents = (await session.scalars(query)).all()
        runs = {
            r.input.get("document_id"): r
            for r in (
                await session.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.workflow_type == "process_document", WorkflowRun.bank_id == principal.bank_id
                    )
                )
            ).all()
        }
    return [
        {
            **S.dump(d, S.DOCUMENT),
            "run_id": str(runs[str(d.id)].id) if str(d.id) in runs else None,
            "run_status": runs[str(d.id)].status if str(d.id) in runs else None,
        }
        for d in documents
    ]


@router.get("/{document_id}")
async def get_document(document_id: uuid.UUID, services: ServicesDep, principal: Reader) -> dict[str, Any]:
    async with services.db.tenant_session(principal.bank_id) as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise NotFoundError("Document not found")
        sections = (
            await session.scalars(select(Section).where(Section.document_id == document_id).order_by(Section.ordinal))
        ).all()
    return {
        **S.dump(document, S.DOCUMENT),
        "metadata": document.doc_metadata,
        "sections": [S.dump(s, S.SECTION) for s in sections],
    }


@router.get("/{document_id}/file")
async def download(document_id: uuid.UUID, services: ServicesDep, principal: Reader) -> Response:
    async with services.db.tenant_session(principal.bank_id) as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise NotFoundError("Document not found")
    data = await services.store.get(document.storage_path)
    return Response(
        content=data,
        media_type=document.mime_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"', "X-Content-Type-Options": "nosniff"},
    )
