"""Writes orchestrator runs to workflow_runs, tasks and executions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, Task
from app.models import WorkflowRun as WorkflowRunRow
from app.orchestrator.workflow import RunStatus, WorkflowRun
from app.repositories.agent_repository import AgentNotFoundError, AgentRepository
from app.runtimes.base import AgentResultStatus

# Orchestrator states -> values allowed by the DB CHECK constraints.
RUN_STATUS = {
    RunStatus.PENDING: "pending",
    RunStatus.RUNNING: "running",
    RunStatus.COMPLETED: "succeeded",
    RunStatus.FAILED: "failed",
    RunStatus.SKIPPED: "skipped",  # tasks only
}
EXECUTION_STATUS = {
    AgentResultStatus.COMPLETED: "succeeded",
    AgentResultStatus.FAILED: "failed",
    AgentResultStatus.TIMED_OUT: "timed_out",
    AgentResultStatus.STEP_LIMIT_EXCEEDED: "failed",
}


def as_json_object(value: Any) -> dict[str, Any] | None:
    """JSONB output columns hold objects; wrap plain strings and lists."""
    if value is None or isinstance(value, dict):
        return value
    return {"result": value}


class RunStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._agents = AgentRepository(session)

    async def start(
        self, workflow_id: uuid.UUID, run_input: dict[str, Any], requested_by: str | None
    ) -> WorkflowRunRow:
        """Insert and commit the run as running, so it is visible while it executes."""
        row = WorkflowRunRow(
            workflow_id=workflow_id,
            status="running",
            input=run_input,
            requested_by=requested_by,
        )
        self.session.add(row)
        await self.session.commit()
        return row

    async def finish(self, row: WorkflowRunRow, run: WorkflowRun) -> None:
        row.status = RUN_STATUS[run.status]
        row.output = as_json_object(run.output)
        row.error = run.error
        row.started_at = run.started_at
        row.completed_at = run.completed_at

        # Resolve agent refs before adding rows: lookups autoflush, and the
        # models have no relationships to order inserts by.
        refs = [await self._agent_ref(t.agent_id) for t in run.tasks]
        tasks = [
            Task(
                id=uuid.uuid4(),
                run_id=row.id,
                assigned_agent_id=agent_uuid,
                name=t.step_id,
                input=t.input,
                output=as_json_object(t.result.output if t.result else None),
                status=RUN_STATUS[t.status],
            )
            for t, (agent_uuid, _) in zip(run.tasks, refs, strict=True)
        ]
        self.session.add_all(tasks)
        await self.session.flush()

        for task_run, task, (agent_uuid, agent_version) in zip(run.tasks, tasks, refs, strict=True):
            if task_run.started_at is None:
                continue  # skipped, never executed
            result = task_run.result
            self.session.add(
                Execution(
                    task_id=task.id,
                    agent_id=agent_uuid,
                    agent_version=agent_version,
                    status=EXECUTION_STATUS[result.status] if result else "failed",
                    started_at=task_run.started_at,
                    completed_at=task_run.completed_at,
                    token_usage=result.usage.model_dump() if result else None,
                    error=task_run.error,
                    logs=[{"metadata": result.metadata}] if result and result.metadata else None,
                )
            )
        await self.session.commit()

    async def _agent_ref(self, slug: str) -> tuple[uuid.UUID | None, int | None]:
        try:
            agent = await self._agents.get(slug, include_archived=True)
        except AgentNotFoundError:
            return None, None
        return agent.id, agent.current_version
