"""Task graph columns for Phase 2: step kind, dependencies and error.

Additive only: nullable or defaulted columns, no rewrite of existing rows
beyond the defaults.

Revision ID: 0002_task_graph_columns
Revises: 0001_initial_schema
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_task_graph_columns"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STEP_KINDS = ("agent", "review", "aggregate")


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("kind", sa.String(20), nullable=False, server_default="agent")
    )
    op.add_column(
        "tasks",
        sa.Column("depends_on", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("tasks", sa.Column("error", sa.Text()))
    op.create_check_constraint(
        op.f("ck_tasks_kind"),
        "tasks", f"kind IN ({', '.join(repr(k) for k in STEP_KINDS)})"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tasks_kind"), "tasks", type_="check")
    op.drop_column("tasks", "error")
    op.drop_column("tasks", "depends_on")
    op.drop_column("tasks", "kind")
