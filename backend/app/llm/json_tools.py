"""JSON-schema preparation for providers and tolerant JSON extraction from model text."""

from __future__ import annotations

import json
import re
from typing import Any

# Keys that carry no constraint and that some providers reject in response schemas.
_STRIP_KEYS = frozenset({"title", "examples", "$schema"})
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a self-contained copy of a Pydantic JSON schema.

    `$ref`s into `$defs` are inlined (Pydantic emits them for nested models) and
    documentation-only keys are dropped. Recursive models are not supported by the
    target providers and raise ValueError.
    """
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name in stack:
                    raise ValueError(f"Recursive schema reference to {name!r} is not supported")
                merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, (*stack, name))
            return {
                key: resolve(value, stack) for key, value in node.items() if key not in _STRIP_KEYS and key != "$defs"
            }
        if isinstance(node, list):
            return [resolve(item, stack) for item in node]
        return node

    result = resolve(schema, ())
    if not isinstance(result, dict):
        raise TypeError("JSON schema root must be an object")
    return result


def schema_instruction(schema: dict[str, Any]) -> str:
    """Prompt text used when a provider cannot enforce the schema natively."""
    return (
        "Respond with a single JSON object only - no prose, no markdown fences. "
        "It must validate against this JSON Schema:\n"
        f"{json.dumps(schema, separators=(',', ':'))}"
    )


def extract_json_text(text: str) -> str:
    """Strip markdown fences / leading prose that models sometimes add around JSON."""
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        return fenced.group(1)
    if stripped.startswith(("{", "[")):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped
