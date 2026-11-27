"""Agent Registry API: /api/v1/agents"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._stub import not_implemented
from app.core.database import get_session
from app.repositories.agent_repository import (
    AgentConflictError,
    AgentNotFoundError,
    AgentRepository,
    to_read_dict,
)
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


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise not_implemented("Run agent")
