import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import MEMORY_SCOPES, created_at_col, in_list, updated_at_col, uuid_pk


class Memory(Base):
    """A note kept between runs (plan: Memory service).

    ``scope`` is "agent" (``owner`` is the agent's slug; only that agent
    reads it) or "shared" (``owner`` is NULL; every agent with memory access
    reads it). Searched with PostgreSQL full-text search ("simple"
    configuration: no stemming, so it works for any language).
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(in_list("scope", MEMORY_SCOPES), name="scope"),
        CheckConstraint("(scope = 'agent') = (owner IS NOT NULL)", name="owner_matches_scope"),
        Index("ix_memories_scope_owner", "scope", "owner"),
        Index(
            "ix_memories_content_fts",
            text("to_tsvector('simple', content)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(63))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_by: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
