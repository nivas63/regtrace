"""Prompt-injection scanner for untrusted document text (ingestion-time).

Prototype implementation: weighted heuristic patterns. It is one layer of defence;
the others are structural (retrieved text is always wrapped as data, agents have no
side-effect tools, outputs are schema-validated, state changes go through workflow
steps and human gates). Chunks scoring >= QUARANTINE_THRESHOLD are excluded from
agent context until a reviewer releases them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

QUARANTINE_THRESHOLD = 0.8

_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(
            r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules|prompts?)",
            re.I,
        ),
        0.9,
        "instruction_override",
    ),
    (re.compile(r"\byou are now\b|\bact as\b.{0,30}\b(assistant|ai|model|system)\b", re.I), 0.6, "role_change"),
    (re.compile(r"\b(system prompt|developer message|hidden instructions?)\b", re.I), 0.6, "prompt_reference"),
    (
        re.compile(
            r"\b(reveal|print|output|leak)\b.{0,30}\b(prompt|instructions|api key|secret|password|token)\b", re.I
        ),
        0.8,
        "secret_extraction",
    ),
    (
        re.compile(r"\bmark (this|all|every).{0,40}\b(compliant|effective|approved)\b", re.I),
        0.9,
        "verdict_manipulation",
    ),
    (re.compile(r"<\s*/?\s*(system|assistant|instructions?)\s*>", re.I), 0.7, "fake_delimiters"),
    (re.compile(r"\b(call|invoke|execute)\b.{0,20}\b(tool|function|command)\b", re.I), 0.5, "tool_abuse"),
    (re.compile(r"[A-Za-z0-9+/]{120,}={0,2}"), 0.4, "encoded_payload"),
]


@dataclass(frozen=True)
class InjectionScan:
    score: float
    signals: list[str]

    @property
    def quarantine(self) -> bool:
        return self.score >= QUARANTINE_THRESHOLD


def scan(text: str) -> InjectionScan:
    signals: list[str] = []
    remaining = 1.0
    for pattern, weight, label in _PATTERNS:
        if pattern.search(text):
            signals.append(label)
            remaining *= 1.0 - weight  # noisy-OR combination
    return InjectionScan(score=round(1.0 - remaining, 3), signals=signals)
