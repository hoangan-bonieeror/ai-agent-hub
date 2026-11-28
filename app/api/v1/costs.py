"""Cost tracking: /api/v1/costs and /api/v1/model-prices

Every agent call is stored as an execution with its token usage, model and
dollar cost (priced when it ran, from ``model_prices``). These endpoints add
the costs up and manage the prices.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import BigInteger, Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import ActorDep
from app.core.database import get_session
from app.models import Agent, AuditLog, Execution, ModelPrice, Task, Workflow, WorkflowRun
from app.policy.pricing import load_price_book

router = APIRouter(tags=["costs"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


# --- summary ---------------------------------------------------------------------


def _tokens(field: str) -> Any:
    return func.coalesce(func.sum(Execution.token_usage[field].astext.cast(BigInteger)), 0)


def _measures() -> list[Any]:
    return [
        func.count(Execution.id).label("executions"),
        func.coalesce(func.sum(Execution.cost_usd), 0).label("cost_usd"),
        func.coalesce(
            func.sum(case((Execution.cost_usd.is_(None), 1), else_=0).cast(Integer)),
            0,
        ).label("unpriced_executions"),
        _tokens("requests").label("requests"),
        _tokens("input_tokens").label("input_tokens"),
        _tokens("output_tokens").label("output_tokens"),
    ]


_FIGURES = (
    "executions",
    "cost_usd",
    "unpriced_executions",
    "requests",
    "input_tokens",
    "output_tokens",
)


def _figures(row: Any) -> dict[str, Any]:
    return {
        "executions": int(row.executions),
        "cost_usd": float(row.cost_usd),
        "unpriced_executions": int(row.unpriced_executions),
        "requests": int(row.requests),
        "input_tokens": int(row.input_tokens),
        "output_tokens": int(row.output_tokens),
    }


def _filtered(stmt: Any, since: datetime, agent: str | None, workflow: str | None) -> Any:
    stmt = (
        stmt.select_from(Execution)
        .outerjoin(Agent, Agent.id == Execution.agent_id)
        .outerjoin(Task, Task.id == Execution.task_id)
        .outerjoin(WorkflowRun, WorkflowRun.id == Task.run_id)
        .outerjoin(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .where(Execution.started_at >= since)
        # Attempts refused or cancelled before reaching a model cost nothing.
        .where(Execution.token_usage.is_not(None))
    )
    if agent is not None:
        stmt = stmt.where(Agent.slug == agent)
    if workflow is not None:
        stmt = stmt.where(Workflow.name == workflow)
    return stmt


@router.get("/costs")
async def cost_summary(
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=366, description="Look back this many days")] = 30,
    agent: Annotated[str | None, Query(description="Only this agent (slug)")] = None,
    workflow: Annotated[str | None, Query(description="Only this workflow (name)")] = None,
) -> dict[str, Any]:
    """Spend and token use over the last ``days`` days (UTC), in total and by
    day, agent, model and workflow. ``workflow: null`` groups direct agent runs.
    Calls whose model has no price count in ``unpriced_executions`` and are
    listed in ``unpriced_models``: add a price, then ``POST /costs/reprice``."""
    now = datetime.now(UTC)
    first_day = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    async def grouped(key: Any) -> list[dict[str, Any]]:
        stmt = _filtered(select(key.label("key"), *_measures()), first_day, agent, workflow)
        rows = (await session.execute(stmt.group_by(key))).all()
        items = [{"key": r.key, **_figures(r)} for r in rows]
        return sorted(items, key=lambda i: (-i["cost_usd"], -i["executions"], str(i["key"])))

    total = (
        await session.execute(_filtered(select(*_measures()), first_day, agent, workflow))
    ).one()

    day = func.date_trunc("day", func.timezone("UTC", Execution.started_at))
    daily = {r.pop("key").date(): r for r in await grouped(day)}
    zero = dict.fromkeys(_FIGURES, 0)
    by_day = [
        {"day": d.isoformat(), **daily.get(d, zero)}
        for d in ((first_day + timedelta(days=i)).date() for i in range(days))
    ]

    by_model = await grouped(Execution.model)
    return {
        "since": first_day,
        "until": now,
        "days": days,
        "total": _figures(total),
        "by_day": by_day,
        "by_agent": await grouped(Agent.slug),
        "by_model": by_model,
        "by_workflow": await grouped(Workflow.name),
        "unpriced_models": [
            {k: m[k] for k in ("key", "unpriced_executions", "input_tokens", "output_tokens")}
            for m in by_model
            if m["unpriced_executions"]
        ],
    }


@router.post("/costs/reprice")
async def reprice(session: SessionDep, actor: ActorDep) -> dict[str, int]:
    """Price the stored calls that have a model but no cost yet, with the
    current prices. Calls already priced keep the price they ran at."""
    prices = await load_price_book(session)
    rows = await session.scalars(
        select(Execution).where(
            Execution.cost_usd.is_(None),
            Execution.model.is_not(None),
            Execution.token_usage.is_not(None),
        )
    )
    updated = 0
    for row in rows:
        cost = prices.cost(row.model, row.token_usage)
        if cost is not None:
            row.cost_usd = cost
            updated += 1
    session.add(
        AuditLog(
            actor=actor,
            action="costs.repriced",
            resource_type="execution",
            meta={"updated": updated},
        )
    )
    await session.commit()
    return {"updated": updated}


# --- prices ----------------------------------------------------------------------


class PriceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ] = Field(description="Model name, or a pattern with * such as gpt-4.1-mini*")
    input_per_mtok: Decimal = Field(
        ge=0, le=100_000, decimal_places=4, description="USD per 1M input tokens"
    )
    output_per_mtok: Decimal = Field(
        ge=0, le=100_000, decimal_places=4, description="USD per 1M output tokens"
    )


def price_view(row: ModelPrice) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "model": row.model,
        "input_per_mtok": float(row.input_per_mtok),
        "output_per_mtok": float(row.output_per_mtok),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _price_audit(actor: str, action: str, row: ModelPrice) -> AuditLog:
    return AuditLog(
        actor=actor,
        action=action,
        resource_type="model_price",
        resource_id=str(row.id),
        meta={
            "model": row.model,
            "input_per_mtok": str(row.input_per_mtok),
            "output_per_mtok": str(row.output_per_mtok),
        },
    )


@router.get("/model-prices")
async def list_prices(session: SessionDep) -> dict[str, Any]:
    rows = await session.scalars(select(ModelPrice).order_by(ModelPrice.model))
    return {"items": [price_view(r) for r in rows]}


@router.put("/model-prices")
async def set_price(body: PriceInput, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    """Add a model's price, or change it. New calls use it; calls already
    stored keep their cost."""
    row = await session.scalar(select(ModelPrice).where(ModelPrice.model == body.model))
    action = "model_price.updated"
    if row is None:
        row = ModelPrice(model=body.model)
        session.add(row)
        action = "model_price.created"
    row.input_per_mtok = body.input_per_mtok
    row.output_per_mtok = body.output_per_mtok
    await session.flush()
    session.add(_price_audit(actor, action, row))
    await session.commit()
    await session.refresh(row)
    return price_view(row)


@router.delete("/model-prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price(price_id: str, session: SessionDep, actor: ActorDep) -> Response:
    try:
        row = await session.get(ModelPrice, uuid.UUID(price_id))
    except ValueError:
        row = None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Price not found: {price_id}")
    session.add(_price_audit(actor, "model_price.deleted", row))
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
