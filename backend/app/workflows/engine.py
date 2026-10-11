"""Durable workflow engine (state machine over persisted steps).

A workflow definition is a set of named steps plus an initial plan (ordered step
names). The engine executes the plan step by step; every attempt is a
`workflow_steps` row and the run's `state` is committed after each step, so a crash
resumes from the last completed step.

Control flow available to a step (via StepResult):
- state_updates : merged into run.state (IDs and counts, never large payloads)
- replan        : replace the *remaining* plan (validated; max MAX_REPLANS per run)
- wait_for_review: pause the run (WAITING_REVIEW) until reviews are resolved
- stop          : finish the run successfully early (e.g. duplicate document)

Failure policy per step: retry retryable errors with exponential backoff up to
`max_attempts`; then run the step's `fallback` if defined; otherwise the run fails
and a RUN_FAILURE human review is opened. Nothing is silently skipped.

Loop prevention: plans may only contain known steps, a step cannot appear twice in a
plan unless marked repeatable, replans are capped, total step executions are capped,
and every LLM call counts against the run's budget (LLMService).
"""

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text

from app.core.errors import DependencyUnavailableError, RegTraceError
from app.llm.errors import LLMExhaustedError, LLMTransientError
from app.llm.service import CallContext
from app.models import HumanReview, WorkflowRun, WorkflowStep
from app.observability.logging import get_logger
from app.services import audit
from app.workflows.tools import ToolEnv

if TYPE_CHECKING:
    from app.container import Services

_log = get_logger("regtrace.workflow")

MAX_REPLANS = 2
MAX_STEP_EXECUTIONS = 60
RETRYABLE: tuple[type[BaseException], ...] = (
    LLMExhaustedError,
    LLMTransientError,
    DependencyUnavailableError,
    ConnectionError,
    TimeoutError,
)


class WorkflowError(RegTraceError):
    code = "workflow_error"
    title = "Workflow error"


@dataclass
class StepResult:
    output: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)
    replan: list[str] | None = None
    replan_reason: str | None = None
    wait_for_review: list[uuid.UUID] | None = None
    stop: bool = False


@dataclass(frozen=True)
class RunSnapshot:
    id: uuid.UUID
    bank_id: uuid.UUID
    workflow_type: str
    input: dict[str, Any]
    state: dict[str, Any]


@dataclass
class StepContext:
    run: RunSnapshot
    step_id: uuid.UUID
    step_name: str
    attempt: int
    services: Services
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def llm_ctx(self, agent: str) -> CallContext:
        return CallContext(agent=agent, run_id=self.run.id, step_id=self.step_id)

    @property
    def tool_env(self) -> ToolEnv:
        return ToolEnv(self.services, self.run.bank_id, self.run.id, self.step_id)

    async def tool(self, agent: str, name: str, **args: Any) -> Any:
        return await self.services.tools.call(self.tool_env, agent, name, args)

    def decide(self, decision: str, reason: str, **data: Any) -> None:
        """Record an explicit decision for the trace (why a branch was taken)."""
        self.decisions.append({"decision": decision, "reason": reason, **data})


StepHandler = Callable[[StepContext], Awaitable[StepResult]]


@dataclass(frozen=True)
class StepSpec:
    name: str
    handler: StepHandler
    description: str
    agent: str | None = None
    max_attempts: int = 3
    fallback: StepHandler | None = None
    repeatable: bool = False


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    description: str
    steps: dict[str, StepSpec]
    initial_plan: Callable[[dict[str, Any]], list[str]]
    llm_call_budget: int = 300

    def validate_plan(self, plan: Sequence[str]) -> list[str]:
        unknown = [s for s in plan if s not in self.steps]
        if unknown:
            raise WorkflowError(f"Plan contains unknown steps: {unknown}")
        seen: set[str] = set()
        for name in plan:
            if name in seen and not self.steps[name].repeatable:
                raise WorkflowError(f"Step {name!r} appears twice in plan")
            seen.add(name)
        return list(plan)


class WorkflowEngine:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._definitions: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        self._definitions[definition.name] = definition

    def definition(self, name: str) -> WorkflowDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise WorkflowError(f"Unknown workflow {name!r}") from exc

    @property
    def definitions(self) -> dict[str, WorkflowDefinition]:
        return dict(self._definitions)

    # -- queueing -----------------------------------------------------------------

    async def enqueue(
        self,
        *,
        bank_id: uuid.UUID,
        workflow_type: str,
        input: dict[str, Any],
        triggered_by: str,
        idempotency_key: str | None = None,
        parent_run_id: uuid.UUID | None = None,
        state: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        definition = self.definition(workflow_type)
        async with self._services.db.system_session() as session:
            if idempotency_key:
                existing = await session.scalar(
                    select(WorkflowRun).where(WorkflowRun.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    return existing
            run = WorkflowRun(
                bank_id=bank_id,
                workflow_type=workflow_type,
                status="QUEUED",
                input=input,
                state=dict(state or {}),
                plan=definition.validate_plan(definition.initial_plan(input)),
                llm_call_budget=definition.llm_call_budget,
                triggered_by=triggered_by,
                idempotency_key=idempotency_key,
                parent_run_id=parent_run_id,
            )
            session.add(run)
            await session.flush()
        async with self._services.db.tenant_session(bank_id) as session:
            await audit.record(
                session,
                bank_id=bank_id,
                actor=audit.Actor("USER" if triggered_by.startswith("user:") else "SYSTEM", triggered_by),
                action="workflow.enqueued",
                subject_type="workflow_run",
                subject_id=run.id,
                run_id=run.id,
                details={"workflow_type": workflow_type, "plan": run.plan},
            )
        return run

    async def claim_next(self, worker_id: str) -> uuid.UUID | None:
        async with self._services.db.system_session() as session:
            run_id = await session.scalar(
                text(
                    """
                    UPDATE workflow_runs SET status = 'RUNNING', locked_by = :worker,
                        started_at = COALESCE(started_at, now())
                    WHERE id = (
                        SELECT id FROM workflow_runs
                        WHERE status = 'QUEUED' AND available_at <= now()
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                ),
                {"worker": worker_id},
            )
        return uuid.UUID(str(run_id)) if run_id is not None else None

    async def recover_orphans(self) -> int:
        """Re-queue runs left RUNNING by a crashed process (single-worker deployment)."""
        async with self._services.db.system_session() as session:
            result = await session.execute(
                text("UPDATE workflow_runs SET status='QUEUED', locked_by=NULL WHERE status='RUNNING'")
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def retry(self, run_id: uuid.UUID) -> bool:
        """Re-queue a FAILED run; completed steps are skipped, so it resumes where it stopped."""
        async with self._services.db.system_session() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is None or run.status != "FAILED":
                return False
            run.status, run.error, run.finished_at, run.locked_by = "QUEUED", None, None, None
            run.llm_calls_used = 0  # fresh budget for the resumed attempt
        return True

    async def resume(self, run_id: uuid.UUID) -> None:
        async with self._services.db.system_session() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is not None and run.status == "WAITING_REVIEW":
                run.status = "QUEUED"

    # -- execution ------------------------------------------------------------------

    async def execute(self, run_id: uuid.UUID) -> None:
        async with self._services.db.system_session() as session:
            run = await session.get(WorkflowRun, run_id)
            if run is None:
                return
            definition = self.definition(run.workflow_type)
            done = set(
                (
                    await session.scalars(
                        select(WorkflowStep.step_name).where(
                            WorkflowStep.run_id == run_id, WorkflowStep.status.in_(("SUCCEEDED", "FALLBACK"))
                        )
                    )
                ).all()
            )
            executions = (
                await session.scalar(text("SELECT count(*) FROM workflow_steps WHERE run_id = :id"), {"id": run_id})
                or 0
            )
            plan, state, replans = list(run.plan), dict(run.state), run.replans
            snapshot = RunSnapshot(run.id, run.bank_id, run.workflow_type, dict(run.input), state)

        _log.info("workflow.run.start", run_id=str(run_id), workflow=snapshot.workflow_type)
        index = 0
        while index < len(plan):
            name = plan[index]
            index += 1
            if name in done and not definition.steps[name].repeatable:
                continue
            spec = definition.steps[name]
            executions += 1
            if executions > MAX_STEP_EXECUTIONS:
                await self._fail(snapshot, name, "Step execution cap reached (loop guard)")
                return
            ordinal = index
            result = await self._run_step(snapshot, spec, ordinal)
            if result is None:
                return  # failed; run already marked
            state.update(result.state_updates)
            snapshot = RunSnapshot(snapshot.id, snapshot.bank_id, snapshot.workflow_type, snapshot.input, state)
            if result.replan is not None:
                if replans >= MAX_REPLANS:
                    await self._fail(snapshot, name, "Replan limit reached (loop guard)")
                    return
                remaining = definition.validate_plan(result.replan)
                plan = plan[:index] + remaining
                replans += 1
                _log.info("workflow.replan", run_id=str(run_id), new_remaining=remaining)
            await self._save_progress(snapshot, plan, replans, name)
            if result.wait_for_review:
                await self._set_status(snapshot.id, "WAITING_REVIEW")
                return
            if result.stop:
                break

        await self._finish(snapshot)

    async def _run_step(self, run: RunSnapshot, spec: StepSpec, ordinal: int) -> StepResult | None:
        last_error: BaseException | None = None
        for attempt in range(1, spec.max_attempts + 1):
            step_id = await self._start_step(run, spec, ordinal, attempt)
            ctx = StepContext(run, step_id, spec.name, attempt, self._services)
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    spec.handler(ctx), timeout=self._services.settings.worker_step_timeout_s
                )
            except RETRYABLE as exc:
                last_error = exc
                await self._end_step(step_id, "FAILED", {}, ctx.decisions, _describe(exc), started)
                if attempt < spec.max_attempts:
                    await asyncio.sleep(min(60.0, 15.0 * 2 ** (attempt - 1)))  # outlast per-minute quota windows
                    continue
                break
            except Exception as exc:  # non-retryable
                last_error = exc
                _log.error(
                    "workflow.step.error", step=spec.name, error=_describe(exc), trace=traceback.format_exc(limit=5)
                )
                await self._end_step(step_id, "FAILED", {}, ctx.decisions, _describe(exc), started)
                break
            status = "WAITING_REVIEW" if result.wait_for_review else "SUCCEEDED"
            await self._end_step(step_id, status, result.output, ctx.decisions, None, started)
            return result

        if spec.fallback is not None:
            step_id = await self._start_step(run, spec, ordinal, spec.max_attempts + 1)
            ctx = StepContext(run, step_id, spec.name, spec.max_attempts + 1, self._services)
            ctx.decide("fallback", f"Primary strategy failed: {_describe(last_error)}")
            started = time.perf_counter()
            try:
                result = await spec.fallback(ctx)
            except Exception as exc:
                await self._end_step(step_id, "FAILED", {}, ctx.decisions, _describe(exc), started)
                last_error = exc
            else:
                await self._end_step(step_id, "FALLBACK", result.output, ctx.decisions, None, started)
                return result

        await self._fail(run, spec.name, _describe(last_error))
        return None

    async def _start_step(self, run: RunSnapshot, spec: StepSpec, ordinal: int, attempt: int) -> uuid.UUID:
        async with self._services.db.system_session() as session:
            step = WorkflowStep(
                run_id=run.id,
                step_name=spec.name,
                ordinal=ordinal,
                attempt=attempt,
                status="RUNNING",
                agent=spec.agent,
            )
            session.add(step)
            await session.execute(
                text("UPDATE workflow_runs SET current_step = :s WHERE id = :id"),
                {"s": spec.name, "id": run.id},
            )
            await session.flush()
            return step.id

    async def _end_step(
        self,
        step_id: uuid.UUID,
        status: str,
        output: dict[str, Any],
        decisions: list[dict[str, Any]],
        error: str | None,
        started: float,
    ) -> None:
        async with self._services.db.system_session() as session:
            step = await session.get(WorkflowStep, step_id)
            if step is not None:
                step.status = status
                step.output = output
                step.decisions = decisions
                step.error = error
                step.finished_at = datetime.now(UTC)
                step.duration_ms = (time.perf_counter() - started) * 1000

    async def _save_progress(self, run: RunSnapshot, plan: list[str], replans: int, step: str) -> None:
        async with self._services.db.system_session() as session:
            row = await session.get(WorkflowRun, run.id)
            if row is not None:
                row.state = dict(run.state)
                row.plan = plan
                row.replans = replans
                row.current_step = step

    async def _set_status(self, run_id: uuid.UUID, status: str) -> None:
        async with self._services.db.system_session() as session:
            row = await session.get(WorkflowRun, run_id)
            if row is not None:
                row.status = status
                row.locked_by = None

    async def _finish(self, run: RunSnapshot) -> None:
        async with self._services.db.system_session() as session:
            row = await session.get(WorkflowRun, run.id)
            if row is not None:
                row.status = "SUCCEEDED"
                row.finished_at = datetime.now(UTC)
                row.locked_by = None
                row.summary = {k: v for k, v in run.state.items() if not isinstance(v, list | dict)}
        async with self._services.db.tenant_session(run.bank_id) as session:
            await audit.record(
                session,
                bank_id=run.bank_id,
                actor=audit.Actor.agent("supervisor"),
                action="workflow.succeeded",
                subject_type="workflow_run",
                subject_id=run.id,
                run_id=run.id,
                details={"workflow_type": run.workflow_type},
            )
        _log.info("workflow.run.succeeded", run_id=str(run.id))

    async def _fail(self, run: RunSnapshot, step: str, error: str) -> None:
        async with self._services.db.system_session() as session:
            row = await session.get(WorkflowRun, run.id)
            if row is not None:
                row.status = "FAILED"
                row.error = f"{step}: {error}"
                row.finished_at = datetime.now(UTC)
                row.locked_by = None
        async with self._services.db.tenant_session(run.bank_id) as session:
            review = HumanReview(
                bank_id=run.bank_id,
                subject_type="RUN_FAILURE",
                subject_id=run.id,
                reason=f"Workflow '{run.workflow_type}' failed at step '{step}': {error}"[:2000],
                recommendation={"action": "Inspect the trace; fix the cause and re-run, or complete manually."},
                priority="HIGH",
                run_id=run.id,
            )
            session.add(review)
            await audit.record(
                session,
                bank_id=run.bank_id,
                actor=audit.Actor.agent("supervisor"),
                action="workflow.failed",
                subject_type="workflow_run",
                subject_id=run.id,
                run_id=run.id,
                details={"step": step, "error": error[:500]},
            )
        _log.error("workflow.run.failed", run_id=str(run.id), step=step, error=error)

    async def delay(self, run_id: uuid.UUID, seconds: float) -> None:
        async with self._services.db.system_session() as session:
            row = await session.get(WorkflowRun, run_id)
            if row is not None:
                row.status = "QUEUED"
                row.available_at = datetime.now(UTC) + timedelta(seconds=seconds)


def _describe(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    if isinstance(exc, RegTraceError):
        return f"{exc.code}: {exc.detail}"
    return f"{type(exc).__name__}: {exc}"[:1000]
