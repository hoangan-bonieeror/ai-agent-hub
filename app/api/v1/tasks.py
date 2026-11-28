"""Task status endpoint (plan section 8)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.v1.runs import SessionDep, agent_slugs, parse_id, run_links, stored_task_view
from app.models import Execution, Task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
async def get_task(task_id: str, session: SessionDep) -> dict[str, Any]:
    """One workflow step in one run: status, input, output and its executions
    (a step a reviewer sent back has one execution per attempt)."""
    task = await session.get(Task, parse_id(task_id, "Task"))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Task not found: {task_id}")
    executions = list(
        (
            await session.scalars(
                select(Execution).where(Execution.task_id == task.id).order_by(Execution.started_at)
            )
        ).all()
    )
    slugs = await agent_slugs(session, {task.assigned_agent_id})
    view = stored_task_view(task, slugs, executions)
    view["input"] = task.input
    view["links"] = {"run": run_links(str(task.run_id))["self"]}
    return view
