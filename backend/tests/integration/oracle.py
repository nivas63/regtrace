"""TEST-ONLY scripted LLM provider.

It answers each prompt from the synthetic corpus's golden labels so the full
pipeline (DB writes, verification gates, workflow control flow, engines) can be
exercised without network access. It is never registered by the application.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.errors import LLMTransientError
from app.llm.types import FinishReason, LLMRequest, ModelTier, ProviderResult, TokenUsage
from scripts import demo_corpus as corpus

_DOC = re.compile(r'<document id="([^"]+)" source="([^"]*)">\n(.*?)\n</document>', re.S)
_ALL_GOLD = [
    (reg.doc_id, para.path, o)
    for reg in corpus.REGULATIONS
    for ch in reg.chapters
    for para in ch.paras
    for o in para.obligations
]


def _docs(content: str) -> list[tuple[str, str, str]]:
    return _DOC.findall(content)


class OracleProvider:
    name = "oracle"

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def model_for(self, tier: ModelTier) -> str:
        return f"oracle-{tier.value}"

    async def list_models(self) -> list[str]:
        return ["oracle-reasoning", "oracle-fast"]

    async def generate(self, request: LLMRequest, model: str) -> ProviderResult:
        self.calls[request.purpose] = self.calls.get(request.purpose, 0) + 1
        # Fail exactly once, mid-extraction (after the first batch has committed), so the
        # pipeline test also proves that step retries are idempotent.
        if request.purpose == "obligation_extraction" and self.calls[request.purpose] == 2:
            raise LLMTransientError("scripted mid-step outage", provider=self.name)
        content = request.messages[-1].content
        handler = getattr(self, f"_{request.purpose}", None)
        payload: dict[str, Any] = handler(content) if handler else {}
        return ProviderResult(
            text=json.dumps(payload),
            model=model,
            usage=TokenUsage(input_tokens=len(content) // 4, output_tokens=200),
            finish_reason=FinishReason.STOP,
        )

    # -- per-prompt answers -------------------------------------------------------

    def _obligation_extraction(self, content: str) -> dict[str, Any]:
        out = []
        for section_id, _source, text in _docs(content):
            flat = " ".join(text.split())
            for _doc, path, gold in _ALL_GOLD:
                if path == section_id and gold.quote in flat:
                    out.append(
                        {
                            "section_id": section_id,
                            "requirement_text": gold.quote,
                            "source_quote": gold.quote,
                            "modality": gold.modality,
                            "obligation_type": gold.obligation_type,
                            "subject": "Regulated Entity",
                            "action": gold.quote[:120],
                            "frequency_text": _period_text(gold.period),
                            "deadline_text": None,
                            "applicability": {},
                            "responsible_function": "Compliance",
                            "required_evidence": [
                                {"description": f"Evidence that {gold.quote[:80]}", "evidence_type": "DOCUMENT"}
                            ],
                            "self_confidence": 0.9,
                        }
                    )
                    break
        return {"obligations": out}

    def _obligation_verification(self, content: str) -> dict[str, Any]:
        ids = re.findall(r"Candidate (\d+):", content)
        return {"results": [{"candidate_id": i, "entailed": True, "reason": "stated in text"} for i in ids]}

    def _control_mapping(self, content: str) -> dict[str, Any]:
        mappings = []
        for _alias, source, text in _docs(content):
            para = re.search(r"para ([\d.]+)", source)
            path = para.group(1) if para else source
            expected = {k for (d, p), keys in corpus.GOLD_MAPPINGS.items() if p == path for k in keys}
            judgements = []
            for key, desc in re.findall(r"^- (C-[A-Z]+-\d+): [^.]+\. (.+?) \(frequency", text, re.M):
                full = key in expected
                judgements.append(
                    {
                        "control_key": key,
                        "relation": "FULL" if full else "NONE",
                        "coverage": 0.9 if full else 0.0,
                        "confidence": 0.9 if full else 0.8,
                        "control_quote": desc[:60] if full else None,
                        "reason": "matches obligation" if full else "different activity",
                    }
                )
            # Mimic models that echo the human-readable label instead of the id (seen with
            # Groq): resolution must still map it back to the right obligation.
            mappings.append({"obligation_id": f"obligation_{path}", "judgements": judgements})
        return {"mappings": mappings}

    def _evidence_assessment(self, content: str) -> dict[str, Any]:
        docs = _docs(content)
        criteria = re.findall(r"^(K\d+): ", content, re.M)
        verdicts = []
        for cid in criteria:
            if docs:
                ev_id, _s, text = docs[0]
                sentence = text.split(".")[0].strip()
                verdicts.append(
                    {
                        "criterion_id": cid,
                        "verdict": "MET",
                        "evidence_id": ev_id,
                        "quote": sentence,
                        "rationale": "excerpt shows it",
                        "confidence": 0.8,
                    }
                )
        return {"verdicts": verdicts}

    def _change_classification(self, content: str) -> dict[str, Any]:
        pairs = re.findall(r"^Pair ([\d.]+):", content, re.M)
        return {
            "changes": [
                {
                    "pair_id": p,
                    "category": "EDITORIAL" if p == "3.2" else "FREQUENCY_CHANGED",
                    "is_material": p != "3.2",
                    "summary": f"Paragraph {p} changed",
                    "confidence": 0.9,
                }
                for p in pairs
            ]
        }

    def _remediation(self, content: str) -> dict[str, Any]:
        return {
            "plans": [
                {
                    "finding_id": fid,
                    "root_cause": "Control design lags the requirement",
                    "actions": [
                        {"kind": "CONTROL_CHANGE", "description": "Align the control with the obligation"},
                        {"kind": "TESTING", "description": "Re-test the control and file evidence"},
                    ],
                    "suggested_owner": "Compliance",
                    "target_days": 45,
                    "dependencies": [],
                    "expected_evidence": "Test report",
                    "verification_method": "Independent re-test",
                    "touches_production": False,
                }
                for fid, _s, _t in _docs(content)
            ]
        }

    def _cluster_confirmation(self, content: str) -> dict[str, Any]:
        return {
            "groups": [
                {
                    "group_id": gid,
                    "equivalent": True,
                    "topic": "Shared requirement",
                    "summary": "Similar duty",
                    "shared_control_suggestion": "One control",
                    "confidence": 0.7,
                }
                for gid, _s, _t in _docs(content)
            ]
        }

    def _conflict_detection(self, content: str) -> dict[str, Any]:
        return {
            "pairs": [
                {
                    "pair_id": pid,
                    "is_potential_conflict": True,
                    "conflict_type": "DURATION_MISMATCH",
                    "reason": "different retention periods",
                    "resolution_hint": None,
                    "applicability_overlap": "same customer data",
                    "confidence": 0.6,
                }
                for pid, _s, _t in _docs(content)
            ]
        }

    def _impact_planning(self, content: str) -> dict[str, Any]:
        catalogue = json.loads(content)["catalogue"]
        return {"steps": list(catalogue), "rationale": "Run the full propagation chain."}

    def _simulation_parsing(self, content: str) -> dict[str, Any]:
        docs = _docs(content)
        return {
            "target_obligation_ids": [docs[0][0]] if docs else [],
            "change_kind": "FREQUENCY",
            "new_frequency_text": "every quarter",
            "interpretation": "Move to quarterly",
            "confidence": 0.8,
        }

    def _query_understanding(self, content: str) -> dict[str, Any]:
        return {"query_type": "REGULATION_LOOKUP", "rewritten_query": content, "sub_queries": []}

    def _grounded_answer(self, content: str) -> dict[str, Any]:
        docs = _docs(content)
        if not docs:
            return {"answer": "none", "claims": [], "insufficient_evidence": True}
        sid, _s, text = docs[0]
        return {
            "answer": text[:200],
            "claims": [{"text": text[:200], "citations": [sid]}],
            "insufficient_evidence": False,
        }

    def _applicability(self, content: str) -> dict[str, Any]:
        return {
            "decisions": [
                {"obligation_id": oid, "decision": "APPLICABLE", "rationale": "bank", "confidence": 0.7}
                for oid, _s, _t in _docs(content)
            ]
        }


def _period_text(period: str | None) -> str | None:
    return {
        "P1D": "daily",
        "P1M": "every month",
        "P3M": "at least once every quarter",
        "P6M": "at least once in six months",
        "P1Y": "at least once a year",
        "P2Y": "once every two years",
        "P8Y": "once every eight years",
        "P10Y": "once every ten years",
    }.get(period or "")
