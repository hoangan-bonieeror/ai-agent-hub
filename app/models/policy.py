import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models._common import (
    POLICY_KINDS,
    POLICY_SCOPES,
    created_at_col,
    in_list,
    updated_at_col,
    uuid_pk,
)


class ModelPrice(Base):
    """What one model costs, in US dollars per million tokens.

    ``model`` is an exact model name or a pattern with ``*`` (``gpt-4.1-mini*``);
    an exact name wins over a pattern, a longer pattern over a shorter one.
    """

    __tablename__ = "model_prices"
    __table_args__ = (
        UniqueConstraint("model", name="uq_model_prices_model"),
        CheckConstraint("input_per_mtok >= 0 AND output_per_mtok >= 0", name="nonnegative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    output_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class Policy(Base):
    """A rule checked before every agent call (see app/policy/engine.py).

    ``kind`` decides the shape of ``rules``: budget, tools or models.
    ``scope`` is global, or one agent / workflow named by ``target``.
    """

    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("name", name="uq_policies_name"),
        CheckConstraint(in_list("kind", POLICY_KINDS), name="kind"),
        CheckConstraint(in_list("scope", POLICY_SCOPES), name="scope"),
        CheckConstraint("(scope = 'global') = (target IS NULL)", name="target_matches_scope"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200))
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
