"""Execution and log endpoint (plan section 8)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.v1.runs import SessionDep, agent_slugs, execution_summary, parse_id
from app.models import Execution, Task

router = APIRouter(prefix="/executions", tags=["executions"])


@router.get("/{execution_id}")
async def get_execution(execution_id: str, session: SessionDep) -> dict[str, Any]:
    """One agent call: status, timing, token usage, error and logs."""
    execution = await session.get(Execution, parse_id(execution_id, "Execution"))
    if execution is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Execution not found: {execution_id}"
        )
    task = await session.get(Task, execution.task_id) if execution.task_id else None
    slugs = await agent_slugs(session, {execution.agent_id})
    return {
        **execution_summary(execution),
        "task_id": str(execution.task_id) if execution.task_id else None,
        "run_id": str(task.run_id) if task else None,
        "step_id": task.name if task else None,
        "agent_id": slugs.get(execution.agent_id) if execution.agent_id else None,
        "agent_version": execution.agent_version,
        "logs": execution.logs or [],
        "links": {
            "task": f"/api/v1/tasks/{execution.task_id}" if execution.task_id else None,
            "run": f"/api/v1/runs/{task.run_id}" if task else None,
        },
    }
