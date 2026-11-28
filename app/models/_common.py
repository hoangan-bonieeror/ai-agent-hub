"""Shared column helpers for ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at_col() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# Allowed status values. Stored as text + CHECK constraint (not PG enums) so new
# values only need a constraint swap, not an ALTER TYPE.
AGENT_STATUSES = ("active", "disabled", "archived")
RUN_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
TASK_STATUSES = ("pending", "queued", "running", "succeeded", "failed", "cancelled", "skipped")
EXECUTION_STATUSES = ("running", "succeeded", "failed", "timed_out", "cancelled")
STEP_KINDS = ("agent", "review", "aggregate")
MCP_TRANSPORTS = ("stdio", "streamable_http", "sse")


def in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"
