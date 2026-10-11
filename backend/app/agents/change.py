"""Regulatory change intelligence.

old version + new version
  -> section alignment (paragraph number, then title/text similarity)   [deterministic]
  -> text diff + editorial pre-check (identical after normalisation)     [deterministic]
  -> semantic classification of the remaining changes                     [LLM, batched]
  -> RegulatoryChange rows (+ which paragraphs need re-extraction)

Amendment circulars ("para 3.2 shall be substituted by ...") are converted to
structured edit instructions by the LLM and applied deterministically to the
previous version, producing a consolidated version that a human must confirm.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select

from app.agents.schemas import AmendmentParse, ChangeBatch
from app.domain.types import ChangeCategory
from app.domain.verification import normalize, text_similarity
from app.ingestion.chunking import build_header, chunk_section
from app.llm.errors import LLMError
from app.llm.prompts import DocumentBlock, load_prompt, spotlight
from app.llm.types import ModelTier
from app.models import Chunk, Document, HumanReview, RegulationVersion, RegulatoryChange, Section
from app.services import audit
from app.workflows.engine import StepContext

_PUNCT = re.compile(r"[^\w\s]")
_ALIGN_THRESHOLD = 0.55
_BATCH = 4


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    editorial: list[str] = field(default_factory=list)
    material: list[str] = field(default_factory=list)
    change_ids: list[str] = field(default_factory=list)

    def changed_paths(self) -> set[str]:
        return set(self.added) | set(self.modified)


def _content_equal(a: str, b: str) -> bool:
    return " ".join(_PUNCT.sub(" ", normalize(a)).split()) == " ".join(_PUNCT.sub(" ", normalize(b)).split())


def align_sections(old: list[Section], new: list[Section]) -> list[tuple[Section | None, Section | None, float]]:
    """Pair paragraphs of two versions. Same number first; renumbered paragraphs are
    matched greedily by text similarity above a threshold."""
    old_by_path = {s.section_path: s for s in old}
    pairs: list[tuple[Section | None, Section | None, float]] = []
    unmatched_new: list[Section] = []
    used_old: set[str] = set()
    for section in new:
        match = old_by_path.get(section.section_path)
        if match is not None:
            pairs.append(
                (
                    match,
                    section,
                    1.0 if match.content_hash == section.content_hash else text_similarity(match.text, section.text),
                )
            )
            used_old.add(match.section_path)
        else:
            unmatched_new.append(section)
    remaining_old = [s for s in old if s.section_path not in used_old]
    for section in unmatched_new:
        best = max(remaining_old, key=lambda o: text_similarity(o.text, section.text), default=None)
        score = text_similarity(best.text, section.text) if best else 0.0
        if best is not None and score >= _ALIGN_THRESHOLD:
            pairs.append((best, section, score))
            remaining_old.remove(best)
        else:
            pairs.append((None, section, 0.0))
    pairs.extend((s, None, 0.0) for s in remaining_old)
    return pairs


def word_diff(old: str, new: str) -> list[dict[str, str]]:
    old_words, new_words = old.split(), new.split()
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_words, new_words).get_opcodes():
        if tag != "equal":
            ops.append({"op": tag, "old": " ".join(old_words[i1:i2]), "new": " ".join(new_words[j1:j2])})
    return ops


async def diff_versions(ctx: StepContext, previous_version_id: uuid.UUID, new_version_id: uuid.UUID) -> DiffResult:
    services = ctx.services
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        new_version = await session.get(RegulationVersion, new_version_id)
        assert new_version is not None

        def paragraphs(version_id: uuid.UUID) -> Any:
            return (
                select(Section)
                .where(Section.version_id == version_id, Section.section_type == "PARAGRAPH", Section.text != "")
                .order_by(Section.ordinal)
            )

        old = list((await session.scalars(paragraphs(previous_version_id))).all())
        new = list((await session.scalars(paragraphs(new_version_id))).all())

    result = DiffResult()
    to_classify: list[tuple[Section, Section, float]] = []
    rows: list[RegulatoryChange] = []
    for old_s, new_s, similarity in align_sections(old, new):
        if old_s is not None and new_s is not None:
            if old_s.content_hash == new_s.content_hash:
                continue
            if _content_equal(old_s.text, new_s.text):
                result.editorial.append(new_s.section_path)
                rows.append(
                    _change_row(
                        new_version,
                        previous_version_id,
                        old_s,
                        new_s,
                        similarity,
                        "MODIFIED",
                        ChangeCategory.EDITORIAL.value,
                        False,
                        "Punctuation/whitespace-only change",
                        1.0,
                    )
                )
                continue
            to_classify.append((old_s, new_s, similarity))
        elif new_s is not None:
            result.added.append(new_s.section_path)
            rows.append(
                _change_row(
                    new_version,
                    previous_version_id,
                    None,
                    new_s,
                    None,
                    "ADDED",
                    ChangeCategory.NEW_REQUIREMENT.value,
                    True,
                    f"New paragraph {new_s.section_path} added",
                    1.0,
                )
            )
        elif old_s is not None:
            result.removed.append(old_s.section_path)
            rows.append(
                _change_row(
                    new_version,
                    previous_version_id,
                    old_s,
                    None,
                    None,
                    "REMOVED",
                    ChangeCategory.REQUIREMENT_REMOVED.value,
                    True,
                    f"Paragraph {old_s.section_path} removed",
                    1.0,
                )
            )

    prompt = load_prompt("change_classification")
    for start in range(0, len(to_classify), _BATCH):
        batch = to_classify[start : start + _BATCH]
        content = "\n\n".join(
            f"Pair {old_s.section_path}:\n"
            + spotlight(
                [
                    DocumentBlock(f"old-{old_s.section_path}", "previous version", old_s.text),
                    DocumentBlock(f"new-{new_s.section_path}", "new version", new_s.text),
                ]
            )
            for old_s, new_s, _ in batch
        )
        judgements: dict[str, Any] = {}
        try:
            outcome = await services.llm.structured(
                ctx=ctx.llm_ctx("change_analyst"),
                prompt=prompt,
                user_content=content,
                schema=ChangeBatch,
                tier=ModelTier.REASONING,
                max_output_tokens=2000,
            )
            judgements = {j.pair_id.replace("Pair", "").strip(): j for j in outcome.value.changes}
        except LLMError as exc:
            ctx.decide(
                "change_classification_fallback", f"LLM unavailable ({exc.code}); treating textual changes as material"
            )
        for old_s, new_s, similarity in batch:
            judgement = judgements.get(new_s.section_path) or judgements.get(old_s.section_path)
            if judgement is None:
                category, material, summary, confidence = "TIGHTENED", True, "Text changed (unclassified)", 0.4
            else:
                category, material = judgement.category.value, judgement.is_material
                summary, confidence = judgement.summary, judgement.confidence
            result.modified.append(new_s.section_path)
            (result.material if material else result.editorial).append(new_s.section_path)
            rows.append(
                _change_row(
                    new_version,
                    previous_version_id,
                    old_s,
                    new_s,
                    similarity,
                    "MODIFIED",
                    category,
                    material,
                    summary,
                    confidence,
                )
            )

    async with services.db.tenant_session(ctx.run.bank_id) as session:
        # Idempotent retries: one change set per (previous, new) version pair.
        await session.execute(
            delete(RegulatoryChange).where(
                RegulatoryChange.from_version_id == previous_version_id,
                RegulatoryChange.to_version_id == new_version_id,
            )
        )
        for row in rows:
            session.add(row)
        await session.flush()
        result.change_ids = [str(r.id) for r in rows]
        await audit.record(
            session,
            bank_id=ctx.run.bank_id,
            actor=audit.Actor.agent("change_analyst"),
            action="regulation.diffed",
            subject_type="regulation_version",
            subject_id=new_version_id,
            run_id=ctx.run.id,
            details={
                "added": result.added,
                "removed": result.removed,
                "modified": result.modified,
                "material": result.material,
                "editorial": result.editorial,
            },
        )
    ctx.decide(
        "change_detected",
        f"{len(result.material) + len(result.added)} material change(s), "
        f"{len(result.editorial)} editorial, {len(result.removed)} removed",
    )
    return result


def _change_row(
    version: RegulationVersion,
    previous_id: uuid.UUID,
    old_s: Section | None,
    new_s: Section | None,
    similarity: float | None,
    change_type: str,
    category: str,
    material: bool,
    summary: str,
    confidence: float,
) -> RegulatoryChange:
    return RegulatoryChange(
        regulation_id=version.regulation_id,
        from_version_id=previous_id,
        to_version_id=version.id,
        section_path=(new_s or old_s).section_path if (new_s or old_s) else "?",  # type: ignore[union-attr]
        change_type=change_type,
        semantic_category=category,
        is_material=material,
        summary=summary,
        old_text=old_s.text if old_s else None,
        new_text=new_s.text if new_s else None,
        similarity=round(similarity, 3) if similarity is not None else None,
        diff=word_diff(old_s.text, new_s.text) if old_s and new_s else [],
        confidence=confidence,
        old_section_id=old_s.id if old_s else None,
        new_section_id=new_s.id if new_s else None,
    )


# ----------------------------------------------------------------------------------
# Amendment circulars
# ----------------------------------------------------------------------------------


async def apply_amendment(
    ctx: StepContext, amendment_document_id: uuid.UUID, version_id: uuid.UUID, previous_version_id: uuid.UUID
) -> dict[str, Any]:
    """Parse the circular into edit instructions (LLM), apply them to the previous
    version (code), store the consolidated text as the new version's sections."""
    services = ctx.services
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        amendment_sections = (
            await session.scalars(
                select(Section).where(Section.document_id == amendment_document_id).order_by(Section.ordinal)
            )
        ).all()
        previous = list(
            (
                await session.scalars(
                    select(Section).where(Section.version_id == previous_version_id).order_by(Section.ordinal)
                )
            ).all()
        )
        previous_version = await session.get(RegulationVersion, previous_version_id)
        assert previous_version is not None
        base_doc = await session.get(Document, previous_version.document_id)
        assert base_doc is not None

    circular = "\n".join(s.text for s in amendment_sections)
    outcome = await services.llm.structured(
        ctx=ctx.llm_ctx("amendment_parser"),
        prompt=load_prompt("amendment_parsing"),
        user_content=spotlight([DocumentBlock("circular", "amendment circular", circular)]),
        schema=AmendmentParse,
        tier=ModelTier.REASONING,
        max_output_tokens=3000,
    )
    texts = {s.section_path: s.text for s in previous}
    applied, failed = [], []
    for ins in outcome.value.instructions:
        if ins.op == "SUBSTITUTE_TEXT" and ins.target_path in texts and ins.old_text and ins.new_text:
            current = texts[ins.target_path]
            if normalize(ins.old_text) in normalize(current):
                pattern = re.compile(r"\s+".join(map(re.escape, ins.old_text.split())), re.I)
                texts[ins.target_path] = pattern.sub(ins.new_text, current, count=1)
                applied.append(ins.model_dump())
                continue
        elif ins.op == "REPLACE_PARAGRAPH" and ins.target_path in texts and ins.new_text:
            texts[ins.target_path] = ins.new_text
            applied.append(ins.model_dump())
            continue
        elif ins.op == "DELETE_PARAGRAPH" and ins.target_path in texts:
            texts.pop(ins.target_path)
            applied.append(ins.model_dump())
            continue
        elif ins.op == "INSERT_PARAGRAPH" and ins.new_path and ins.new_text:
            texts[ins.new_path] = ins.new_text
            applied.append(ins.model_dump())
            continue
        failed.append({**ins.model_dump(), "error": "target text not found; not applied"})

    async with services.db.tenant_session(ctx.run.bank_id) as session:
        version = await session.get(RegulationVersion, version_id)
        assert version is not None
        consolidated = Document(
            bank_id=None,
            sha256=hashlib.sha256(f"consolidated:{version_id}".encode()).hexdigest(),
            filename=f"consolidated-v{version.version_no}.txt",
            mime_type="text/plain",
            size_bytes=sum(len(t) for t in texts.values()),
            storage_path=base_doc.storage_path,
            doc_class="REGULATION",
            status="PARSED",
            title=base_doc.title,
            doc_metadata={"consolidated_from": str(previous_version_id), "amendment": str(amendment_document_id)},
            is_synthetic=base_doc.is_synthetic,
        )
        session.add(consolidated)
        await session.flush()
        version.document_id = consolidated.id
        rows: list[tuple[Chunk, str]] = []
        for ordinal, source in enumerate(previous):
            if source.section_path not in texts and source.section_type == "PARAGRAPH":
                continue
            body = texts.get(source.section_path, source.text)
            section = Section(
                bank_id=None,
                document_id=consolidated.id,
                version_id=version_id,
                section_path=source.section_path,
                title=source.title,
                level=source.level,
                ordinal=ordinal,
                section_type=source.section_type,
                text=body,
                content_hash=hashlib.sha256(" ".join(body.split()).lower().encode()).hexdigest(),
                page_from=source.page_from,
                page_to=source.page_to,
            )
            session.add(section)
            await session.flush()
            for piece in chunk_section(body):
                chunk = Chunk(
                    bank_id=None,
                    document_id=consolidated.id,
                    section_id=section.id,
                    version_id=version_id,
                    ordinal=piece.ordinal,
                    header=build_header(base_doc.title, None, source.section_path, source.title),
                    text=piece.text,
                    token_count=piece.token_count,
                    char_start=piece.char_start,
                    doc_class="REGULATION",
                    effective_from=version.effective_from,
                    is_current=False,
                )
                rows.append((chunk, f"{chunk.header}\n{chunk.text}"))
        vectors = await services.embedder.embed_documents([t for _, t in rows])
        for (chunk, _), vector in zip(rows, vectors, strict=True):
            chunk.embedding = vector
            session.add(chunk)
        session.add(
            HumanReview(
                bank_id=ctx.run.bank_id,
                subject_type="AMENDMENT",
                subject_id=version_id,
                reason=f"Confirm consolidated text after applying {len(applied)} amendment instruction(s)"
                + (f"; {len(failed)} could not be applied" if failed else ""),
                recommendation={"applied": applied, "failed": failed},
                priority="HIGH",
                run_id=ctx.run.id,
            )
        )
        await audit.record(
            session,
            bank_id=ctx.run.bank_id,
            actor=audit.Actor.agent("amendment_parser"),
            action="amendment.applied",
            subject_type="regulation_version",
            subject_id=version_id,
            run_id=ctx.run.id,
            details={"applied": len(applied), "failed": len(failed)},
        )
        from app.agents.regulatory import attach_version  # local import avoids a cycle

        await attach_version(session, version, consolidated.id, previous_version_id)
    return {"applied": applied, "failed": failed, "consolidated_document_id": str(consolidated.id)}
