"""Cost tracking and the policy engine (Phase 3).

* model_prices: dollars per million input / output tokens, per model.
* policies: budgets, tool rules and model rules checked before each agent call.
* executions: the model and cost of each call; task_id becomes optional so
  direct agent runs (POST /agents/{id}/run) are recorded too.

Revision ID: 0005_costs_policies
Revises: 0004_memories
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_costs_policies"
down_revision: str | None = "0004_memories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "model_prices",
        _id(),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("input_per_mtok", sa.Numeric(12, 4), nullable=False),
        sa.Column("output_per_mtok", sa.Numeric(12, 4), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("model", name=op.f("uq_model_prices_model")),
        sa.CheckConstraint(
            "input_per_mtok >= 0 AND output_per_mtok >= 0",
            name=op.f("ck_model_prices_nonnegative"),
        ),
    )
    op.create_table(
        "policies",
        _id(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("target", sa.String(200)),
        sa.Column("rules", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        *_timestamps(),
        sa.UniqueConstraint("name", name=op.f("uq_policies_name")),
        sa.CheckConstraint("kind IN ('budget', 'tools', 'models')", name=op.f("ck_policies_kind")),
        sa.CheckConstraint(
            "scope IN ('global', 'agent', 'workflow')", name=op.f("ck_policies_scope")
        ),
        sa.CheckConstraint(
            "(scope = 'global') = (target IS NULL)", name=op.f("ck_policies_target_matches_scope")
        ),
    )
    op.add_column("executions", sa.Column("model", sa.String(200)))
    op.add_column("executions", sa.Column("cost_usd", sa.Numeric(14, 6)))
    op.alter_column("executions", "task_id", nullable=True)
    op.create_index("ix_executions_started_at", "executions", ["started_at"])
    # Attempts that never reached a model were stored as JSON null.
    op.execute("UPDATE executions SET token_usage = NULL WHERE token_usage = 'null'::jsonb")


def downgrade() -> None:
    op.drop_index("ix_executions_started_at", table_name="executions")
    # Direct runs have no task; they cannot be kept under the old NOT NULL.
    op.execute("DELETE FROM executions WHERE task_id IS NULL")
    op.alter_column("executions", "task_id", nullable=False)
    op.drop_column("executions", "cost_usd")
    op.drop_column("executions", "model")
    op.drop_table("policies")
    op.drop_table("model_prices")
