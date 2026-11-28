"""Workflow endpoints (plan section 8)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import ActorDep
from app.api.v1.runs import ManagerDep, run_view
from app.core.config import get_settings
from app.core.database import get_session, get_sessionmaker
from app.models import Workflow
from app.orchestrator import WorkflowDefinition
from app.orchestrator.dependencies import get_policy_engine, get_runtime_registry
from app.policy.engine import PolicyEngine
from app.runtimes import RuntimeRegistry

router = APIRouter(prefix="/workflows", tags=["workflows"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RuntimesDep = Annotated[RuntimeRegistry, Depends(get_runtime_registry)]


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    wait: bool = Field(
        default=True,
        description="Wait for the run to finish. false: return 202 at once and stream progress.",
    )


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


@router.get("")
async def list_workflows(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Every stored workflow version, by name then newest version first."""
    rows = await session.scalars(
        select(Workflow)
        .order_by(Workflow.name, Workflow.version.desc())
        .limit(limit)
        .offset(offset)
    )
    return {"items": [_workflow_read(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, session: SessionDep) -> dict[str, Any]:
    """A workflow by UUID, or by name (its newest enabled version)."""
    return _workflow_read(await _load_workflow(session, workflow_id, allow_disabled=True))


async def _load_workflow(
    session: AsyncSession, workflow_id: str, *, allow_disabled: bool = False
) -> Workflow:
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
    if not row.enabled and not allow_disabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Workflow is disabled: {workflow_id}")
    return row


@router.post(
    "/{workflow_id}/run",
    responses={202: {"description": "Started in the background (wait=false)"}},
)
async def run_workflow(
    workflow_id: str,
    body: WorkflowRunRequest,
    response: Response,
    session: SessionDep,
    runtimes: RuntimesDep,
    manager: ManagerDep,
    actor: ActorDep,
    policy: Annotated[PolicyEngine, Depends(get_policy_engine)],
) -> dict[str, Any]:
    """Runs the workflow. Independent steps run in parallel.

    With ``wait`` (the default) the call returns when the run finishes. A
    failed step still returns 200 with ``status: "failed"``: the run itself
    was recorded. With ``"wait": false`` it returns 202 at once; follow the
    run at ``links.events`` (Server-Sent Events) or ``links.self``.
    """
    row = await _load_workflow(session, workflow_id)
    definition = WorkflowDefinition.model_validate({**row.definition, "version": row.version})
    workflow = {"id": str(row.id), "name": row.name, "version": row.version}
    await session.close()  # don't hold a connection for the length of the run

    run = await manager.start(
        sessionmaker=get_sessionmaker(),
        workflow_id=row.id,
        definition=definition,
        workflow_input=body.input,
        runtimes=runtimes,
        requested_by=actor,
        max_parallel=get_settings().workflow_max_parallel,
        wait=body.wait,
        policy=policy,
    )
    if not body.wait:
        response.status_code = status.HTTP_202_ACCEPTED
    return run_view(run, workflow)
