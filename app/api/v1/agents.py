"""Agent Registry API: /api/v1/agents"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.orchestrator import AgentOrchestrator, AgentUnavailableError
from app.orchestrator.dependencies import get_policy_engine, get_runtime_registry
from app.orchestrator.persistence import direct_execution
from app.policy.engine import PolicyDenied, PolicyEngine
from app.policy.pricing import load_price_book
from app.registry.agent_registry import AgentRegistry
from app.repositories.agent_repository import (
    AgentConflictError,
    AgentNotFoundError,
    AgentRepository,
    to_read_dict,
)
from app.runtimes import RuntimeNotFoundError, RuntimeRegistry
from app.schemas.agent import (
    AgentCreate,
    AgentList,
    AgentRead,
    AgentUpdate,
    AgentVersionRead,
)

router = APIRouter(prefix="/agents", tags=["agents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_actor(
    x_actor: Annotated[str | None, Header(max_length=200)] = None,
) -> str:
    # Placeholder until auth lands (plan section 11): the caller names itself.
    # Replace with the authenticated principal; do not trust this for access control.
    return x_actor or "anonymous"


ActorDep = Annotated[str, Depends(get_actor)]


def _not_found(exc: AgentNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {exc.args[0]}")


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, session: SessionDep, actor: ActorDep) -> dict:
    repo = AgentRepository(session)
    try:
        agent = await repo.create(body, actor=actor)
    except AgentConflictError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return to_read_dict(agent)


@router.get("", response_model=AgentList)
async def list_agents(
    session: SessionDep,
    status_: Annotated[
        Literal["active", "disabled", "archived"] | None,
        Query(alias="status", description="Defaults to every status except archived."),
    ] = None,
    runtime: str | None = None,
    capability: str | None = None,
    tool: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    agents, total = await AgentRepository(session).list_agents(
        status=status_,
        runtime=runtime,
        capability=capability,
        tool=tool,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [to_read_dict(a) for a in agents],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(agent_id: str, session: SessionDep) -> dict:
    """`agent_id` is the agent's UUID or its slug."""
    try:
        return to_read_dict(await AgentRepository(session).get(agent_id))
    except AgentNotFoundError as exc:
        raise _not_found(exc) from exc


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: str, body: AgentUpdate, session: SessionDep, actor: ActorDep
) -> dict:
    """Changing any definition field records a new version; changing only
    `status` does not."""
    repo = AgentRepository(session)
    try:
        agent = await repo.update(agent_id, body, actor=actor)
    except AgentNotFoundError as exc:
        await session.rollback()
        raise _not_found(exc) from exc
    except AgentConflictError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return to_read_dict(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_agent(agent_id: str, session: SessionDep, actor: ActorDep) -> Response:
    """Archives the agent (soft delete). It disappears from lists and lookups,
    but its versions and run history are kept and its slug stays reserved."""
    try:
        await AgentRepository(session).archive(agent_id, actor=actor)
    except AgentNotFoundError as exc:
        await session.rollback()
        raise _not_found(exc) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{agent_id}/versions", response_model=list[AgentVersionRead])
async def list_agent_versions(agent_id: str, session: SessionDep) -> list:
    """Newest first. Works for archived agents too."""
    try:
        return await AgentRepository(session).list_versions(agent_id)
    except AgentNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/{agent_id}/versions/{version}", response_model=AgentVersionRead)
async def get_agent_version(agent_id: str, version: int, session: SessionDep):
    try:
        return await AgentRepository(session).get_version(agent_id, version)
    except AgentNotFoundError as exc:
        raise _not_found(exc) from exc


class AgentRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=50_000, description="What the agent should do.")
    input: dict[str, Any] = Field(default_factory=dict, description="Extra context data.")


@router.post("/{agent_id}/run")
async def run_agent(
    agent_id: str,
    body: AgentRunRequest,
    session: SessionDep,
    runtimes: Annotated[RuntimeRegistry, Depends(get_runtime_registry)],
    policy: Annotated[PolicyEngine, Depends(get_policy_engine)],
) -> dict[str, Any]:
    """Runs one agent directly and waits for its result. Agent failures
    (timeout, step limit, model error) return 200 with a non-completed status.
    A policy that refuses the call (budget reached, model not allowed) returns 403.
    The call is recorded as an execution, with its cost."""
    orchestrator = AgentOrchestrator(AgentRegistry(session), runtimes, policy=policy)
    try:
        result = await orchestrator.execute_task(agent_id, {"task": body.task, **body.input})
    except AgentNotFoundError as exc:
        raise _not_found(exc) from exc
    except AgentUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except PolicyDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    agent = await AgentRepository(session).get(agent_id, include_archived=True)
    execution = direct_execution(
        result,
        agent_id=agent.id,
        agent_version=agent.current_version,
        prices=await load_price_book(session),
    )
    session.add(execution)
    await session.commit()
    return {
        **result.model_dump(mode="json"),
        "execution_id": str(execution.id),
        "cost_usd": None if execution.cost_usd is None else float(execution.cost_usd),
    }
