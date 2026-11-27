"""Initial schema: agents, agent_versions, workflows, workflow_runs, tasks,
executions, tools, audit_logs.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, TS, nullable=False, server_default=sa.func.now())


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now())


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


AGENT_STATUSES = ("active", "disabled", "archived")
RUN_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
TASK_STATUSES = ("pending", "queued", "running", "succeeded", "failed", "cancelled", "skipped")
EXECUTION_STATUSES = ("running", "succeeded", "failed", "timed_out", "cancelled")


def upgrade() -> None:
    # gen_random_uuid() is built in from PostgreSQL 13; pgcrypto covers older servers.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "agents",
        _id(),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("runtime", sa.String(50), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("slug", name="uq_agents_slug"),
        sa.CheckConstraint(_in("status", AGENT_STATUSES), name=op.f("ck_agents_status")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name=op.f("ck_agents_slug_format")
        ),
        sa.CheckConstraint("current_version >= 1", name=op.f("ck_agents_current_version_positive")),
    )
    op.create_index("ix_agents_status", "agents", ["status"])

    op.create_table(
        "agent_versions",
        _id(),
        sa.Column("agent_id", UUID, sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", JSONB, nullable=False),
        sa.Column("created_by", sa.String(200)),
        _created_at(),
        # The unique index on (agent_id, version) also serves lookups by agent_id.
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_id_version"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agent_versions_version_positive")),
    )

    op.create_table(
        "workflows",
        _id(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("name", "version", name="uq_workflows_name_version"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_workflows_version_positive")),
    )

    op.create_table(
        "workflow_runs",
        _id(),
        sa.Column(
            "workflow_id",
            UUID,
            sa.ForeignKey("workflows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("input", JSONB, nullable=False, server_default="{}"),
        sa.Column("output", JSONB),
        sa.Column("error", sa.Text()),
        sa.Column("requested_by", sa.String(200)),
        sa.Column("started_at", TS),
        sa.Column("completed_at", TS),
        _created_at(),
        sa.CheckConstraint(_in("status", RUN_STATUSES), name=op.f("ck_workflow_runs_status")),
    )
    op.create_index(
        "ix_workflow_runs_workflow_id_created_at", "workflow_runs", ["workflow_id", "created_at"]
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])

    op.create_table(
        "tasks",
        _id(),
        sa.Column(
            "run_id", UUID, sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("parent_task_id", UUID, sa.ForeignKey("tasks.id", ondelete="CASCADE")),
        sa.Column("assigned_agent_id", UUID, sa.ForeignKey("agents.id", ondelete="RESTRICT")),
        sa.Column("name", sa.String(200)),
        sa.Column("input", JSONB, nullable=False, server_default="{}"),
        sa.Column("output", JSONB),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(_in("status", TASK_STATUSES), name=op.f("ck_tasks_status")),
        sa.CheckConstraint("retry_count >= 0", name=op.f("ck_tasks_retry_count_nonnegative")),
    )
    op.create_index("ix_tasks_run_id", "tasks", ["run_id"])
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])
    op.create_index("ix_tasks_assigned_agent_id", "tasks", ["assigned_agent_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "executions",
        _id(),
        sa.Column("task_id", UUID, sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID, sa.ForeignKey("agents.id", ondelete="RESTRICT")),
        sa.Column("agent_version", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        _created_at("started_at"),
        sa.Column("completed_at", TS),
        sa.Column("token_usage", JSONB),
        sa.Column("error", sa.Text()),
        sa.Column("logs", JSONB),
        sa.CheckConstraint(_in("status", EXECUTION_STATUSES), name=op.f("ck_executions_status")),
    )
    op.create_index("ix_executions_task_id", "executions", ["task_id"])
    op.create_index("ix_executions_agent_id_started_at", "executions", ["agent_id", "started_at"])

    op.create_table(
        "tools",
        _id(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("schema", JSONB, nullable=False, server_default="{}"),
        sa.Column("permission_policy", JSONB, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("name", name="uq_tools_name"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100)),
        sa.Column("timestamp", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id", "timestamp"]
    )
    op.create_index("ix_audit_logs_actor_timestamp", "audit_logs", ["actor", "timestamp"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("tools")
    op.drop_table("executions")
    op.drop_table("tasks")
    op.drop_table("workflow_runs")
    op.drop_table("workflows")
    op.drop_table("agent_versions")
    op.drop_table("agents")
    # pgcrypto is left installed: other objects may depend on it.
