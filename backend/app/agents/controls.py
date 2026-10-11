"""Control agents: regulation-to-control mapping, evidence assessment, and control
understanding from policy documents.

Mapping is deliberately NOT "ask the LLM": candidates come from vector similarity +
lexical overlap + deterministic rule boosts; the LLM adjudicates only the top
candidates, its supporting quote is verified against the control text, and the
final confidence blends retrieval, rules, the model's judgement and verification.
Human decisions (APPROVED / REJECTED) are never overwritten.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, select, text

from app.agents.schemas import EvidenceAssessmentResult, MappingBatch, PolicyControls
from app.domain.frequency import is_at_least_as_frequent, normalize_period
from app.domain.types import MappingType
from app.domain.verification import verify_quote
from app.knowledge_graph.graph import upsert_edge
from app.llm.errors import LLMError
from app.llm.prompts import AliasMap, DocumentBlock, load_prompt, spotlight
from app.llm.types import ModelTier
from app.models import (
    Chunk,
    Control,
    ControlObligation,
    Document,
    Evidence,
    EvidenceAssessment,
    HumanReview,
    Obligation,
    ObligationApplicability,
    Section,
)
from app.services import audit
from app.workflows.engine import StepContext

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "or",
        "for",
        "in",
        "on",
        "by",
        "at",
        "be",
        "is",
        "are",
        "shall",
        "all",
        "any",
        "its",
        "with",
        "from",
        "as",
        "that",
        "this",
    ]
)
CANDIDATES_PER_OBLIGATION = 4
MIN_PRIOR = 0.25
OBLIGATIONS_PER_CALL = 3
MAPPING_REVIEW_THRESHOLD = 0.75
POLICY_SECTIONS_PER_CALL = 6
_TYPE_KEYWORDS = {
    "CUSTOMER_DUE_DILIGENCE": ("kyc", "cdd", "onboarding", "identity", "beneficial", "updation"),
    "RISK_ASSESSMENT": ("risk", "categorisation", "scoring"),
    "RECORD_RETENTION": ("retention", "archive", "records", "retained", "logs"),
    "REPORTING": ("report", "fiu", "str", "ctr", "submission", "communicat"),
    "GOVERNANCE": ("board", "committee", "policy", "officer", "approval"),
    "MONITORING": ("monitor", "alert", "surveillance"),
    "TESTING_AUDIT": ("audit", "test", "assessment", "vapt"),
    "TRAINING": ("training", "e-learning", "awareness"),
    "TECHNICAL_CONTROL": ("access", "authentication", "patch", "mfa", "privileged"),
    "BUSINESS_CONTINUITY": ("backup", "disaster", "recovery", "drill", "restor"),
    "INCIDENT_MANAGEMENT": ("incident", "cyber", "breach", "soc"),
    "DATA_PROTECTION": ("personal data", "privacy", "erase", "protection officer"),
}


def _tokens(value: str) -> set[str]:
    return {t for t in _TOKEN.findall(value.lower()) if t not in _STOP and len(t) > 2}


def rule_score(obligation: Obligation, control: Control) -> tuple[float, list[str]]:
    """Deterministic evidence for a candidate pair (0..1) with explanations."""
    score, reasons = 0.0, []
    keywords = _TYPE_KEYWORDS.get(obligation.obligation_type, ())
    haystack = f"{control.name} {control.description}".lower()
    if any(k in haystack for k in keywords):
        score += 0.5
        reasons.append(f"control mentions {obligation.obligation_type.lower()} keywords")
    period = (obligation.frequency or {}).get("period")
    adequate = is_at_least_as_frequent(control.frequency, period)
    if adequate is True:
        score += 0.3
        reasons.append(f"control frequency {control.frequency} meets required {period}")
    elif adequate is False:
        reasons.append(f"control frequency {control.frequency} weaker than required {period}")
    function = (obligation.responsible_function or "").lower()
    if function and control.business_unit and control.business_unit.lower() in function:
        score += 0.2
        reasons.append("business unit matches responsible function")
    return min(1.0, score), reasons


async def map_obligations(ctx: StepContext, obligation_ids: list[uuid.UUID]) -> dict[str, Any]:
    services = ctx.services
    bank_id = ctx.run.bank_id
    stats: dict[str, Any] = {
        "obligations": 0,
        "llm_calls": 0,
        "proposed": 0,
        "full": 0,
        "partial": 0,
        "none": 0,
        "unmapped": 0,
        "quote_verified": 0,
        "kept_human": 0,
        "to_review": 0,
    }
    async with services.db.tenant_session(bank_id) as session:
        applicable = set(
            (
                await session.scalars(
                    select(ObligationApplicability.obligation_id).where(
                        ObligationApplicability.bank_id == bank_id, ObligationApplicability.decision != "NOT_APPLICABLE"
                    )
                )
            ).all()
        )
        obligations = [
            o
            for o in (
                await session.scalars(
                    select(Obligation).where(Obligation.id.in_(obligation_ids), Obligation.status == "ACTIVE")
                )
            ).all()
            if o.id in applicable
        ]
        controls = {
            c.id: c
            for c in (
                await session.scalars(select(Control).where(Control.bank_id == bank_id, Control.status == "ACTIVE"))
            ).all()
        }
        candidates: dict[uuid.UUID, list[tuple[Control, float, float, list[str]]]] = {}
        for obligation in obligations:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM controls "
                            "WHERE bank_id = :b AND status = 'ACTIVE' AND embedding IS NOT NULL "
                            "ORDER BY embedding <=> CAST(:v AS vector) LIMIT 8"
                        ),
                        {
                            "v": str(list(obligation.embedding)) if obligation.embedding is not None else "[0]",
                            "b": bank_id,
                        },
                    )
                ).all()
                if obligation.embedding is not None
                else []
            )
            scored = []
            obligation_tokens = _tokens(f"{obligation.requirement_text} {obligation.source_quote}")
            for control_id, similarity in rows:
                control = controls[control_id]
                control_tokens = _tokens(f"{control.name} {control.description}")
                lexical = len(obligation_tokens & control_tokens) / max(1, len(obligation_tokens))
                rules, reasons = rule_score(obligation, control)
                prior = 0.55 * max(0.0, float(similarity)) + 0.25 * lexical + 0.20 * rules
                scored.append((control, round(prior, 3), round(float(similarity), 3), reasons))
            scored.sort(key=lambda s: s[1], reverse=True)
            candidates[obligation.id] = [s for s in scored if s[1] >= MIN_PRIOR][:CANDIDATES_PER_OBLIGATION]
        existing = {
            (m.obligation_id, m.control_id): m
            for m in (
                await session.scalars(
                    select(ControlObligation).where(
                        ControlObligation.bank_id == bank_id,
                        ControlObligation.obligation_id.in_([o.id for o in obligations]),
                    )
                )
            ).all()
        }
    stats["obligations"] = len(obligations)

    prompt = load_prompt("control_mapping")
    judgements: dict[tuple[uuid.UUID, str], Any] = {}
    todo = [o for o in obligations if candidates.get(o.id)]
    for start in range(0, len(todo), OBLIGATIONS_PER_CALL):
        batch = todo[start : start + OBLIGATIONS_PER_CALL]
        blocks = []
        aliases: AliasMap[Obligation] = AliasMap("O")
        for obligation in batch:
            listing = "\n".join(
                f"- {c.control_key}: {c.name}. {c.description} (frequency: {c.frequency or 'n/a'}; "
                f"owner: {c.owner or 'n/a'})"
                for c, *_ in candidates[obligation.id]
            )
            alias = aliases.add(obligation, obligation.id, obligation.section_path)
            blocks.append(
                DocumentBlock(
                    alias,
                    f"obligation {alias} (para {obligation.section_path})",
                    f"OBLIGATION: {obligation.requirement_text}\nSOURCE QUOTE: {obligation.source_quote}\n"
                    f"REQUIRED FREQUENCY: {(obligation.frequency or {}).get('text') or 'n/a'}\n"
                    f"DEADLINE: {obligation.deadline_text or 'n/a'}\nCANDIDATE CONTROLS:\n{listing}",
                )
            )
        try:
            outcome = await services.llm.structured(
                ctx=ctx.llm_ctx("control_mapper"),
                prompt=prompt,
                user_content=spotlight(blocks),
                schema=MappingBatch,
                tier=ModelTier.REASONING,
                max_output_tokens=3500,
                redact=True,
            )
            stats["llm_calls"] += 1
            unresolved = 0
            for mapping in outcome.value.mappings:
                target = aliases.resolve(mapping.obligation_id)
                if target is None:
                    unresolved += 1
                    continue
                for item in mapping.judgements:
                    judgements[(target.id, item.control_key.strip().upper())] = item
            if unresolved:
                ctx.decide("mapping_ids_unresolved", f"{unresolved} judgement group(s) referenced unknown obligations")
        except LLMError as exc:
            ctx.decide(
                "mapping_llm_unavailable",
                f"{exc.code}: falling back to retrieval-only proposals (marked low confidence, sent to review)",
                batch=[o.section_path for o in batch],
            )

    async with services.db.tenant_session(bank_id) as session:
        # Re-load existing mappings in THIS session: rows loaded by the earlier read
        # session are detached, and changes to them would be silently discarded.
        existing = {
            (m.obligation_id, m.control_id): m
            for m in (
                await session.scalars(
                    select(ControlObligation).where(
                        ControlObligation.bank_id == bank_id,
                        ControlObligation.obligation_id.in_([o.id for o in obligations]),
                    )
                )
            ).all()
        }
        for obligation in obligations:
            proposals = 0
            for control, prior, similarity, reasons in candidates.get(obligation.id, []):
                judgement = judgements.get((obligation.id, control.control_key.upper()))
                current = existing.get((obligation.id, control.id))
                if current is not None and current.review_status in ("APPROVED", "REJECTED", "MODIFIED"):
                    stats["kept_human"] += 1
                    continue
                if judgement is None:
                    relation, coverage, llm_conf, quote, reason = (
                        MappingType.PARTIAL.value,
                        round(prior * 0.6, 2),
                        0.3,
                        None,
                        "Retrieval-only proposal (no model adjudication): " + "; ".join(reasons),
                    )
                else:
                    relation, coverage, llm_conf = judgement.relation, judgement.coverage, judgement.confidence
                    quote, reason = judgement.control_quote, judgement.reason
                verified = bool(quote) and verify_quote(quote or "", control.description).verified
                stats["quote_verified"] += verified
                confidence = round(0.5 * llm_conf + 0.3 * prior + 0.2 * (1.0 if verified else 0.0), 3)
                if relation == MappingType.NONE:
                    stats["none"] += 1
                    if current is not None:
                        current.mapping_type, current.coverage_score, current.review_status = "NONE", 0.0, "PROPOSED"
                        current.reason = reason
                    continue
                stats["full" if relation == "FULL" else "partial"] += 1
                row = current or ControlObligation(bank_id=bank_id, obligation_id=obligation.id, control_id=control.id)
                row.mapping_type, row.coverage_score, row.confidence = relation, coverage, confidence
                row.retrieval_score, row.reason, row.control_quote = similarity, reason, quote if verified else None
                row.evidence = {
                    "prior": prior,
                    "rule_reasons": reasons,
                    "quote_verified": verified,
                    "llm_confidence": llm_conf,
                }
                row.review_status, row.proposed_by, row.run_id = "PROPOSED", "AGENT", ctx.run.id
                if current is None:
                    session.add(row)
                await session.flush()
                proposals += 1
                stats["proposed"] += 1
                await upsert_edge(
                    session,
                    src=("obligation", obligation.id),
                    rel="SATISFIED_BY",
                    dst=("control", control.id),
                    bank_id=bank_id,
                    props={"mapping_type": relation, "coverage": coverage, "status": "PROPOSED"},
                )
                if confidence < MAPPING_REVIEW_THRESHOLD:
                    stats["to_review"] += 1
                    session.add(
                        HumanReview(
                            bank_id=bank_id,
                            subject_type="MAPPING",
                            subject_id=row.id,
                            reason=f"Low-confidence mapping {obligation.section_path} -> {control.control_key}",
                            recommendation={"relation": relation, "coverage": coverage, "reason": reason},
                            confidence=confidence,
                            run_id=ctx.run.id,
                        )
                    )
            if proposals == 0:
                stats["unmapped"] += 1
        await audit.record(
            session,
            bank_id=bank_id,
            actor=audit.Actor.agent("control_mapper"),
            action="mappings.proposed",
            run_id=ctx.run.id,
            details={k: v for k, v in stats.items() if isinstance(v, int)},
        )
    return stats


# ----------------------------------------------------------------------------------
# Evidence assessment
# ----------------------------------------------------------------------------------


async def assess_evidence(ctx: StepContext, control_ids: list[uuid.UUID]) -> dict[str, Any]:
    services = ctx.services
    bank_id = ctx.run.bank_id
    stats = {
        "controls": 0,
        "llm_calls": 0,
        "criteria": 0,
        "met": 0,
        "not_met": 0,
        "insufficient": 0,
        "quote_rejected": 0,
        "no_artefacts": 0,
    }
    prompt = load_prompt("evidence_assessment")
    for control_id in control_ids:
        async with services.db.tenant_session(bank_id) as session:
            control = await session.get(Control, control_id)
            if control is None:
                continue
            mapped = (
                (
                    await session.execute(
                        select(Obligation)
                        .join(ControlObligation, ControlObligation.obligation_id == Obligation.id)
                        .where(
                            ControlObligation.control_id == control_id,
                            ControlObligation.mapping_type != "NONE",
                            ControlObligation.review_status != "REJECTED",
                            Obligation.status == "ACTIVE",
                        )
                    )
                )
                .scalars()
                .all()
            )
            evidence_rows = (
                await session.scalars(
                    select(Evidence).where(Evidence.control_id == control_id, Evidence.document_id.is_not(None))
                )
            ).all()
            excerpts: dict[uuid.UUID, str] = {}
            for ev in evidence_rows:
                chunks = (
                    await session.scalars(
                        select(Chunk.text)
                        .where(Chunk.document_id == ev.document_id, Chunk.quarantined.is_(False))
                        .order_by(Chunk.ordinal)
                        .limit(8)
                    )
                ).all()
                excerpts[ev.id] = "\n".join(chunks)[:3500]
        if not mapped:
            continue
        stats["controls"] += 1
        criteria: dict[str, tuple[str, uuid.UUID]] = {}
        for obligation in mapped:
            items = obligation.required_evidence or [{"description": f"Evidence that: {obligation.requirement_text}"}]
            for item in items[:3]:
                criterion = item.get("description", "")[:300]
                if criterion and criterion.lower() not in {c.lower() for c, _ in criteria.values()}:
                    criteria[f"K{len(criteria) + 1}"] = (criterion, obligation.id)
        criteria = dict(list(criteria.items())[:8])
        stats["criteria"] += len(criteria)

        verdicts: dict[str, Any] = {}
        evidence_aliases: AliasMap[uuid.UUID] = AliasMap("E")
        if not excerpts:
            stats["no_artefacts"] += 1
            ctx.decide(
                "no_evidence_artefacts",
                f"{control.control_key}: no evidence artefact on file; "
                "all criteria INSUFFICIENT without calling the model",
                control=control.control_key,
            )
        else:
            content = (
                f"CONTROL {control.control_key}: {control.name}. {control.description}\n\nCRITERIA:\n"
                + "\n".join(f"{cid}: {crit}" for cid, (crit, _) in criteria.items())
                + "\n\nEVIDENCE EXCERPTS:\n"
                + spotlight(
                    DocumentBlock(evidence_aliases.add(ev_id, ev_id), "evidence artefact", body)
                    for ev_id, body in excerpts.items()
                )
            )
            try:
                outcome = await services.llm.structured(
                    ctx=ctx.llm_ctx("evidence_assessor"),
                    prompt=prompt,
                    user_content=content,
                    schema=EvidenceAssessmentResult,
                    tier=ModelTier.REASONING,
                    max_output_tokens=2500,
                    redact=True,
                )
                stats["llm_calls"] += 1
                verdicts = {v.criterion_id.strip().upper(): v for v in outcome.value.verdicts}
            except LLMError as exc:
                ctx.decide(
                    "evidence_llm_unavailable", f"{exc.code}: criteria marked INSUFFICIENT", control=control.control_key
                )

        async with services.db.tenant_session(bank_id) as session:
            # Idempotent retries: replace this run's earlier judgements for the control.
            await session.execute(
                delete(EvidenceAssessment).where(
                    EvidenceAssessment.run_id == ctx.run.id, EvidenceAssessment.control_id == control_id
                )
            )
            for cid, (criterion, obligation_id) in criteria.items():
                v = verdicts.get(cid)
                verdict, quote, evidence_id, rationale, confidence = (
                    "INSUFFICIENT",
                    None,
                    None,
                    "No supporting excerpt",
                    0.5,
                )
                if v is not None:
                    verdict, rationale, confidence = v.verdict, v.rationale, v.confidence
                    evidence_id = evidence_aliases.resolve(v.evidence_id) if v.evidence_id else None
                    quote = v.quote
                    if evidence_id is None and quote:
                        # The verified quote itself identifies its artefact (deterministic).
                        evidence_id = next(
                            (e for e, body in excerpts.items() if verify_quote(quote, body).verified), None
                        )
                verified = False
                if verdict in ("MET", "NOT_MET"):
                    source = excerpts.get(evidence_id) if evidence_id else None
                    verified = bool(quote and source and verify_quote(quote, source).verified)
                    if not verified:
                        stats["quote_rejected"] += 1
                        ctx.decide(
                            "evidence_quote_rejected",
                            f"{control.control_key} {cid}: quote not found in evidence; downgraded to INSUFFICIENT",
                        )
                        verdict, rationale = (
                            "INSUFFICIENT",
                            f"Model claimed {v.verdict if v else '?'} without a verifiable quote",
                        )
                stats[verdict.lower()] += 1
                session.add(
                    EvidenceAssessment(
                        bank_id=bank_id,
                        run_id=ctx.run.id,
                        obligation_id=obligation_id,
                        control_id=control_id,
                        evidence_id=evidence_id if verified else None,
                        criterion=criterion,
                        verdict=verdict,
                        quote=quote if verified else None,
                        quote_verified=verified,
                        rationale=rationale,
                        confidence=confidence,
                    )
                )
    return stats


# ----------------------------------------------------------------------------------
# Controls described in policy documents (control-framework understanding)
# ----------------------------------------------------------------------------------


async def extract_policy_controls(ctx: StepContext, document_id: uuid.UUID) -> dict[str, Any]:
    services = ctx.services
    bank_id = ctx.run.bank_id
    async with services.db.tenant_session(bank_id) as session:
        document = await session.get(Document, document_id)
        assert document is not None
        sections = list(
            (
                await session.scalars(
                    select(Section)
                    .where(Section.document_id == document_id, Section.section_type == "PARAGRAPH", Section.text != "")
                    .order_by(Section.ordinal)
                )
            ).all()
        )
        controls = {
            c.control_key: c
            for c in (
                await session.scalars(select(Control).where(Control.bank_id == bank_id, Control.status == "ACTIVE"))
            ).all()
        }
    if not sections:
        return {"controls_found": 0}
    by_id = {s.section_path: s for s in sections}
    extracted = []
    # Small batches keep each response well inside free-tier output / token-per-minute limits.
    for start in range(0, len(sections), POLICY_SECTIONS_PER_CALL):
        batch = sections[start : start + POLICY_SECTIONS_PER_CALL]
        outcome = await services.llm.structured(
            ctx=ctx.llm_ctx("control_understanding"),
            prompt=load_prompt("policy_control_extraction"),
            user_content=spotlight(DocumentBlock(s.section_path, document.title or "policy", s.text) for s in batch),
            schema=PolicyControls,
            tier=ModelTier.REASONING,
            max_output_tokens=3000,
            redact=True,
        )
        extracted.extend(outcome.value.controls)
    stats: defaultdict[str, int] = defaultdict(int)
    async with services.db.tenant_session(bank_id) as session:
        for item in extracted:
            stats["controls_found"] += 1
            section = by_id.get(item.section_id)
            if section is None or not verify_quote(item.source_quote, section.text).verified:
                stats["rejected_quote"] += 1
                continue
            control = controls.get(item.control_ref or "")
            if control is not None:
                stats["reconciled"] += 1
                db_control = await session.get(Control, control.id)
                assert db_control is not None
                db_control.procedure_ref = db_control.procedure_ref or f"{document.title} s.{section.section_path}"
                db_control.attributes = {
                    **db_control.attributes,
                    "policy_section_id": str(section.id),
                    "policy_description": item.description,
                    "policy_frequency": normalize_period(item.frequency_text),
                }
                policy_period = normalize_period(item.frequency_text)
                if policy_period and db_control.frequency and policy_period != db_control.frequency:
                    stats["frequency_mismatch"] += 1
                    session.add(
                        HumanReview(
                            bank_id=bank_id,
                            subject_type="CONTROL",
                            subject_id=db_control.id,
                            reason=f"{db_control.control_key}: policy states {policy_period} but control matrix "
                            f"says {db_control.frequency}",
                            recommendation={"policy_quote": item.source_quote},
                            run_id=ctx.run.id,
                        )
                    )
            else:
                stats["policy_only"] += 1
                session.add(
                    HumanReview(
                        bank_id=bank_id,
                        subject_type="CONTROL",
                        subject_id=section.id,
                        reason=f"Control described in policy but absent from the control matrix: {item.name}",
                        recommendation=item.model_dump(),
                        run_id=ctx.run.id,
                    )
                )
    return dict(stats)


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None
