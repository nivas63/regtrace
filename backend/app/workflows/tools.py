"""Explicit, permissioned tool registry.

Agents never call arbitrary functions. A tool has a Pydantic input schema, an
allow-list of agents, a timeout, and every invocation (including denials and
timeouts) is persisted to `tool_calls` for the execution trace.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from app.core.errors import AuthorizationError, DomainValidationError, RegTraceError
from app.models import ToolCall

if TYPE_CHECKING:
    from app.container import Services


@dataclass(frozen=True)
class ToolEnv:
    services: Services
    bank_id: uuid.UUID
    run_id: uuid.UUID | None
    step_id: uuid.UUID | None


ToolFunc = Callable[[ToolEnv, Any], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    func: ToolFunc
    allowed_agents: frozenset[str]
    timeout_s: float = 60.0
    side_effects: bool = False


class ToolTimeoutError(RegTraceError):
    code = "tool_timeout"
    title = "Tool timed out"
    http_status = 504


def _summarise(result: Any) -> dict[str, Any]:
    if isinstance(result, list):
        return {"type": "list", "count": len(result)}
    if isinstance(result, dict):
        return {"type": "dict", "keys": sorted(result)[:20]}
    if isinstance(result, BaseModel):
        return {"type": type(result).__name__}
    return {"type": type(result).__name__}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} registered twice")
        self._tools[tool.name] = tool

    def catalogue(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "agents": sorted(t.allowed_agents),
                "side_effects": t.side_effects,
                "input_schema": t.input_model.model_json_schema(),
            }
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    async def call(self, env: ToolEnv, agent: str, name: str, args: dict[str, Any]) -> Any:
        started = time.perf_counter()
        tool = self._tools.get(name)
        status, error, result = "OK", None, None
        try:
            if tool is None:
                raise AuthorizationError(f"Unknown tool {name!r}")
            if agent not in tool.allowed_agents:
                raise AuthorizationError(f"Agent {agent!r} may not call {name!r}")
            try:
                parsed = tool.input_model.model_validate(args)
            except ValidationError as exc:
                raise DomainValidationError(f"Invalid arguments for {name}: {exc}") from exc
            try:
                result = await asyncio.wait_for(tool.func(env, parsed), timeout=tool.timeout_s)
            except TimeoutError as exc:
                raise ToolTimeoutError(f"{name} exceeded {tool.timeout_s}s") from exc
            return result
        except AuthorizationError as exc:
            status, error = "DENIED", exc.detail
            raise
        except ToolTimeoutError as exc:
            status, error = "TIMEOUT", exc.detail
            raise
        except Exception as exc:
            status, error = "ERROR", str(exc)[:500]
            raise
        finally:
            async with env.services.db.system_session() as session:
                session.add(
                    ToolCall(
                        run_id=env.run_id,
                        step_id=env.step_id,
                        agent=agent,
                        tool=name,
                        args=_jsonable(args),
                        result_summary=_summarise(result) if status == "OK" else {},
                        status=status,
                        error=error,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                )


def _jsonable(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, list):
            out[key] = [str(v) if isinstance(v, uuid.UUID) else v for v in value][:50]
        elif isinstance(value, str) and len(value) > 500:
            out[key] = value[:500] + "..."
        else:
            out[key] = value
    return out
