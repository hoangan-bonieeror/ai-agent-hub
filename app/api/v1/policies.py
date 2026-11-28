"""Policy engine: /api/v1/policies and the audit log, /api/v1/audit-logs

Policies are checked before every agent call (see app/policy/engine.py).
Every change to a policy is written to the audit log.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import ActorDep
from app.core.database import get_session
from app.models import AuditLog, Policy
from app.orchestrator.dependencies import get_policy_engine
from app.policy.engine import PolicyDenied, PolicyEngine, spec_from_row, spend_statement
from app.policy.rules import PolicySpec, period_start
from app.registry.agent_registry import AgentRegistry
from app.repositories.agent_repository import AgentNotFoundError

router = APIRouter(tags=["policies"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class PolicyUpdate(BaseModel):
    """PATCH body: only the fields sent change. ``kind`` is fixed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    scope: str | None = None
    target: str | None = None
    rules: dict[str, Any] | None = None
    enabled: bool | None = None


class PolicyCheck(BaseModel):
    agent: str = Field(description="Agent slug")
    workflow: str | None = Field(default=None, description="Workflow name, if run in one")


def _invalid(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", *e["loc"]], "msg": e["msg"]} for e in exc.errors()],
    )


async def budget_status(session: AsyncSession, spec: PolicySpec) -> dict[str, Any]:
    rules = spec.budget
    since = period_start(rules.period)
    spent = float(await session.scalar(spend_statement(spec.scope, spec.target, since)) or 0)
    return {
        "spent_usd": spent,
        "limit_usd": rules.limit_usd,
        "since": since,
        "used": spent / rules.limit_usd,
        "reached": spent >= rules.limit_usd,
    }


async def policy_view(session: AsyncSession, row: Policy) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "kind": row.kind,
        "scope": row.scope,
        "target": row.target,
        "rules": row.rules,
        "enabled": row.enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if row.kind == "budget":
        view["status"] = await budget_status(session, spec_from_row(row))
    return view


async def load_policy(session: AsyncSession, policy_id: str) -> Policy:
    try:
        row = await session.get(Policy, uuid.UUID(policy_id))
    except ValueError:
        row = None
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Policy not found: {policy_id}")
    return row


def audit(session: AsyncSession, actor: str, action: str, row: Policy, **meta: Any) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type="policy",
            resource_id=str(row.id),
            meta={"name": row.name, "kind": row.kind, **meta},
        )
    )


async def _save(session: AsyncSession, row: Policy) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"A policy named '{row.name}' already exists"
        ) from exc


@router.get("/policies")
async def list_policies(session: SessionDep) -> dict[str, Any]:
    """Every policy. Budgets include what has been spent in the current period."""
    rows = await session.scalars(select(Policy).order_by(Policy.kind, Policy.name))
    return {"items": [await policy_view(session, r) for r in rows]}


@router.post("/policies", status_code=status.HTTP_201_CREATED)
async def create_policy(body: PolicySpec, session: SessionDep, actor: ActorDep) -> dict[str, Any]:
    row = Policy(**body.model_dump())
    session.add(row)
    await _save(session, row)
    audit(
        session, actor, "policy.created", row, rules=row.rules, scope=row.scope, target=row.target
    )
    await session.commit()
    await session.refresh(row)
    return await policy_view(session, row)


@router.get("/policies/{policy_id}")
async def get_policy(policy_id: str, session: SessionDep) -> dict[str, Any]:
    return await policy_view(session, await load_policy(session, policy_id))


@router.patch("/policies/{policy_id}")
async def update_policy(
    policy_id: str, body: PolicyUpdate, session: SessionDep, actor: ActorDep
) -> dict[str, Any]:
    row = await load_policy(session, policy_id)
    current = spec_from_row(row).model_dump()
    changes = body.model_dump(exclude_unset=True)
    if changes.get("scope") == "global":
        changes.setdefault("target", None)
    try:
        spec = PolicySpec.model_validate({**current, **changes})
    except ValidationError as exc:
        raise _invalid(exc) from exc
    before = {k: current[k] for k in changes}
    for field, value in spec.model_dump().items():
        setattr(row, field, value)
    await _save(session, row)
    audit(
        session,
        actor,
        "policy.updated",
        row,
        before=before,
        after={k: getattr(row, k) for k in changes},
    )
    await session.commit()
    await session.refresh(row)
    return await policy_view(session, row)


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: str, session: SessionDep, actor: ActorDep) -> Response:
    row = await load_policy(session, policy_id)
    audit(
        session, actor, "policy.deleted", row, rules=row.rules, scope=row.scope, target=row.target
    )
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/policies/check")
async def check_policies(
    body: PolicyCheck,
    session: SessionDep,
    engine: Annotated[PolicyEngine, Depends(get_policy_engine)],
) -> dict[str, Any]:
    """Dry run: what the policies would do to the agent's next call, without
    running it."""
    try:
        agent = await AgentRegistry(session).get_agent(body.agent)
    except AgentNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {body.agent}"
        ) from exc
    try:
        decision = await engine.check(agent, workflow=body.workflow)
    except PolicyDenied as exc:
        return {"allowed": False, "policy": exc.policy, "reason": str(exc)}
    return {
        "allowed": True,
        "applied": decision.applied,
        "tools": decision.agent.tools,
        "removed_tools": decision.removed_tools,
        "blocked_tools": decision.agent.blocked_tools,
        "warnings": decision.warnings,
    }


@router.get("/audit-logs")
async def list_audit_logs(
    session: SessionDep,
    resource_type: Annotated[str | None, Query(description="e.g. policy, agent")] = None,
    resource_id: str | None = None,
    actor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Who changed what, newest first."""
    stmt = select(AuditLog)
    if resource_type is not None:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if actor is not None:
        stmt = stmt.where(AuditLog.actor == actor)
    rows = await session.scalars(
        stmt.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    )
    return {
        "items": [
            {
                "id": r.id,
                "actor": r.actor,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "timestamp": r.timestamp,
                "metadata": r.meta,
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }
