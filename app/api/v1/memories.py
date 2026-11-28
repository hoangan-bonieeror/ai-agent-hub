"""Memory service API: /api/v1/memories"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import ActorDep
from app.core.database import get_session
from app.memory.store import MAX_CONTENT, item_from_row, search_statement
from app.models import Agent, AuditLog, Memory

router = APIRouter(prefix="/memories", tags=["memory"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Content = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CONTENT)
]


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Content
    scope: Literal["agent", "shared"] = "shared"
    owner: str | None = Field(default=None, description="Agent slug; required for scope 'agent'.")
    tags: list[Tag] = Field(default_factory=list, max_length=10)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _owner(self) -> MemoryCreate:
        if self.scope == "agent" and not self.owner:
            raise ValueError("An agent memory needs an owner (the agent's slug)")
        if self.scope == "shared" and self.owner:
            raise ValueError("A shared memory has no owner")
        return self


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Content | None = None
    tags: list[Tag] | None = Field(default=None, max_length=10)
    expires_at: datetime | None = None


def view(row: Memory) -> dict[str, Any]:
    return item_from_row(row).model_dump(mode="json")


async def load(session: AsyncSession, memory_id: str) -> Memory:
    try:
        row = await session.get(Memory, uuid.UUID(memory_id))
    except ValueError:
        row = None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Memory not found: {memory_id}")
    return row


def audit(session: AsyncSession, actor: str, action: str, row: Memory) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type="memory",
            resource_id=str(row.id),
            meta={"scope": row.scope, "owner": row.owner},
        )
    )


@router.get("")
async def list_memories(
    session: SessionDep,
    q: Annotated[str, Query(max_length=500, description="Full-text search")] = "",
    agent: Annotated[
        str | None,
        Query(description="Only what this agent can read: its own and shared memories"),
    ] = None,
    scope: Literal["agent", "shared"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Memories that have not expired, best match first (newest first without ``q``)."""
    stmt = search_statement(q, agent=agent, scope=scope)
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = await session.scalars(stmt.limit(limit).offset(offset))
    return {"items": [view(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_memory(body: MemoryCreate, session: SessionDep, actor: ActorDep) -> dict:
    if body.owner is not None:
        exists = await session.scalar(select(Agent.id).where(Agent.slug == body.owner))
        if exists is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"No agent with slug '{body.owner}'"
            )
    row = Memory(
        scope=body.scope,
        owner=body.owner,
        content=body.content,
        tags=body.tags,
        expires_at=body.expires_at,
        created_by=actor,
    )
    session.add(row)
    await session.flush()
    audit(session, actor, "memory.created", row)
    await session.commit()
    await session.refresh(row)
    return view(row)


@router.get("/{memory_id}")
async def get_memory(memory_id: str, session: SessionDep) -> dict:
    return view(await load(session, memory_id))


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: str, body: MemoryUpdate, session: SessionDep, actor: ActorDep
) -> dict:
    row = await load(session, memory_id)
    changes = body.model_dump(exclude_unset=True)
    if "content" in changes:
        if changes["content"] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="content cannot be null"
            )
        row.content = changes["content"]
    if "tags" in changes:
        row.tags = changes["tags"] or []
    if "expires_at" in changes:
        row.expires_at = changes["expires_at"]
    audit(session, actor, "memory.updated", row)
    await session.commit()
    await session.refresh(row)
    return view(row)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, session: SessionDep, actor: ActorDep) -> Response:
    row = await load(session, memory_id)
    audit(session, actor, "memory.deleted", row)
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
