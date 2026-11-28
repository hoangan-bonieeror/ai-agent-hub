"""MCP servers, and a link from discovered tools to their server (Phase 3).

Additive only: a new table and a nullable column.

Revision ID: 0003_mcp_servers
Revises: 0002_task_graph_columns
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_mcp_servers"
down_revision: str | None = "0002_task_graph_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MCP_TRANSPORTS = ("stdio", "streamable_http", "sse")


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("transport", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("allowed_tools", postgresql.JSONB()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("name", name="uq_mcp_servers_name"),
        sa.CheckConstraint(
            f"transport IN ({', '.join(repr(t) for t in MCP_TRANSPORTS)})",
            name=op.f("ck_mcp_servers_transport"),
        ),
    )
    op.add_column(
        "tools",
        sa.Column(
            "mcp_server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "mcp_servers.id", ondelete="CASCADE", name="fk_tools_mcp_server_id_mcp_servers"
            ),
        ),
    )
    op.create_index("ix_tools_mcp_server_id", "tools", ["mcp_server_id"])


def downgrade() -> None:
    op.drop_index("ix_tools_mcp_server_id", table_name="tools")
    op.drop_column("tools", "mcp_server_id")
    op.drop_table("mcp_servers")
