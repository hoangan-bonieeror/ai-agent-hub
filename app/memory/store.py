"""Memory service: notes that outlive a run (plan: Memory).

Two scopes:

- ``agent``: private to one agent (``owner`` = its slug).
- ``shared``: readable by every agent that has memory access.

Agents reach memory through two tools, ``memory_search`` and
``memory_save`` (see :mod:`app.memory.tools`), granted by putting
``memory`` in the agent's ``tools`` list. People manage it through
``/api/v1/memories`` and the dashboard.

Search is PostgreSQL full-text search with the "simple" configuration (no
stemming, so Vietnamese and English both work), plus a substring match so
short or partial words still hit. Embeddings can replace this later
without changing the interface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

Scope = Literal["agent", "shared"]
MAX_CONTENT = 4000


class MemoryItem(BaseModel):
    id: str
    scope: Scope
    owner: str | None
    content: str
    tags: list[str] = Field(default_factory=list)
    created_by: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryStore(Protocol):
    async def search(
        self, query: str, *, agent: str | None, limit: int = 5
    ) -> list[MemoryItem]: ...

    async def save(
        self,
        content: str,
        *,
        scope: Scope,
        owner: str | None,
        tags: list[str] | None = None,
        created_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem: ...


def _visible(item: MemoryItem, agent: str | None, now: datetime) -> bool:
    if item.expires_at is not None and item.expires_at <= now:
        return False
    if agent is None:
        return True
    return item.scope == "shared" or item.owner == agent


class InMemoryMemoryStore:
    """For tests and the in-process demo."""

    def __init__(self) -> None:
        self.items: list[MemoryItem] = []

    async def search(self, query: str, *, agent: str | None, limit: int = 5) -> list[MemoryItem]:
        now = datetime.now(UTC)
        words = [w for w in query.lower().split() if w]
        scored = []
        for item in self.items:
            if not _visible(item, agent, now):
                continue
            text = f"{item.content} {' '.join(item.tags)}".lower()
            score = sum(1 for w in words if w in text)
            if score or not words:
                scored.append((score, item.updated_at, item))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [item for *_, item in scored[:limit]]

    async def save(
        self,
        content: str,
        *,
        scope: Scope,
        owner: str | None,
        tags: list[str] | None = None,
        created_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem:
        now = datetime.now(UTC)
        item = MemoryItem(
            id=str(uuid.uuid4()),
            scope=scope,
            owner=owner if scope == "agent" else None,
            content=content[:MAX_CONTENT],
            tags=tags or [],
            created_by=created_by,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        self.items.append(item)
        return item


def item_from_row(row) -> MemoryItem:
    return MemoryItem(
        id=str(row.id),
        scope=row.scope,
        owner=row.owner,
        content=row.content,
        tags=list(row.tags or []),
        created_by=row.created_by,
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def search_statement(query: str, *, agent: str | None, scope: str | None = None):
    """SELECT for memories matching ``query`` (all when blank), best first."""
    from sqlalchemy import and_, case, func, or_, select

    from app.models import Memory

    stmt = select(Memory).where(or_(Memory.expires_at.is_(None), Memory.expires_at > func.now()))
    if agent is not None:
        stmt = stmt.where(
            or_(Memory.scope == "shared", and_(Memory.scope == "agent", Memory.owner == agent))
        )
    if scope is not None:
        stmt = stmt.where(Memory.scope == scope)
    query = query.strip()
    if not query:
        return stmt.order_by(Memory.updated_at.desc())
    tsquery = func.plainto_tsquery("simple", query)
    vector = func.to_tsvector("simple", Memory.content)
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    substring = Memory.content.ilike(f"%{escaped}%", escape="\\")
    # An exact phrase match ranks above word matches.
    rank = func.ts_rank(vector, tsquery) + case((substring, 1.0), else_=0.0)
    return stmt.where(or_(vector.op("@@")(tsquery), substring)).order_by(
        rank.desc(), Memory.updated_at.desc()
    )


class DbMemoryStore:
    """Memories in PostgreSQL; one short session per call."""

    async def search(self, query: str, *, agent: str | None, limit: int = 5) -> list[MemoryItem]:
        from app.core.database import get_sessionmaker

        async with get_sessionmaker()() as session:
            rows = await session.scalars(search_statement(query, agent=agent).limit(limit))
            return [item_from_row(r) for r in rows]

    async def save(
        self,
        content: str,
        *,
        scope: Scope,
        owner: str | None,
        tags: list[str] | None = None,
        created_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryItem:
        from app.core.database import get_sessionmaker
        from app.models import Memory

        async with get_sessionmaker()() as session:
            row = Memory(
                scope=scope,
                owner=owner if scope == "agent" else None,
                content=content[:MAX_CONTENT],
                tags=tags or [],
                created_by=created_by,
                expires_at=expires_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return item_from_row(row)
