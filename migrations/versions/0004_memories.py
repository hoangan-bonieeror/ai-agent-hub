"""Memory service: notes agents keep between runs (Phase 3).

Additive only: one new table with a full-text index.

Revision ID: 0004_memories
Revises: 0003_mcp_servers
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_memories"
down_revision: str | None = "0003_mcp_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("owner", sa.String(63)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(200)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("scope IN ('agent', 'shared')", name=op.f("ck_memories_scope")),
        sa.CheckConstraint(
            "(scope = 'agent') = (owner IS NOT NULL)", name=op.f("ck_memories_owner_matches_scope")
        ),
    )
    op.create_index("ix_memories_scope_owner", "memories", ["scope", "owner"])
    op.execute(
        "CREATE INDEX ix_memories_content_fts ON memories "
        "USING gin (to_tsvector('simple', content))"
    )


def downgrade() -> None:
    op.drop_table("memories")
