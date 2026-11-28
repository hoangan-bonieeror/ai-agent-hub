import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import created_at_col, updated_at_col, uuid_pk


class Tool(Base):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("name", name="uq_tools_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # JSON Schema of the tool's input parameters.
    schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Deny by default: an empty policy grants nothing (plan section 11).
    permission_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Set for tools discovered on an MCP server (name "mcp:<server>/<tool>").
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
