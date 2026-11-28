"""The policy engine: checked before every agent call, direct or in a workflow.

For each call it collects the enabled policies that apply to the agent (and
the workflow it runs in), then:

1. ``models``: refuses the call when the agent's model is not allowed.
2. ``tools``: removes denied tools from the agent. Denied tools of an MCP
   server the agent was granted as a whole are hidden by the runtime.
3. ``budget``: refuses the call once spend in the period has reached the limit
   (``action: block``), or lets it run with a warning (``action: warn``).

Budgets are checked before the call, from recorded spend, so steps already
running in parallel can take spend somewhat past a limit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.policy.rules import PolicySpec, model_allowed, period_start, tool_denied
from app.runtimes.base import AgentConfig


class PolicyDenied(Exception):
    """A policy refused the agent call."""

    def __init__(self, policy: str, message: str) -> None:
        super().__init__(message)
        self.policy = policy


@dataclass
class PolicyDecision:
    agent: AgentConfig
    applied: list[str] = field(default_factory=list)
    removed_tools: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any] | None:
        """What to keep with the call's record, or None when nothing changed."""
        if not (self.removed_tools or self.warnings or self.agent.blocked_tools):
            return None
        return {
            "applied": self.applied,
            "removed_tools": self.removed_tools,
            "blocked_tools": self.agent.blocked_tools,
            "warnings": self.warnings,
        }


class PolicyStore(Protocol):
    async def active_policies(self) -> list[PolicySpec]: ...

    async def spend(self, scope: str, target: str | None, since: datetime | None) -> Decimal: ...


class PolicyEngine:
    def __init__(self, store: PolicyStore, *, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))

    async def check(self, agent: AgentConfig, *, workflow: str | None = None) -> PolicyDecision:
        """The agent as it may run, or PolicyDenied."""
        policies = [
            p for p in await self.store.active_policies() if p.applies_to(agent.id, workflow)
        ]
        decision = PolicyDecision(agent=agent, applied=[p.name for p in policies])
        if not policies:
            return decision

        for policy in (p for p in policies if p.kind == "models"):
            allowed = policy.rules["allow"]
            if not model_allowed(agent.model, allowed):
                model = f"model '{agent.model}'" if agent.model else "the runtime's default model"
                raise PolicyDenied(
                    policy.name,
                    f"Policy '{policy.name}' does not allow {model} for agent '{agent.id}' "
                    f"(allowed: {', '.join(allowed)})",
                )

        deny = [pattern for p in policies if p.kind == "tools" for pattern in p.rules["deny"]]
        if deny:
            kept = [t for t in agent.tools if not tool_denied(t, deny)]
            decision.removed_tools = [t for t in agent.tools if t not in kept]
            blocked = sorted({*agent.blocked_tools, *deny})
            decision.agent = agent = agent.model_copy(
                update={"tools": kept, "blocked_tools": blocked}
            )

        for policy in (p for p in policies if p.kind == "budget"):
            rules = policy.budget
            since = period_start(rules.period, self.clock())
            spent = await self.store.spend(policy.scope, policy.target, since)
            limit = Decimal(str(rules.limit_usd))
            if spent < limit:
                continue
            period = {"day": "today", "month": "this month", "total": "in total"}[rules.period]
            message = f"Budget '{policy.name}' reached: ${spent:.2f} of ${limit:.2f} {period}"
            if rules.action == "block":
                raise PolicyDenied(policy.name, message)
            decision.warnings.append(message)
        return decision


# --- stores ----------------------------------------------------------------------


class InMemoryPolicyStore:
    """For tests: fixed policies; spend from a dict keyed by (scope, target)."""

    def __init__(
        self,
        policies: list[PolicySpec] | None = None,
        spend: dict[tuple[str, str | None], Decimal] | None = None,
    ) -> None:
        self.policies = list(policies or [])
        self.spent = dict(spend or {})
        self.since: list[datetime | None] = []

    async def active_policies(self) -> list[PolicySpec]:
        return [p for p in self.policies if p.enabled]

    async def spend(self, scope: str, target: str | None, since: datetime | None) -> Decimal:
        self.since.append(since)
        return self.spent.get((scope, target), Decimal(0))


def spend_statement(scope: str, target: str | None, since: datetime | None) -> Any:
    from sqlalchemy import func, select

    from app.models import Agent, Execution, Task, Workflow, WorkflowRun

    stmt = select(func.coalesce(func.sum(Execution.cost_usd), 0))
    if scope == "agent":
        stmt = stmt.join(Agent, Agent.id == Execution.agent_id).where(Agent.slug == target)
    elif scope == "workflow":
        stmt = (
            stmt.join(Task, Task.id == Execution.task_id)
            .join(WorkflowRun, WorkflowRun.id == Task.run_id)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(Workflow.name == target)
        )
    if since is not None:
        stmt = stmt.where(Execution.started_at >= since)
    return stmt


def spec_from_row(row: Any) -> PolicySpec:
    return PolicySpec.model_validate(
        {
            "name": row.name,
            "description": row.description,
            "kind": row.kind,
            "scope": row.scope,
            "target": row.target,
            "rules": row.rules,
            "enabled": row.enabled,
        }
    )


class DbPolicyStore:
    """Policies and spend from PostgreSQL; one short session per call."""

    async def active_policies(self) -> list[PolicySpec]:
        from sqlalchemy import select

        from app.core.database import get_sessionmaker
        from app.models import Policy

        async with get_sessionmaker()() as session:
            rows = await session.scalars(select(Policy).where(Policy.enabled.is_(True)))
            return [spec_from_row(r) for r in rows]

    async def spend(self, scope: str, target: str | None, since: datetime | None) -> Decimal:
        from app.core.database import get_sessionmaker

        async with get_sessionmaker()() as session:
            return Decimal(await session.scalar(spend_statement(scope, target, since)) or 0)
