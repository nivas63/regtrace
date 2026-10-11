"""Reversible PII redaction applied before any bank text leaves the process.

Identifiers are replaced by stable placeholders ([PAN_1], [ACCOUNT_2], ...) and
restored in the model output, so verbatim-quote verification still works against
the original text. Regulatory text is public and is not redacted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("AADHAAR", re.compile(r"\b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b")),
    ("PHONE", re.compile(r"(?:\+91[\s-]?)?\b[6-9][0-9]{9}\b")),
    ("ACCOUNT", re.compile(r"\b[0-9]{11,18}\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
]


@dataclass
class Redactor:
    """One instance per LLM call; holds the placeholder mapping for restoration."""

    mapping: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        for label, pattern in _RULES:

            def replace(match: re.Match[str], lb: str = label) -> str:
                return self._placeholder(lb, match.group(0))

            text = pattern.sub(replace, text)
        return text

    def _placeholder(self, label: str, value: str) -> str:
        if value in self._reverse:
            return self._reverse[value]
        self._counts[label] = self._counts.get(label, 0) + 1
        token = f"[{label}_{self._counts[label]}]"
        self.mapping[token] = value
        self._reverse[value] = token
        return token

    def restore(self, text: str) -> str:
        for token, value in self.mapping.items():
            text = text.replace(token, value)
        return text

    @property
    def redacted_count(self) -> int:
        return len(self.mapping)
