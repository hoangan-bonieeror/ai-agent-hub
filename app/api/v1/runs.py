"""Workflow run status and live progress: /api/v1/runs"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models import Agent, Execution, Task, Workflow
from app.models import WorkflowRun as WorkflowRunRow
from app.orchestrator.persistence import RUN_STATUS, as_json_object
from app.orchestrator.runner import RunManager, get_run_manager
from app.orchestrator.workflow import TaskRun, WorkflowRun
from app.runtimes.base import TokenUsage

router = APIRouter(prefix="/runs", tags=["runs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[RunManager, Depends(get_run_manager)]

TERMINAL_RUN_STATUSES = ("succeeded", "failed", "cancelled")


def parse_id(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"{what} not found: {value}"
        ) from None


def run_links(run_id: str) -> dict[str, str]:
    return {"self": f"/api/v1/runs/{run_id}", "events": f"/api/v1/runs/{run_id}/events"}


# --- views of an in-memory run (returned by POST /workflows/{id}/run) ------------


def task_view(task: TaskRun) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "step_id": task.step_id,
        "kind": task.kind,
        "agent_id": task.agent_id,
        "depends_on": task.depends_on,
        "status": RUN_STATUS[task.status],
        "output": as_json_object(task.output),
        "error": task.error,
        "attempts": len(task.attempts),
        "duration_ms": task.result.duration_ms if task.result else None,
    }


def run_view(run: WorkflowRun, workflow: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "workflow": workflow,
        "status": RUN_STATUS[run.status],
        "output": run.output,
        "error": run.error,
        "usage": run.usage.model_dump(),
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "tasks": [task_view(t) for t in run.tasks],
        "links": run_links(run.id),
    }


# --- views of stored rows (status endpoints) --------------------------------------


async def agent_slugs(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = await session.execute(select(Agent.id, Agent.slug).where(Agent.id.in_(wanted)))
    return {row_id: slug for row_id, slug in rows.all()}


def execution_summary(row: Execution) -> dict[str, Any]:
    duration = None
    if row.completed_at is not None and row.started_at is not None:
        duration = int((row.completed_at - row.started_at).total_seconds() * 1000)
    return {
        "execution_id": str(row.id),
        "status": row.status,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": duration,
        "token_usage": row.token_usage,
        "model": row.model,
        "cost_usd": money(row.cost_usd),
        "error": row.error,
    }


def stored_task_view(
    row: Task, slugs: dict[uuid.UUID, str], executions: list[Execution]
) -> dict[str, Any]:
    return {
        "task_id": str(row.id),
        "run_id": str(row.run_id),
        "step_id": row.name,
        "kind": row.kind,
        "agent_id": slugs.get(row.assigned_agent_id) if row.assigned_agent_id else None,
        "depends_on": row.depends_on,
        "status": row.status,
        "output": row.output,
        "error": row.error,
        "retry_count": row.retry_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "executions": [execution_summary(e) for e in executions],
    }


def money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def cost_of(executions: list[Execution]) -> dict[str, Any]:
    """Total dollar cost; ``unpriced`` counts calls whose model had no price."""
    priced = [e.cost_usd for e in executions if e.cost_usd is not None]
    unpriced = sum(1 for e in executions if e.cost_usd is None and e.token_usage)
    return {"cost_usd": float(sum(priced, Decimal(0))), "unpriced_executions": unpriced}


async def run_costs(session: AsyncSession, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, float]:
    if not run_ids:
        return {}
    rows = await session.execute(
        select(Task.run_id, func.coalesce(func.sum(Execution.cost_usd), 0))
        .join(Execution, Execution.task_id == Task.id)
        .where(Task.run_id.in_(run_ids))
        .group_by(Task.run_id)
    )
    return {run_id: float(total) for run_id, total in rows.all()}


def usage_of(executions: list[Execution]) -> dict[str, int]:
    total = TokenUsage()
    for execution in executions:
        if execution.token_usage:
            total = total + TokenUsage.model_validate(execution.token_usage)
    return total.model_dump()


def workflow_ref(row: Workflow) -> dict[str, Any]:
    return {"id": str(row.id), "name": row.name, "version": row.version}


async def load_run(session: AsyncSession, run_id: str) -> tuple[WorkflowRunRow, Workflow]:
    found = (
        await session.execute(
            select(WorkflowRunRow, Workflow)
            .join(Workflow, Workflow.id == WorkflowRunRow.workflow_id)
            .where(WorkflowRunRow.id == parse_id(run_id, "Run"))
        )
    ).first()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Run not found: {run_id}")
    run, workflow = found
    return run, workflow


@router.get("")
async def list_runs(
    session: SessionDep,
    workflow: Annotated[str | None, Query(description="Workflow name")] = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Runs, newest first."""
    stmt = select(WorkflowRunRow, Workflow).join(
        Workflow, Workflow.id == WorkflowRunRow.workflow_id
    )
    if workflow is not None:
        stmt = stmt.where(Workflow.name == workflow)
    if run_status is not None:
        stmt = stmt.where(WorkflowRunRow.status == run_status)
    stmt = stmt.order_by(WorkflowRunRow.created_at.desc(), WorkflowRunRow.id).limit(limit)
    rows = (await session.execute(stmt.offset(offset))).all()
    costs = await run_costs(session, [run.id for run, _ in rows])
    return {
        "items": [
            {
                "run_id": str(run.id),
                "workflow": workflow_ref(wf),
                "status": run.status,
                "error": run.error,
                "requested_by": run.requested_by,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "cost_usd": costs.get(run.id, 0.0),
                "links": run_links(str(run.id)),
            }
            for run, wf in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{run_id}")
async def get_run(run_id: str, session: SessionDep) -> dict[str, Any]:
    """A run with every task's status and output. While the run is going,
    this shows its live state."""
    run, wf = await load_run(session, run_id)
    tasks = list((await session.scalars(select(Task).where(Task.run_id == run.id))).all())
    order = {s.get("id"): i for i, s in enumerate(wf.definition.get("steps", []))}
    tasks.sort(key=lambda t: order.get(t.name, len(order)))
    executions = list(
        (
            await session.scalars(
                select(Execution)
                .where(Execution.task_id.in_([t.id for t in tasks]))
                .order_by(Execution.started_at)
            )
        ).all()
    )
    slugs = await agent_slugs(session, {t.assigned_agent_id for t in tasks})
    by_task: dict[uuid.UUID, list[Execution]] = {}
    for execution in executions:
        by_task.setdefault(execution.task_id, []).append(execution)
    return {
        "run_id": str(run.id),
        "workflow": workflow_ref(wf),
        "status": run.status,
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "requested_by": run.requested_by,
        "usage": usage_of(executions),
        **cost_of(executions),
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "tasks": [stored_task_view(t, slugs, by_task.get(t.id, [])) for t in tasks],
        "links": run_links(str(run.id)),
    }


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(run_id: str, session: SessionDep, manager: ManagerDep) -> dict[str, Any]:
    """Cancel a background run. Running agent calls are stopped and steps not
    started yet are skipped."""
    run, _ = await load_run(session, run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Run already {run.status}")
    if not await manager.cancel(str(run.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Run is not executing on this server")
    return {"run_id": str(run.id), "status": "cancelled", "links": run_links(str(run.id))}


def sse(event: str, data: str, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {data}\n\n"


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def run_events(
    run_id: str,
    session: SessionDep,
    manager: ManagerDep,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int, Query(ge=0, description="Skip the first N events")] = 0,
) -> StreamingResponse:
    """Server-Sent Events for a run (plan section 8.2).

    Replays the run's events so far, then streams new ones until the run
    ends. Each event has an ``id``; reconnecting with ``Last-Event-ID``
    resumes after it. If this server no longer holds the run's events (it
    finished long ago, or the server restarted), one ``run.status`` event
    with the stored status is sent instead.
    """
    run, _ = await load_run(session, run_id)
    snapshot = {"run_id": str(run.id), "status": run.status, "error": run.error}
    await session.close()

    start = after
    if last_event_id is not None and last_event_id.isdigit():
        start = int(last_event_id)
    key = str(run.id)

    async def stream() -> AsyncIterator[str]:
        if not manager.broker.has(key):
            yield sse("run.status", json.dumps(snapshot))
            return
        async for item in manager.broker.stream(key, after=start):
            if item is None:
                yield ": keep-alive\n\n"
                continue
            seq, event = item
            yield sse(event.type, event.model_dump_json(), seq)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
