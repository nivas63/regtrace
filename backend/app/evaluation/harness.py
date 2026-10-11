"""Evaluation harness: measure the system against golden labels (data/golden/*.jsonl).

Suites (all computed from the live database state, i.e. from what the agents actually
produced):
  parsing       paragraph recovery + verbatim-quote recoverability per document
  extraction    obligation precision / recall / F1, type / modality / period accuracy,
                unverified-output rate (LLM outputs caught by the quote gate), calibration (ECE)
  mapping       precision / recall of obligation -> control links
  gaps          recall / precision of seeded gaps
  retrieval     Recall@1/3/5, MRR of hybrid retrieval on golden queries
  change        precision / recall of material-change detection (v1 -> v2), editorial suppression
  cross_reg     conflict recall, cluster recall
  operations    agent success rate, LLM calls, cache hit rate, latency, equivalent cost

Known limitation (reported with the results): the golden set is small (65 obligations)
and was written by the same author as the synthetic documents.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.api.serializers import pairs
from app.container import Services
from app.ingestion.parsers import parse
from app.ingestion.structure import detect_sections
from app.ingestion.validation import validate_upload
from app.models import (
    Chunk,
    ComplianceFinding,
    Control,
    ControlObligation,
    Document,
    EvalRun,
    LLMCall,
    Obligation,
    ObligationClusterMember,
    RegulationVersion,
    RegulatoryChange,
    RegulatoryConflict,
    Section,
    WorkflowRun,
    WorkflowStep,
)
from app.retrieval.search import HybridSearcher, SearchFilters

DATASET_VERSION = "synthetic-v1"
_TOKEN = re.compile(r"[a-z0-9]+")
DOC_FILES = {
    "SYN-KYC-v1": "SYN_KYC_Master_Direction_2024_v1.pdf",
    "SYN-KYC-v2": "SYN_KYC_Master_Direction_2025_v2.pdf",
    "SYN-ITG-v1": "SYN_IT_Governance_Master_Direction_2024.pdf",
    "SYN-DPA-v1": "SYN_Data_Retention_Guidelines_2025.pdf",
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(_TOKEN.findall(a.lower())), set(_TOKEN.findall(b.lower()))
    return len(ta & tb) / max(1, len(ta | tb))


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


class Evaluator:
    def __init__(self, services: Services, data_dir: Path, bank_id: uuid.UUID) -> None:
        self.s = services
        self.data = data_dir
        self.golden = data_dir / "golden"
        self.bank_id = bank_id

    async def _versions_by_doc(self) -> dict[str, uuid.UUID]:
        async with self.s.db.tenant_session(self.bank_id) as session:
            rows = (
                await session.execute(
                    select(Document.filename, RegulationVersion.id).join(
                        RegulationVersion, RegulationVersion.document_id == Document.id
                    )
                )
            ).all()
        by_file = {name: vid for name, vid in rows}
        return {doc: by_file[f] for doc, f in DOC_FILES.items() if f in by_file}

    # -- suites -------------------------------------------------------------------

    def parsing(self) -> dict[str, Any]:
        from scripts import demo_corpus as corpus  # corpus lives with the generator

        per_doc, total_paras, found_paras, total_quotes, found_quotes = {}, 0, 0, 0, 0
        for reg in corpus.REGULATIONS:
            raw = (self.data / "synthetic" / "regulations" / reg.filename).read_bytes()
            doc = parse(validate_upload(reg.filename, raw, max_bytes=50 << 20, max_pdf_pages=500).kind, raw)
            sections = {s.section_path: " ".join(s.text.split()) for s in detect_sections(doc)}
            expected = [p for ch in reg.chapters for p in ch.paras]
            paras = sum(1 for p in expected if p.path in sections)
            quotes = [(p.path, o.quote) for p in expected for o in p.obligations]
            ok_quotes = sum(1 for path, q in quotes if q in sections.get(path, ""))
            per_doc[reg.doc_id] = {"paragraphs": f"{paras}/{len(expected)}", "quotes": f"{ok_quotes}/{len(quotes)}"}
            total_paras += len(expected)
            found_paras += paras
            total_quotes += len(quotes)
            found_quotes += ok_quotes
        return {
            "paragraph_recovery": round(found_paras / total_paras, 3),
            "quote_recoverability": round(found_quotes / total_quotes, 3),
            "per_document": per_doc,
        }

    async def extraction(self) -> dict[str, Any]:
        gold = _jsonl(self.golden / "obligations.jsonl")
        versions = await self._versions_by_doc()
        async with self.s.db.tenant_session(self.bank_id) as session:
            predicted = (
                await session.scalars(
                    select(Obligation).where(
                        Obligation.version_id.in_(list(versions.values())), Obligation.status != "REJECTED"
                    )
                )
            ).all()
            steps = (
                (
                    await session.execute(
                        select(WorkflowStep.output)
                        .join(WorkflowRun, WorkflowRun.id == WorkflowStep.run_id)
                        .where(
                            WorkflowRun.bank_id == self.bank_id,
                            WorkflowStep.step_name == "extract_obligations",
                            WorkflowStep.status == "SUCCEEDED",
                        )
                    )
                )
                .scalars()
                .all()
            )
        doc_of_version = {v: d for d, v in versions.items()}
        by_doc: dict[str, list[Obligation]] = defaultdict(list)
        for o in predicted:
            by_doc[doc_of_version[o.version_id]].append(o)
        tp = fn = 0
        matched: set[uuid.UUID] = set()
        type_ok = modality_ok = period_ok = period_total = 0
        calibration: list[tuple[float, bool]] = []
        evaluated_docs = [d for d in versions]
        for g in (g for g in gold if g["doc_id"] in evaluated_docs):
            candidates = [o for o in by_doc[g["doc_id"]] if o.section_path == g["section_path"] and o.id not in matched]
            best = max(candidates, key=lambda o: _jaccard(o.source_quote, g["quote"]), default=None)
            if best is not None and _jaccard(best.source_quote, g["quote"]) >= 0.5:
                tp += 1
                matched.add(best.id)
                type_ok += best.obligation_type == g["obligation_type"]
                modality_ok += best.modality == g["modality"]
                if g["period"]:
                    period_total += 1
                    period_ok += (best.frequency or {}).get("period") == g["period"]
            else:
                fn += 1
        fp = sum(1 for docs in by_doc.values() for o in docs if o.id not in matched)
        for docs in by_doc.values():
            calibration.extend((o.confidence, o.id in matched) for o in docs)
        proposed = sum(int(s.get("proposed", 0)) for s in steps)
        rejected_quote = sum(int(s.get("rejected_quote", 0)) for s in steps)
        rejected_verifier = sum(int(s.get("rejected_verifier", 0)) for s in steps)
        return {
            **_prf(tp, fp, fn),
            "type_accuracy": round(type_ok / tp, 3) if tp else None,
            "modality_accuracy": round(modality_ok / tp, 3) if tp else None,
            "period_accuracy": round(period_ok / period_total, 3) if period_total else None,
            "llm_obligations_proposed": proposed,
            "unverified_output_rate": round(rejected_quote / proposed, 3) if proposed else None,
            "verifier_rejections": rejected_verifier,
            "expected_calibration_error": _ece(calibration),
            "documents": evaluated_docs,
        }

    async def mapping(self) -> dict[str, Any]:
        gold = _jsonl(self.golden / "mappings.jsonl")
        versions = await self._versions_by_doc()
        async with self.s.db.tenant_session(self.bank_id) as session:
            rows = (
                await session.execute(
                    select(Obligation.version_id, Obligation.section_path, Control.control_key)
                    .join(ControlObligation, ControlObligation.obligation_id == Obligation.id)
                    .join(Control, Control.id == ControlObligation.control_id)
                    .where(
                        ControlObligation.mapping_type != "NONE",
                        ControlObligation.review_status != "REJECTED",
                        Obligation.version_id.in_(list(versions.values())),
                    )
                )
            ).all()
        doc_of_version = {v: d for d, v in versions.items()}
        predicted = {(doc_of_version[v], p, k) for v, p, k in rows}
        expected = {
            (g["doc_id"], g["section_path"], k) for g in gold if g["doc_id"] in versions for k in g["control_keys"]
        }
        tp = len(predicted & expected)
        # Precision only over obligations that have a golden mapping entry (others are unlabeled).
        labeled = {(g["doc_id"], g["section_path"]) for g in gold}
        fp = len({x for x in predicted - expected if (x[0], x[1]) in labeled})
        return _prf(tp, fp, len(expected - predicted))

    async def gaps(self) -> dict[str, Any]:
        gold = _jsonl(self.golden / "gaps.jsonl")
        versions = await self._versions_by_doc()
        async with self.s.db.tenant_session(self.bank_id) as session:
            rows = (
                await session.execute(
                    select(Obligation.version_id, Obligation.section_path, ComplianceFinding.gap_type)
                    .join(ComplianceFinding, ComplianceFinding.obligation_id == Obligation.id)
                    .where(
                        ComplianceFinding.bank_id == self.bank_id, ComplianceFinding.status.in_(("OPEN", "SUPERSEDED"))
                    )
                )
            ).all()
        doc_of_version = {v: d for d, v in versions.items()}
        found: dict[tuple[str, str], set[str]] = defaultdict(set)
        for v, path, gap in rows:
            doc = doc_of_version.get(v)
            if doc:
                found[(doc.replace("-v2", "-v1"), path)].add(gap)
        detected = [g for g in gold if found.get((g["doc_id"], g["section_path"]), set()) & set(g["gap_types"])]
        seeded_keys = {(g["doc_id"], g["section_path"]) for g in gold}
        flagged_keys = set(found)
        return {
            "seeded_gaps": len(gold),
            "detected": len(detected),
            "recall": round(len(detected) / len(gold), 3) if gold else None,
            "obligations_flagged": len(flagged_keys),
            "flagged_outside_seed_set": len(flagged_keys - seeded_keys),
            "missed": [f"{g['doc_id']} {g['section_path']}" for g in gold if g not in detected],
        }

    async def retrieval(self) -> dict[str, Any]:
        gold = _jsonl(self.golden / "retrieval.jsonl")
        searcher = HybridSearcher(self.s.embedder, self.s.reranker)
        hits_at = {1: 0, 3: 0, 5: 0}
        reciprocal = 0.0
        async with self.s.db.tenant_session(self.bank_id) as session:
            files = pairs((await session.execute(select(Document.id, Document.filename))).all())
            for item in gold:
                relevant = {(DOC_FILES[r["doc_id"]], r["section_path"]) for r in item["relevant"]}
                hits = await searcher.search(
                    session, item["query"], SearchFilters(doc_classes=["REGULATION"], current_only=False), top_k=5
                )
                ranked = [(files.get(h.document_id), h.section_path) for h in hits]
                rank = next((i + 1 for i, key in enumerate(ranked) if key in relevant), None)
                if rank:
                    reciprocal += 1 / rank
                    for k in hits_at:
                        hits_at[k] += rank <= k
        n = len(gold)
        return {
            **{f"recall@{k}": round(v / n, 3) for k, v in hits_at.items()},
            "mrr": round(reciprocal / n, 3),
            "queries": n,
            "embedding_model": self.s.embedder.name,
            "reranker": self.s.reranker.name,
        }

    async def change(self) -> dict[str, Any]:
        gold = _jsonl(self.golden / "changes.jsonl")
        async with self.s.db.tenant_session(self.bank_id) as session:
            rows = (await session.scalars(select(RegulatoryChange))).all()
        if not rows:
            return {"status": "not run (upload KYC v2 to evaluate change intelligence)"}
        detected = {(c.section_path, c.is_material) for c in rows}
        expected_material = {g["section_path"] for g in gold if g["is_material"]}
        predicted_material = {p for p, m in detected if m}
        editorial_expected = {g["section_path"] for g in gold if not g["is_material"]}
        tp = len(predicted_material & expected_material)
        return {
            **_prf(tp, len(predicted_material - expected_material), len(expected_material - predicted_material)),
            "editorial_suppressed": sorted(editorial_expected - predicted_material),
            "changes_recorded": len(rows),
        }

    async def cross_regulation(self) -> dict[str, Any]:
        gold_conflicts = _jsonl(self.golden / "conflicts.jsonl")
        gold_clusters = _jsonl(self.golden / "clusters.jsonl")
        versions = await self._versions_by_doc()
        async with self.s.db.tenant_session(self.bank_id) as session:
            obligations = {o.id: o for o in (await session.scalars(select(Obligation))).all()}
            conflicts = (await session.scalars(select(RegulatoryConflict))).all()
            members = (await session.scalars(select(ObligationClusterMember))).all()
        doc_of_version = {v: d.replace("-v2", "-v1") for d, v in versions.items()}

        def key(oid: uuid.UUID) -> tuple[str, str] | None:
            o = obligations.get(oid)
            return (doc_of_version.get(o.version_id, "?"), o.section_path) if o else None

        predicted_pairs = {frozenset({key(c.obligation_a_id), key(c.obligation_b_id)}) for c in conflicts}
        expected_pairs = [
            frozenset({(g["a"]["doc_id"], g["a"]["section_path"]), (g["b"]["doc_id"], g["b"]["section_path"])})
            for g in gold_conflicts
        ]
        found = sum(1 for p in expected_pairs if p in predicted_pairs)
        clusters: dict[uuid.UUID, set[tuple[str, str] | None]] = defaultdict(set)
        for m in members:
            clusters[m.cluster_id].add(key(m.obligation_id))
        cluster_hits = sum(
            1
            for g in gold_clusters
            if any({(x["doc_id"], x["section_path"]) for x in g["members"]} <= c for c in clusters.values())
        )
        return {
            "conflict_recall": round(found / len(expected_pairs), 3) if expected_pairs else None,
            "potential_conflicts_flagged": len(conflicts),
            "conflict_precision_vs_seeded": round(found / len(predicted_pairs), 3) if predicted_pairs else None,
            "cluster_recall": round(cluster_hits / len(gold_clusters), 3) if gold_clusters else None,
            "clusters": len(clusters),
        }

    async def operations(self) -> dict[str, Any]:
        async with self.s.db.system_session() as session:
            runs = pairs(
                (
                    await session.execute(
                        select(WorkflowRun.status, func.count())
                        .where(WorkflowRun.bank_id == self.bank_id)
                        .group_by(WorkflowRun.status)
                    )
                ).all()
            )
            llm = (
                await session.execute(
                    select(
                        func.count(),
                        func.count().filter(LLMCall.cache_hit),
                        func.avg(LLMCall.latency_ms),
                        func.sum(LLMCall.equivalent_cost_usd),
                        func.count().filter(LLMCall.outcome != "SUCCESS"),
                    )
                )
            ).one()
            durations = (
                await session.execute(
                    select(
                        WorkflowRun.workflow_type,
                        func.avg(func.extract("epoch", WorkflowRun.finished_at - WorkflowRun.started_at)),
                    )
                    .where(WorkflowRun.bank_id == self.bank_id, WorkflowRun.status == "SUCCEEDED")
                    .group_by(WorkflowRun.workflow_type)
                )
            ).all()
            chunks = await session.scalar(select(func.count()).select_from(Chunk))
            sections = await session.scalar(select(func.count()).select_from(Section))
        total = sum(runs.values())
        return {
            "agent_success_rate": round(runs.get("SUCCEEDED", 0) / total, 3) if total else None,
            "runs": runs,
            "llm_calls": llm[0],
            "cache_hit_rate": round(llm[1] / llm[0], 3) if llm[0] else None,
            "avg_llm_latency_ms": round(float(llm[2] or 0), 1),
            "llm_failures": llm[4],
            "equivalent_cost_usd": round(float(llm[3] or 0), 4),
            "actual_cost_usd": 0.0,
            "avg_run_seconds": {w: round(float(s or 0), 1) for w, s in durations},
            "indexed_sections": sections,
            "indexed_chunks": chunks,
        }

    async def run_all(self, notes: str | None = None) -> dict[str, Any]:
        results: dict[str, Any] = {
            "parsing": self.parsing(),
            "extraction": await self.extraction(),
            "mapping": await self.mapping(),
            "gaps": await self.gaps(),
            "retrieval": await self.retrieval(),
            "change": await self.change(),
            "cross_regulation": await self.cross_regulation(),
            "operations": await self.operations(),
        }
        results["limitations"] = [
            "Golden set is small (65 obligations, 35 mappings, 11 seeded gaps, 25 queries).",
            "Golden labels were written by the author of the synthetic documents (possible bias).",
            "Documents are clean digital PDFs; scanned/OCR reliability is not measured.",
        ]
        async with self.s.db.system_session() as session:
            for suite, metrics in results.items():
                if suite != "limitations":
                    session.add(
                        EvalRun(
                            suite=suite,
                            dataset_version=DATASET_VERSION,
                            metrics=metrics,
                            details={"llm_providers": sorted(self.s.llm_stack.router.providers)},
                            notes=notes,
                        )
                    )
        return results


def _ece(pairs: list[tuple[float, bool]], bins: int = 5) -> float | None:
    if not pairs:
        return None
    total, error = len(pairs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(c, ok) for c, ok in pairs if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if bucket:
            accuracy = sum(ok for _, ok in bucket) / len(bucket)
            confidence = sum(c for c, _ in bucket) / len(bucket)
            error += len(bucket) / total * abs(accuracy - confidence)
    return round(error, 3)
