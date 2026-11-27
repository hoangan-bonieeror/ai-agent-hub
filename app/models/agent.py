import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models._common import (
    AGENT_STATUSES,
    created_at_col,
    in_list,
    updated_at_col,
    uuid_pk,
)


class Agent(Base):
    """An agent definition. Behaviour lives in `config` (instructions, tools,
    capabilities, limits); columns hold what we filter or join on."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_agents_slug"),
        CheckConstraint(in_list("status", AGENT_STATUSES), name="status"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name="slug_format"),
        CheckConstraint("current_version >= 1", name="current_version_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    runtime: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active", index=True
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", order_by="AgentVersion.version", passive_deletes=True
    )


class AgentVersion(Base):
    """Immutable snapshot of an agent's full definition at each change."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_id_version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_col()

    agent: Mapped[Agent] = relationship(back_populates="versions")
