"""Regulatory agents: document intelligence / version linking, obligation extraction
(with independent verification), obligation lineage across versions, applicability.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import (
    ApplicabilityBatch,
    DocumentIntel,
    ObligationExtraction,
    ObligationVerification,
)
from app.domain.frequency import normalize_period
from app.domain.scoring import IMPACT_PROFILE
from app.domain.types import ObligationType
from app.domain.verification import text_similarity, verify_quote
from app.knowledge_graph.graph import upsert_edge
from app.llm.errors import LLMError
from app.llm.prompts import AliasMap, DocumentBlock, load_prompt, spotlight
from app.llm.types import ModelTier
from app.models import (
    Bank,
    Chunk,
    ControlObligation,
    Document,
    HumanReview,
    Obligation,
    ObligationApplicability,
    ObligationLineage,
    Regulation,
    RegulationVersion,
    Section,
)
from app.services import audit
from app.workflows.engine import StepContext

REVIEW_THRESHOLD = 0.70
_BATCH_TOKENS = 1800
_MAX_BATCH_PARAGRAPHS = 6

# ----------------------------------------------------------------------------------
# Document intelligence / version linking
# ----------------------------------------------------------------------------------


@dataclass
class LinkResult:
    relationship: str  # NEW_REGULATION | NEW_VERSION | AMENDMENT | DUPLICATE
    regulation_id: uuid.UUID
    version_id: uuid.UUID
    previous_version_id: uuid.UUID | None
    decided_by: str
    reason: str
    confidence: float


async def link_regulation(ctx: StepContext, document_id: uuid.UUID) -> LinkResult:
    """Decide which regulation a regulatory document belongs to and create its version.

    Deterministic signals first (explicit reference / supersession of a known circular,
    identical reference number, near-identical title); the LLM is consulted only when
    those signals are inconclusive.
    """
    services = ctx.services
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        document = await session.get(Document, document_id)
        assert document is not None
        meta = document.doc_metadata
        existing = await _regulation_catalogue(session)

    ref_no, refs = meta.get("ref_no"), set(meta.get("referenced_refs") or [])
    relationship, target, reason, confidence, decided_by = "NEW_REGULATION", None, "", 0.9, "RULE"
    for reg in existing:
        if ref_no and ref_no in reg["refs"]:
            relationship, target = "DUPLICATE", reg
            reason = f"Reference number {ref_no} already ingested as version {reg['latest_version_no']}"
            break
        if refs & set(reg["refs"]):
            relationship = "AMENDMENT" if meta.get("looks_like_amendment") else "NEW_VERSION"
            target = reg
            reason = f"Document explicitly references {sorted(refs & set(reg['refs']))[0]} of '{reg['title']}'"
            confidence = 0.95
            break
    title = document.title or ""
    if target is None:
        best = max(existing, key=lambda r: text_similarity(r["title"], title), default=None)
        similarity = text_similarity(best["title"], title) if best else 0.0
        if best and similarity >= 0.92:
            relationship, target, confidence = "NEW_VERSION", best, 0.8
            reason = f"Title matches existing regulation '{best['title']}' (similarity {similarity:.2f})"
        elif best and similarity >= 0.6 and services.llm.available:
            intel = await _ask_document_intel(ctx, document, meta, existing)
            decided_by, confidence, reason = "LLM", intel.confidence, intel.reason
            if intel.relationship in ("NEW_VERSION", "AMENDMENT") and intel.related_regulation_id:
                target = next((r for r in existing if r["id"] == intel.related_regulation_id), None)
                relationship = intel.relationship if target else "NEW_REGULATION"
        else:
            reason = "No existing regulation is referenced or has a matching title"
    ctx.decide("version_link", reason, relationship=relationship, decided_by=decided_by, confidence=confidence)

    async with services.db.tenant_session(ctx.run.bank_id) as session:
        document = await session.get(Document, document_id)
        assert document is not None
        effective = _date(meta.get("effective_from")) or _date(meta.get("issued_on"))
        if relationship == "DUPLICATE" and target is not None:
            document.status = "DUPLICATE"
            return LinkResult(
                relationship,
                uuid.UUID(target["id"]),
                uuid.UUID(target["latest_version_id"]),
                None,
                decided_by,
                reason,
                confidence,
            )

        if target is None:
            regulation = Regulation(
                regulator=meta.get("regulator") or "UNKNOWN",
                title=document.title or document.filename,
                ref_no=ref_no,
                is_synthetic=bool(meta.get("is_synthetic")),
            )
            session.add(regulation)
            await session.flush()
            previous_id, version_no = None, 1
        else:
            found = await session.get(Regulation, uuid.UUID(target["id"]))
            assert found is not None
            regulation = found
            previous_id = uuid.UUID(target["latest_version_id"])
            version_no = target["latest_version_no"] + 1

        version = RegulationVersion(
            regulation_id=regulation.id,
            document_id=document_id,
            version_no=version_no,
            issued_on=_date(meta.get("issued_on")),
            effective_from=effective,
            supersedes_version_id=previous_id,
            origin="AMENDMENT_APPLIED"
            if relationship == "AMENDMENT"
            else ("FULL_REISSUE" if previous_id else "ORIGINAL"),
            status="PENDING_REVIEW" if relationship == "AMENDMENT" else "ACTIVE",
        )
        session.add(version)
        await session.flush()
        if relationship != "AMENDMENT":
            await attach_version(session, version, document_id, previous_id)
        await upsert_edge(session, src=("regulation", regulation.id), rel="HAS_VERSION", dst=("version", version.id))
        if previous_id:
            await upsert_edge(
                session,
                src=("version", version.id),
                rel="AMENDS" if relationship == "AMENDMENT" else "SUPERSEDES",
                dst=("version", previous_id),
            )
        await audit.record(
            session,
            bank_id=ctx.run.bank_id,
            actor=audit.Actor.agent("document_intelligence"),
            action="regulation.version_linked",
            subject_type="regulation_version",
            subject_id=version.id,
            run_id=ctx.run.id,
            details={
                "relationship": relationship,
                "regulation": regulation.title,
                "version_no": version_no,
                "decided_by": decided_by,
                "reason": reason,
            },
        )
        return LinkResult(relationship, regulation.id, version.id, previous_id, decided_by, reason, confidence)


async def attach_version(
    session: AsyncSession, version: RegulationVersion, document_id: uuid.UUID, previous_id: uuid.UUID | None
) -> None:
    """Bind sections/chunks of a document to a version and roll the temporal window."""
    today = date.today()
    current = version.effective_from is None or version.effective_from <= today
    await session.execute(update(Section).where(Section.document_id == document_id).values(version_id=version.id))
    await session.execute(
        update(Chunk)
        .where(Chunk.document_id == document_id)
        .values(version_id=version.id, effective_from=version.effective_from, is_current=current)
    )
    sections = (await session.scalars(select(Section).where(Section.document_id == document_id))).all()
    for section in sections:
        await upsert_edge(session, src=("version", version.id), rel="HAS_SECTION", dst=("section", section.id))
    if previous_id is None:
        return
    previous = await session.get(RegulationVersion, previous_id)
    if previous is not None:
        previous.effective_to = version.effective_from or today
        if current:
            previous.status = "SUPERSEDED"
        await session.execute(
            update(Chunk)
            .where(Chunk.version_id == previous_id)
            .values(effective_to=previous.effective_to, is_current=not current)
        )


async def _regulation_catalogue(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(
                Regulation.id,
                Regulation.title,
                RegulationVersion.id,
                RegulationVersion.version_no,
                Document.doc_metadata,
            )
            .join(RegulationVersion, RegulationVersion.regulation_id == Regulation.id)
            .join(Document, Document.id == RegulationVersion.document_id)
            .order_by(RegulationVersion.version_no)
        )
    ).all()
    catalogue: dict[uuid.UUID, dict[str, Any]] = {}
    for reg_id, title, version_id, version_no, metadata in rows:
        entry = catalogue.setdefault(reg_id, {"id": str(reg_id), "title": title, "refs": []})
        if metadata.get("ref_no"):
            entry["refs"].append(metadata["ref_no"])
        entry["latest_version_id"], entry["latest_version_no"] = str(version_id), version_no
    return list(catalogue.values())


async def _ask_document_intel(
    ctx: StepContext, document: Document, meta: dict[str, Any], existing: list[dict[str, Any]]
) -> DocumentIntel:
    async with ctx.services.db.tenant_session(ctx.run.bank_id) as session:
        sections = (
            await session.scalars(
                select(Section).where(Section.document_id == document.id).order_by(Section.ordinal).limit(6)
            )
        ).all()
    head = "\n".join(s.text for s in sections)[:3000]
    content = (
        f"Extracted metadata: {json.dumps(meta, default=str)}\n"
        f"Existing regulations: {json.dumps([{k: r[k] for k in ('id', 'title', 'refs')} for r in existing])}\n\n"
        + spotlight([DocumentBlock("head", document.filename, head)])
    )
    outcome = await ctx.services.llm.structured(
        ctx=ctx.llm_ctx("document_intelligence"),
        prompt=load_prompt("document_intelligence"),
        user_content=content,
        schema=DocumentIntel,
        tier=ModelTier.FAST,
        max_output_tokens=800,
    )
    return outcome.value


# ----------------------------------------------------------------------------------
# Obligation extraction
# ----------------------------------------------------------------------------------


@dataclass
class ExtractionStats:
    sections: int = 0
    llm_batches: int = 0
    proposed: int = 0
    accepted: int = 0
    rejected_quote: int = 0
    rejected_verifier: int = 0
    sent_to_review: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _batches(sections: list[Section]) -> list[list[Section]]:
    """Group consecutive paragraphs of the same chapter into LLM-sized batches."""
    batches: list[list[Section]] = []
    current: list[Section] = []
    tokens = 0.0
    chapter: uuid.UUID | None = None
    for section in sections:
        size = len(section.text.split()) * 1.3 + 20
        if current and (
            (section.parent_id != chapter and chapter is not None and tokens > _BATCH_TOKENS / 3)
            or tokens + size > _BATCH_TOKENS
            or len(current) >= _MAX_BATCH_PARAGRAPHS
        ):
            batches.append(current)
            current, tokens = [], 0.0
        current.append(section)
        tokens += size
        chapter = section.parent_id
    if current:
        batches.append(current)
    return batches


async def extract_obligations(
    ctx: StepContext,
    version_id: uuid.UUID,
    section_ids: list[uuid.UUID] | None = None,
) -> tuple[list[uuid.UUID], ExtractionStats]:
    """Extract, verify and persist obligations for (some) paragraphs of a version."""
    services = ctx.services
    stats = ExtractionStats()
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        version = await session.get(RegulationVersion, version_id)
        assert version is not None
        query = (
            select(Section)
            .where(Section.version_id == version_id, Section.section_type == "PARAGRAPH", Section.text != "")
            .order_by(Section.ordinal)
        )
        if section_ids is not None:
            query = query.where(Section.id.in_(section_ids))
        sections = list((await session.scalars(query)).all())
        regulation = await session.get(Regulation, version.regulation_id)
        assert regulation is not None
    stats.sections = len(sections)
    await _discard_previous_attempt(ctx, version_id, [s.id for s in sections])
    if not sections:
        return [], stats

    extract_prompt, verify_prompt = load_prompt("obligation_extraction"), load_prompt("obligation_verification")
    created: list[uuid.UUID] = []
    per_path_counter: dict[str, int] = defaultdict(int)

    for batch in _batches(sections):
        stats.llm_batches += 1
        by_label = {s.section_path: s for s in batch}
        content = f"Regulation: {regulation.title} ({regulation.regulator})\n\n" + spotlight(
            DocumentBlock(s.section_path, f"{regulation.title} para {s.section_path}", s.text) for s in batch
        )
        outcome = await services.llm.structured(
            ctx=ctx.llm_ctx("obligation_extractor"),
            prompt=extract_prompt,
            user_content=content,
            schema=ObligationExtraction,
            tier=ModelTier.REASONING,
            max_output_tokens=4000,
        )
        candidates = []
        for item in outcome.value.obligations:
            stats.proposed += 1
            section = by_label.get(item.section_id)
            match = verify_quote(item.source_quote, section.text) if section else None
            if section is None or match is None or not match.verified:
                # Quote may belong to a neighbouring paragraph of the batch.
                for other in batch:
                    alt = verify_quote(item.source_quote, other.text)
                    if alt.verified:
                        section, match = other, alt
                        break
            if section is None or match is None or not match.verified:
                stats.rejected_quote += 1
                ctx.decide(
                    "reject_obligation",
                    "source_quote not found verbatim in source paragraph",
                    section=item.section_id,
                    quote=item.source_quote[:120],
                )
                continue
            candidates.append((item, section, match))
        if not candidates:
            continue

        # Independent verification by a second (fast) model.
        verdicts: dict[str, tuple[bool, str]] = {}
        verify_content = "\n\n".join(
            f"Candidate {i}:\n"
            + spotlight([DocumentBlock(section.section_path, "source paragraph", section.text)])
            + f"\nExtracted: modality={item.modality}; subject={item.subject}; "
            f"requirement={item.requirement_text}"
            for i, (item, section, _) in enumerate(candidates)
        )
        try:
            verification = await services.llm.structured(
                ctx=ctx.llm_ctx("obligation_verifier"),
                prompt=verify_prompt,
                user_content=verify_content,
                schema=ObligationVerification,
                tier=ModelTier.FAST,
                max_output_tokens=2500,
            )
            verdicts = {
                r.candidate_id.replace("Candidate", "").strip(): (r.entailed, r.reason)
                for r in verification.value.results
            }
        except LLMError as exc:
            ctx.decide("verifier_unavailable", f"Verification skipped: {exc.code}; confidence lowered")

        async with services.db.tenant_session(ctx.run.bank_id) as session:
            texts: list[tuple[Obligation, str]] = []
            for i, (item, section, match) in enumerate(candidates):
                entailed, verifier_reason = verdicts.get(str(i), (None, "not verified"))
                if entailed is False and item.self_confidence < 0.9:
                    stats.rejected_verifier += 1
                    ctx.decide("reject_obligation", f"verifier: {verifier_reason}", section=section.section_path)
                    continue
                factors = {
                    "quote_match": match.score,
                    "verifier": 1.0 if entailed else (0.5 if entailed is None else 0.0),
                    "self_confidence": item.self_confidence,
                    "has_evidence_spec": 1.0 if item.required_evidence else 0.5,
                }
                confidence = round(
                    0.35 * factors["quote_match"]
                    + 0.30 * factors["verifier"]
                    + 0.25 * factors["self_confidence"]
                    + 0.10 * factors["has_evidence_spec"],
                    3,
                )
                per_path_counter[section.section_path] += 1
                # Lineage (carry_forward) re-keys obligations that evolve from a prior version.
                key = f"{str(regulation.id)[:8]}:{section.section_path}:{per_path_counter[section.section_path]}"
                severity = IMPACT_PROFILE.get(item.obligation_type, IMPACT_PROFILE[ObligationType.OTHER])[0]
                obligation = Obligation(
                    obligation_key=key,
                    regulation_id=regulation.id,
                    version_id=version_id,
                    section_id=section.id,
                    section_path=section.section_path,
                    requirement_text=item.requirement_text,
                    source_quote=section.text[match.start : match.end],
                    char_start=match.start,
                    char_end=match.end,
                    modality=item.modality.value,
                    obligation_type=item.obligation_type.value,
                    subject=item.subject,
                    action=item.action,
                    condition=item.condition,
                    applicability=item.applicability.model_dump(),
                    frequency={
                        "text": item.frequency_text,
                        "period": normalize_period(item.frequency_text),
                        "kind": "PERIODIC" if normalize_period(item.frequency_text) else "EVENT_OR_CONTINUOUS",
                    },
                    deadline_text=item.deadline_text,
                    responsible_function=item.responsible_function,
                    severity_hint=severity,
                    required_evidence=[e.model_dump() for e in item.required_evidence],
                    ambiguity_note=item.ambiguity_note,
                    confidence=confidence,
                    confidence_factors={**factors, "verifier_reason": verifier_reason},
                    review_status="PENDING",
                    status="ACTIVE",
                    effective_from=version.effective_from,
                    extraction_run_id=ctx.run.id,
                    prompt_version=outcome.prompt_version,
                    model=outcome.model,
                )
                session.add(obligation)
                texts.append((obligation, f"{item.requirement_text} {section.text}"))
            vectors = await services.embedder.embed_documents([t for _, t in texts])
            for (obligation, _), vector in zip(texts, vectors, strict=True):
                obligation.embedding = vector
            await session.flush()
            for obligation, _ in texts:
                created.append(obligation.id)
                stats.accepted += 1
                await upsert_edge(
                    session,
                    src=("section", obligation.section_id),
                    rel="HAS_OBLIGATION",
                    dst=("obligation", obligation.id),
                )
                if obligation.confidence < REVIEW_THRESHOLD or obligation.ambiguity_note:
                    stats.sent_to_review += 1
                    session.add(
                        HumanReview(
                            bank_id=ctx.run.bank_id,
                            subject_type="OBLIGATION",
                            subject_id=obligation.id,
                            reason=(
                                "Low extraction confidence"
                                if obligation.confidence < REVIEW_THRESHOLD
                                else f"Ambiguity flagged: {obligation.ambiguity_note}"
                            ),
                            recommendation={
                                "requirement_text": obligation.requirement_text,
                                "quote": obligation.source_quote,
                            },
                            confidence=obligation.confidence,
                            run_id=ctx.run.id,
                        )
                    )
            await audit.record(
                session,
                bank_id=ctx.run.bank_id,
                actor=audit.Actor.agent("obligation_extractor"),
                action="obligations.extracted",
                subject_type="regulation_version",
                subject_id=version_id,
                run_id=ctx.run.id,
                details={"count": len(texts), "sections": [s.section_path for s in batch]},
            )
    return created, stats


async def _discard_previous_attempt(ctx: StepContext, version_id: uuid.UUID, section_ids: list[uuid.UUID]) -> None:
    """Idempotent retries: extraction commits batch by batch, so a failed attempt can
    leave partial results. Before re-extracting, delete what earlier attempts of THIS
    run wrote for these sections (nothing downstream references them yet)."""
    async with ctx.services.db.tenant_session(ctx.run.bank_id) as session:
        stale = list(
            (
                await session.scalars(
                    select(Obligation.id).where(
                        Obligation.version_id == version_id,
                        Obligation.section_id.in_(section_ids),
                        Obligation.extraction_run_id == ctx.run.id,
                    )
                )
            ).all()
        )
        if not stale:
            return
        await session.execute(delete(HumanReview).where(HumanReview.subject_id.in_(stale)))
        await session.execute(
            text("DELETE FROM kg_edges WHERE dst_id = ANY(:ids) OR src_id = ANY(:ids)"), {"ids": stale}
        )
        await session.execute(delete(Obligation).where(Obligation.id.in_(stale)))
    ctx.decide("retry_cleanup", f"Discarded {len(stale)} obligation(s) written by an earlier failed attempt")


# ----------------------------------------------------------------------------------
# Lineage across versions
# ----------------------------------------------------------------------------------


async def carry_forward(
    ctx: StepContext,
    previous_version_id: uuid.UUID,
    new_version_id: uuid.UUID,
    changed_paths: set[str],
    editorial_paths: set[str],
    new_obligation_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """Link obligations of the previous version to the new one.

    - Paragraphs with identical text: obligations are copied (same key, re-verified).
    - Changed paragraphs: newly extracted obligations are matched to old ones by text
      similarity -> MODIFIED (or UNCHANGED when the change was editorial).
    - Old obligations without a successor -> REMOVED; new without predecessor -> ADDED.
    Mappings of MODIFIED obligations are copied as STALE so the mapping agent re-adjudicates.
    """
    services = ctx.services
    summary = {"unchanged": 0, "modified": 0, "added": 0, "removed": 0, "copied_mappings": 0}
    modified_ids: list[uuid.UUID] = []
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        old_obligations = list(
            (
                await session.scalars(
                    select(Obligation).where(
                        Obligation.version_id == previous_version_id, Obligation.status == "ACTIVE"
                    )
                )
            ).all()
        )
        new_sections = {
            s.section_path: s
            for s in (await session.scalars(select(Section).where(Section.version_id == new_version_id))).all()
        }
        new_obligations = (
            list((await session.scalars(select(Obligation).where(Obligation.id.in_(new_obligation_ids)))).all())
            if new_obligation_ids
            else []
        )
        new_version = await session.get(RegulationVersion, new_version_id)
        assert new_version is not None

        pairs: list[tuple[Obligation | None, Obligation | None, str, float | None]] = []
        unmatched_new = {o.id: o for o in new_obligations}
        for prior in old_obligations:
            section = new_sections.get(prior.section_path)
            if prior.section_path not in changed_paths and section is not None:
                match = verify_quote(prior.source_quote, section.text)
                if match.verified:
                    copy = _clone_obligation(prior, new_version, section, match.start, match.end)
                    session.add(copy)
                    await session.flush()
                    pairs.append((prior, copy, "UNCHANGED", 1.0))
                    continue
            candidates = [o for o in unmatched_new.values() if o.section_path == prior.section_path]
            best = max(
                candidates, key=lambda o: text_similarity(o.requirement_text, prior.requirement_text), default=None
            )
            if best is not None:
                similarity = text_similarity(best.requirement_text, prior.requirement_text)
                change = "UNCHANGED" if prior.section_path in editorial_paths else "MODIFIED"
                best.obligation_key = prior.obligation_key
                unmatched_new.pop(best.id)
                pairs.append((prior, best, change, round(similarity, 3)))
            else:
                pairs.append((prior, None, "REMOVED", None))
        pairs.extend((None, o, "ADDED", None) for o in unmatched_new.values())

        for old, new, change, pair_similarity in pairs:
            summary[change.lower()] += 1
            session.add(
                ObligationLineage(
                    prev_obligation_id=old.id if old else None,
                    next_obligation_id=new.id if new else None,
                    change_type=change,
                    similarity=pair_similarity,
                )
            )
            if old is not None:
                old.status = "SUPERSEDED" if new is not None else "REMOVED"
            if old is not None and new is not None:
                await upsert_edge(
                    session,
                    src=("obligation", old.id),
                    rel="EVOLVED_TO",
                    dst=("obligation", new.id),
                    props={"change_type": change},
                )
                mappings = (
                    await session.scalars(
                        select(ControlObligation).where(
                            ControlObligation.obligation_id == old.id, ControlObligation.bank_id == ctx.run.bank_id
                        )
                    )
                ).all()
                for mapping in mappings:
                    session.add(
                        ControlObligation(
                            bank_id=ctx.run.bank_id,
                            obligation_id=new.id,
                            control_id=mapping.control_id,
                            mapping_type=mapping.mapping_type,
                            coverage_score=mapping.coverage_score,
                            confidence=mapping.confidence,
                            retrieval_score=mapping.retrieval_score,
                            reason=mapping.reason,
                            control_quote=mapping.control_quote,
                            evidence={**mapping.evidence, "carried_from": str(mapping.id)},
                            review_status=mapping.review_status if change == "UNCHANGED" else "STALE",
                            proposed_by="LINEAGE",
                            run_id=ctx.run.id,
                        )
                    )
                    summary["copied_mappings"] += 1
                if change == "MODIFIED":
                    modified_ids.append(new.id)
        await session.flush()
        await audit.record(
            session,
            bank_id=ctx.run.bank_id,
            actor=audit.Actor.agent("change_analyst"),
            action="obligations.lineage",
            subject_type="regulation_version",
            subject_id=new_version_id,
            run_id=ctx.run.id,
            details=summary,
        )
        active_ids = [n.id for _, n, _, _ in pairs if n is not None]
    return {
        "summary": summary,
        "modified_ids": [str(i) for i in modified_ids],
        "added_ids": [str(o.id) for o in unmatched_new.values()],
        "active_obligation_ids": [str(i) for i in active_ids],
    }


def _clone_obligation(
    old: Obligation, version: RegulationVersion, section: Section, start: int, end: int
) -> Obligation:
    skip = {"id", "created_at", "updated_at", "version_id", "section_id", "char_start", "char_end", "effective_from"}
    values = {c.key: getattr(old, c.key) for c in Obligation.__table__.columns if c.key not in skip}
    return Obligation(
        **values,
        version_id=version.id,
        section_id=section.id,
        char_start=start,
        char_end=end,
        effective_from=version.effective_from,
    )


# ----------------------------------------------------------------------------------
# Applicability
# ----------------------------------------------------------------------------------


async def assess_applicability(ctx: StepContext, obligation_ids: list[uuid.UUID]) -> dict[str, int]:
    """Deterministic rules against the bank profile; LLM only for undecidable cases."""
    services = ctx.services
    counts = {"APPLICABLE": 0, "NOT_APPLICABLE": 0, "UNCERTAIN": 0, "llm_decided": 0}
    async with services.db.tenant_session(ctx.run.bank_id) as session:
        bank = await session.get(Bank, ctx.run.bank_id)
        assert bank is not None
        profile = bank.profile
        obligations = list((await session.scalars(select(Obligation).where(Obligation.id.in_(obligation_ids)))).all())
    aliases = {a.lower() for a in profile.get("entity_type_aliases", [])} | {
        str(profile.get("entity_type", "")).lower()
    }
    products = {p.lower() for p in profile.get("products", [])}

    decisions: dict[uuid.UUID, tuple[str, str, str, float]] = {}
    undecided: list[Obligation] = []
    for obligation in obligations:
        spec = obligation.applicability or {}
        entity_types = [e.lower() for e in spec.get("entity_types", [])]
        listed_products = [p.lower() for p in spec.get("products", [])]
        if entity_types and not any(any(a in e or e in a for a in aliases if a) for e in entity_types):
            undecided.append(obligation)
            continue
        if listed_products and not any(any(p in lp or lp in p for p in products) for lp in listed_products):
            if "v-cip" in " ".join(listed_products) or "video" in " ".join(listed_products):
                if profile.get("offers_vcip"):
                    decisions[obligation.id] = ("APPLICABLE", "RULE", "Bank offers V-CIP onboarding", 0.9)
                    continue
            undecided.append(obligation)
            continue
        reason = (
            f"Entity type matches bank profile ({profile.get('entity_type')})"
            if entity_types
            else "Obligation applies generally to regulated entities; no restricting condition"
        )
        decisions[obligation.id] = ("APPLICABLE", "RULE", reason, 0.9)

    if undecided and services.llm.available:
        applicability_aliases: AliasMap[Obligation] = AliasMap("O")
        content = f"Bank profile: {json.dumps(profile)}\n\n" + spotlight(
            DocumentBlock(
                (alias := applicability_aliases.add(o, o.id, o.section_path)),
                f"obligation {alias} (para {o.section_path})",
                f"{o.source_quote}\nApplicability conditions: {json.dumps(o.applicability)}",
            )
            for o in undecided
        )
        try:
            batch = await services.llm.structured(
                ctx=ctx.llm_ctx("applicability"),
                prompt=load_prompt("applicability"),
                user_content=content,
                schema=ApplicabilityBatch,
                tier=ModelTier.FAST,
                max_output_tokens=2000,
            )
            for item in batch.value.decisions:
                target = applicability_aliases.resolve(item.obligation_id)
                if target is None:
                    continue
                decisions[target.id] = (item.decision, "LLM", item.rationale, item.confidence)
                counts["llm_decided"] += 1
        except LLMError as exc:
            ctx.decide("applicability_llm_unavailable", exc.code)
    for obligation in undecided:
        decisions.setdefault(obligation.id, ("UNCERTAIN", "RULE", "Profile lacks information to decide", 0.3))

    async with services.db.tenant_session(ctx.run.bank_id) as session:
        for oid, (decision, by, rationale, confidence) in decisions.items():
            counts[decision] += 1
            existing = await session.scalar(
                select(ObligationApplicability).where(
                    ObligationApplicability.obligation_id == oid, ObligationApplicability.bank_id == ctx.run.bank_id
                )
            )
            if existing is not None and existing.decided_by == "HUMAN":
                continue  # never override a human decision
            row = existing or ObligationApplicability(bank_id=ctx.run.bank_id, obligation_id=oid)
            row.decision, row.decided_by, row.rationale, row.confidence = decision, by, rationale, confidence
            row.review_status = "APPROVED" if by == "RULE" and decision == "APPLICABLE" else "PENDING"
            if existing is None:
                session.add(row)
            if by == "LLM" or decision == "UNCERTAIN":
                session.add(
                    HumanReview(
                        bank_id=ctx.run.bank_id,
                        subject_type="APPLICABILITY",
                        subject_id=oid,
                        reason=f"Applicability decided by {by}: {decision}",
                        confidence=confidence,
                        recommendation={"decision": decision, "rationale": rationale},
                        run_id=ctx.run.id,
                    )
                )
    return counts


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
