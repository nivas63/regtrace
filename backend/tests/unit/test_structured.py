from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from app.llm.errors import StructuredOutputError
from app.llm.json_tools import extract_json_text, inline_schema
from app.llm.rate_limiter import RateLimiter
from app.llm.router import LLMRouter, RouterPolicy
from app.llm.structured import StructuredLLM
from app.llm.types import LLMRequest, Message, ModelTier
from tests.conftest import FakeClock
from tests.unit.test_router import ScriptedProvider


class Citation(BaseModel):
    chunk_id: str
    quote: str


class Probe(BaseModel):
    modality: Literal["MUST", "MAY"]
    citations: list[Citation]


REQUEST = LLMRequest(purpose="t", system="s", messages=[Message(role="user", content="x")])
VALID = '{"modality":"MUST","citations":[{"chunk_id":"C1","quote":"shall"}]}'


def _structured(provider: ScriptedProvider, clock: FakeClock) -> StructuredLLM:
    router = LLMRouter(
        providers={provider.name: provider},
        chains={tier: [provider.name] for tier in ModelTier},
        rate_limiter=RateLimiter({}, clock=clock),
        policy=RouterPolicy(),
        clock=clock,
    )
    return StructuredLLM(router, max_repairs=1)


async def test_valid_output_parses_first_time(clock: FakeClock) -> None:
    result = await _structured(ScriptedProvider("a", [VALID]), clock).generate(REQUEST, Probe)
    assert result.value.modality == "MUST" and len(result.responses) == 1


async def test_fenced_output_is_accepted(clock: FakeClock) -> None:
    provider = ScriptedProvider("a", [f"Sure! ```json\n{VALID}\n```"])
    result = await _structured(provider, clock).generate(REQUEST, Probe)
    assert result.value.citations[0].chunk_id == "C1"


async def test_invalid_output_is_repaired_with_validator_feedback(clock: FakeClock) -> None:
    provider = ScriptedProvider("a", ['{"modality":"MAYBE","citations":[]}', VALID])
    result = await _structured(provider, clock).generate(REQUEST, Probe)
    assert result.value.modality == "MUST" and len(result.responses) == 2


async def test_still_invalid_after_repair_raises(clock: FakeClock) -> None:
    provider = ScriptedProvider("a", ["not json", "still not json"])
    with pytest.raises(StructuredOutputError):
        await _structured(provider, clock).generate(REQUEST, Probe)


def test_inline_schema_resolves_refs_and_strips_titles() -> None:
    schema = inline_schema(Probe.model_json_schema())
    assert "$defs" not in schema and "title" not in schema
    item = schema["properties"]["citations"]["items"]
    assert item["properties"]["chunk_id"]["type"] == "string"


def test_extract_json_text_handles_prose_prefix() -> None:
    assert extract_json_text('Here you go: {"a": 1} thanks') == '{"a": 1}'
