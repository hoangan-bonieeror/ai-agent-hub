"""Workflow endpoints (plan section 8)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import ActorDep
from app.core.database import get_session
from app.models import Workflow
from app.orchestrator import AgentOrchestrator, WorkflowDefinition
from app.orchestrator.dependencies import get_runtime_registry
from app.orchestrator.persistence import RUN_STATUS, RunStore, as_json_object
from app.registry.agent_registry import AgentRegistry
from app.runtimes import RuntimeRegistry

router = APIRouter(prefix="/workflows", tags=["workflows"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RuntimesDep = Annotated[RuntimeRegistry, Depends(get_runtime_registry)]


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


def _workflow_read(row: Workflow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "version": row.version,
        "enabled": row.enabled,
        "definition": row.definition,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow(body: WorkflowDefinition, session: SessionDep) -> dict[str, Any]:
    """Saves a workflow definition. Posting an existing name adds a new version."""
    latest = await session.scalar(
        select(func.max(Workflow.version)).where(Workflow.name == body.name)
    )
    row = Workflow(
        name=body.name,
        description=body.description or None,
        version=(latest or 0) + 1,
        definition=body.model_dump(mode="json", exclude={"version"}),
    )
    session.add(row)
    await session.commit()
    return _workflow_read(row)


async def _load_workflow(session: AsyncSession, workflow_id: str) -> Workflow:
    """`workflow_id` is a workflow UUID, or a name (its newest enabled version)."""
    try:
        stmt = select(Workflow).where(Workflow.id == uuid.UUID(workflow_id))
    except ValueError:
        stmt = (
            select(Workflow)
            .where(Workflow.name == workflow_id, Workflow.enabled.is_(True))
            .order_by(Workflow.version.desc())
            .limit(1)
        )
    row = await session.scalar(stmt)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Workflow not found: {workflow_id}")
    if not row.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Workflow is disabled: {workflow_id}")
    return row


@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    body: WorkflowRunRequest,
    session: SessionDep,
    runtimes: RuntimesDep,
    actor: ActorDep,
) -> dict[str, Any]:
    """Runs the workflow's steps in order and returns when it finishes.

    A failed step still returns 200 with ``status: "failed"``: the run itself
    was recorded. Progress streaming (SSE) and background runs come in Phase 2.
    """
    row = await _load_workflow(session, workflow_id)
    definition = WorkflowDefinition.model_validate({**row.definition, "version": row.version})

    store = RunStore(session)
    run_row = await store.start(row.id, body.input, requested_by=actor)
    orchestrator = AgentOrchestrator(AgentRegistry(session), runtimes)
    try:
        run = await orchestrator.run_workflow(definition, body.input)
    except Exception:
        # The orchestrator records agent failures itself; this is a bug path.
        # Don't leave the committed run row stuck at "running".
        await session.rollback()
        run_row.status = "failed"
        run_row.error = "Internal error while running the workflow"
        await session.commit()
        raise
    await store.finish(run_row, run)

    return {
        "run_id": str(run_row.id),
        "workflow": {"id": str(row.id), "name": row.name, "version": row.version},
        "status": RUN_STATUS[run.status],
        "output": run.output,
        "error": run.error,
        "usage": run.usage.model_dump(),
        "tasks": [
            {
                "step_id": t.step_id,
                "agent_id": t.agent_id,
                "status": RUN_STATUS[t.status],
                "output": as_json_object(t.result.output) if t.result else None,
                "error": t.error,
                "duration_ms": t.result.duration_ms if t.result else None,
            }
            for t in run.tasks
        ],
    }
