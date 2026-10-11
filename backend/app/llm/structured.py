"""Schema-validated LLM output.

Raw model JSON is never trusted. Every structured call:
1. sends the Pydantic model's JSON schema to the provider,
2. extracts JSON from the text (tolerating fences/prose),
3. validates it with Pydantic,
4. on failure, feeds the exact validation errors back once ("repair"),
5. raises StructuredOutputError if it is still invalid - the caller then routes the
   item to human review instead of guessing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.errors import StructuredOutputError
from app.llm.json_tools import extract_json_text, inline_schema
from app.llm.router import LLMRouter
from app.llm.types import FinishReason, LLMRequest, LLMResponse, Message
from app.observability.logging import get_logger

T = TypeVar("T", bound=BaseModel)
_log = get_logger("regtrace.llm.structured")
_MAX_ERRORS_FED_BACK = 10


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    value: T
    # Every model response involved (first attempt + repairs) for tracing/costing.
    responses: list[LLMResponse]


class StructuredLLM:
    def __init__(self, router: LLMRouter, max_repairs: int = 1) -> None:
        self._router = router
        self._max_repairs = max_repairs

    async def generate(self, request: LLMRequest, schema: type[T]) -> StructuredResult[T]:
        json_schema = inline_schema(schema.model_json_schema())
        current = request.model_copy(update={"json_schema": json_schema})
        responses: list[LLMResponse] = []

        for repair_no in range(self._max_repairs + 1):
            response = await self._router.generate(current)
            responses.append(response)
            problem = self._problem(response, schema)
            if not isinstance(problem, str):
                return StructuredResult(value=problem, responses=responses)

            _log.warning(
                "llm.structured.invalid",
                purpose=request.purpose,
                provider=response.provider,
                repair_no=repair_no,
                problem=problem[:300],
            )
            current = current.model_copy(
                update={
                    "messages": [
                        *current.messages,
                        Message(role="assistant", content=response.text),
                        Message(
                            role="user",
                            content=(
                                "Your previous reply was rejected by the validator:\n"
                                f"{problem}\n"
                                "Return the corrected JSON object only."
                            ),
                        ),
                    ]
                }
            )

        raise StructuredOutputError(
            f"Invalid structured output for '{request.purpose}' after {self._max_repairs} repair attempt(s)",
            context={"last_output": responses[-1].text[:1000]},
        )

    @staticmethod
    def _problem(response: LLMResponse, schema: type[T]) -> T | str:
        """Return the validated model, or a human-readable description of the problem."""
        if response.finish_reason is FinishReason.LENGTH:
            return "Output was truncated at the token limit. Be more concise."
        try:
            return schema.model_validate_json(extract_json_text(response.text))
        except ValidationError as exc:
            errors = [
                f"- {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                for err in exc.errors()[:_MAX_ERRORS_FED_BACK]
            ]
            return "\n".join(errors)
        except json.JSONDecodeError as exc:
            return f"Not valid JSON: {exc.msg}"
