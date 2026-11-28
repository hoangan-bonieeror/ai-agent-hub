import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import (
    EXECUTION_STATUSES,
    RUN_STATUSES,
    STEP_KINDS,
    TASK_STATUSES,
    created_at_col,
    in_list,
    updated_at_col,
    uuid_pk,
)


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_workflows_name_version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(in_list("status", RUN_STATUSES), name="status"),
        Index("ix_workflow_runs_workflow_id_created_at", "workflow_id", "created_at"),
        Index("ix_workflow_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # RESTRICT: runs are history; a workflow with runs should be disabled, not deleted.
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(in_list("status", TASK_STATUSES), name="status"),
        CheckConstraint(in_list("kind", STEP_KINDS), name="kind"),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        Index("ix_tasks_run_id", "run_id"),
        Index("ix_tasks_parent_task_id", "parent_task_id"),
        Index("ix_tasks_assigned_agent_id", "assigned_agent_id"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE")
    )
    # Agents are archived rather than deleted, so RESTRICT protects history.
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT")
    )
    #: Workflow step id.
    name: Mapped[str | None] = mapped_column(String(200))
    #: Step kind: agent, review or aggregate.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, server_default="agent")
    #: Step ids (names) of the tasks in the same run this one waited for.
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Execution(Base):
    """One agent call: an attempt at a workflow task (a retried task has
    several), or a direct run (``POST /agents/{id}/run``, no task)."""

    __tablename__ = "executions"
    __table_args__ = (
        CheckConstraint(in_list("status", EXECUTION_STATUSES), name="status"),
        Index("ix_executions_task_id", "task_id"),
        Index("ix_executions_agent_id_started_at", "agent_id", "started_at"),
        Index("ix_executions_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT")
    )
    agent_version: Mapped[int | None] = mapped_column(Integer)
    #: Model the call used, as the runtime reported it.
    model: Mapped[str | None] = mapped_column(String(200))
    #: Price of the call in US dollars at the time it ran; None when the
    #: model had no price (see model_prices).
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    started_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # none_as_null: a call that never reached a model stores SQL NULL, not JSON null.
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[str | None] = mapped_column(Text)
    logs: Mapped[list[Any] | None] = mapped_column(JSONB)
