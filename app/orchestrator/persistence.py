"""Keeps workflow_runs, tasks and executions in step with a running workflow.

:class:`RunRecorder` writes the run and its pending tasks before the run
starts, updates rows as progress events arrive (so the status endpoints show
live state), and reconciles everything once more when the run ends. It uses
its own session, so it never shares one with the agent registry lookups that
happen in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Execution, Task
from app.models import WorkflowRun as WorkflowRunRow
from app.orchestrator.workflow import RunStatus, TaskAttempt, TaskRun, WorkflowEvent, WorkflowRun
from app.policy.pricing import PriceBook, load_price_book
from app.repositories.agent_repository import AgentNotFoundError, AgentRepository
from app.runtimes.base import AgentResult, AgentResultStatus

logger = logging.getLogger(__name__)

# Orchestrator states -> values allowed by the DB CHECK constraints.
RUN_STATUS = {
    RunStatus.PENDING: "pending",
    RunStatus.QUEUED: "queued",  # tasks only
    RunStatus.RUNNING: "running",
    RunStatus.COMPLETED: "succeeded",
    RunStatus.FAILED: "failed",
    RunStatus.SKIPPED: "skipped",  # tasks only
    RunStatus.CANCELLED: "cancelled",
}
EXECUTION_STATUS = {
    AgentResultStatus.COMPLETED: "succeeded",
    AgentResultStatus.FAILED: "failed",
    AgentResultStatus.TIMED_OUT: "timed_out",
    AgentResultStatus.STEP_LIMIT_EXCEEDED: "failed",
}
#: Events that change no stored state.
_NO_STATE = {"review.completed", "task.revision_requested"}


def as_json_object(value: Any) -> dict[str, Any] | None:
    """JSONB output columns hold objects; wrap plain strings and lists."""
    if value is None or isinstance(value, dict):
        return value
    return {"result": value}


def execution_status(attempt: TaskAttempt) -> str:
    if attempt.cancelled:
        return "cancelled"
    if attempt.result is not None:
        return EXECUTION_STATUS[attempt.result.status]
    return "running" if attempt.completed_at is None else "failed"


class RunRecorder:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.session = sessionmaker()
        self._lock = asyncio.Lock()
        self._agents: dict[str, tuple[uuid.UUID | None, int | None]] = {}
        self._prices = PriceBook()

    async def close(self) -> None:
        await self.session.close()

    async def create(
        self, workflow_id: uuid.UUID, run: WorkflowRun, requested_by: str | None
    ) -> None:
        """Insert the run as pending, with a pending row per task, and commit."""
        self._prices = await load_price_book(self.session)
        repo = AgentRepository(self.session)
        for slug in {t.agent_id for t in run.tasks if t.agent_id}:
            try:
                agent = await repo.get(slug, include_archived=True)
            except AgentNotFoundError:
                self._agents[slug] = (None, None)
            else:
                self._agents[slug] = (agent.id, agent.current_version)

        run_id = uuid.UUID(run.id)
        self.session.add(
            WorkflowRunRow(
                id=run_id,
                workflow_id=workflow_id,
                status=RUN_STATUS[run.status],
                input=run.input,
                requested_by=requested_by,
            )
        )
        await self.session.flush()  # the models have no relationships to order inserts by
        self.session.add_all(
            Task(
                id=uuid.UUID(t.id),
                run_id=run_id,
                assigned_agent_id=self._agent_ref(t.agent_id)[0],
                name=t.step_id,
                kind=t.kind,
                depends_on=t.depends_on,
                status=RUN_STATUS[t.status],
            )
            for t in run.tasks
        )
        await self.session.commit()

    async def record(self, event: WorkflowEvent, run: WorkflowRun) -> None:
        """Event handler: write what the event changed. Errors are logged and
        left for :meth:`finish` to repair. The write is shielded, so cancelling
        the run never interrupts a commit halfway."""
        if event.type in _NO_STATE:
            return
        await asyncio.shield(self._record(event, run))

    async def _record(self, event: WorkflowEvent, run: WorkflowRun) -> None:
        async with self._lock:
            try:
                if event.step_id is not None:
                    await self._sync_task(run.task(event.step_id), uuid.UUID(run.id))
                else:
                    await self._sync_run(run)
                await self.session.commit()
            except Exception:
                logger.exception("Could not record %s for run %s", event.type, run.id)
                await self.session.rollback()

    async def finish(self, run: WorkflowRun) -> None:
        """Write the final state of the run and every task."""
        async with self._lock:
            try:
                for task in run.tasks:
                    await self._sync_task(task, uuid.UUID(run.id))
                await self._sync_run(run)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                raise

    # --- internals -------------------------------------------------------------

    def _agent_ref(self, slug: str | None) -> tuple[uuid.UUID | None, int | None]:
        return self._agents.get(slug, (None, None)) if slug else (None, None)

    async def _sync_run(self, run: WorkflowRun) -> None:
        row = await self.session.get(WorkflowRunRow, uuid.UUID(run.id))
        if row is None:
            return
        row.status = RUN_STATUS[run.status]
        row.output = as_json_object(run.output)
        row.error = run.error
        row.started_at = run.started_at
        row.completed_at = run.completed_at

    async def _sync_task(self, task: TaskRun, run_id: uuid.UUID) -> None:
        row = await self.session.get(Task, uuid.UUID(task.id))
        if row is None:
            return
        row.status = RUN_STATUS[task.status]
        row.input = task.input
        row.output = as_json_object(task.output)
        row.error = task.error
        row.retry_count = max(len(task.attempts) - 1, 0)

        agent_uuid, agent_version = self._agent_ref(task.agent_id)
        for attempt in task.attempts:
            execution = await self.session.get(Execution, uuid.UUID(attempt.id))
            if execution is None:
                execution = Execution(
                    id=uuid.UUID(attempt.id),
                    task_id=row.id,
                    agent_id=agent_uuid,
                    agent_version=agent_version,
                )
                self.session.add(execution)
            result = attempt.result
            execution.status = execution_status(attempt)
            execution.started_at = attempt.started_at
            execution.completed_at = attempt.completed_at
            execution.token_usage = result.usage.model_dump() if result else None
            execution.model = result.model if result else None
            execution.cost_usd = self._prices.cost(result.model, result.usage) if result else None
            execution.error = attempt.error
            execution.logs = _logs(attempt)


def direct_execution(
    result: AgentResult,
    *,
    agent_id: uuid.UUID | None,
    agent_version: int | None,
    prices: PriceBook,
) -> Execution:
    """The record of a direct agent run (no workflow task)."""
    from datetime import UTC, datetime, timedelta

    completed = datetime.now(UTC)
    return Execution(
        agent_id=agent_id,
        agent_version=agent_version,
        status=EXECUTION_STATUS[result.status],
        started_at=completed - timedelta(milliseconds=result.duration_ms),
        completed_at=completed,
        token_usage=result.usage.model_dump(),
        model=result.model,
        cost_usd=prices.cost(result.model, result.usage),
        error=result.error,
        logs=[{"metadata": result.metadata}] if result.metadata else None,
    )


def _logs(attempt: TaskAttempt) -> list[dict[str, Any]] | None:
    logs: list[dict[str, Any]] = []
    revision = attempt.input.get("revision")
    if revision:
        logs.append(
            {
                "revision": {
                    "round": revision.get("round"),
                    "reviewer": revision.get("reviewer"),
                    "feedback": revision.get("feedback"),
                }
            }
        )
    if attempt.result is not None and attempt.result.metadata:
        logs.append({"metadata": attempt.result.metadata})
    return logs or None
