import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import MCP_TRANSPORTS, created_at_col, in_list, updated_at_col, uuid_pk


class McpServer(Base):
    """A Model Context Protocol server whose tools agents can be granted.

    ``config`` holds how to reach it (command/args for stdio, url for HTTP)
    and the *names* of environment variables to pass along; secret values
    never live in this row (plan section 5).
    """

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("name", name="uq_mcp_servers_name"),
        CheckConstraint(in_list("transport", MCP_TRANSPORTS), name="transport"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(63), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Tools of this server that agents may be granted. NULL: every tool it lists.
    allowed_tools: Mapped[list[str] | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
