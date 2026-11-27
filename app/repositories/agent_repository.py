"""Data access and business rules for agents. Callers own the transaction:
methods flush but never commit."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentVersion, AuditLog
from app.schemas.agent import (
    CONFIG_FIELDS,
    DEFINITION_FIELDS,
    AgentCreate,
    AgentLimits,
    AgentUpdate,
)


class AgentNotFoundError(KeyError):
    """No agent with that id or slug (archived agents count as missing)."""


class AgentConflictError(Exception):
    """Slug already taken, or an expected_version check failed."""


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def snapshot(agent: Agent) -> dict[str, Any]:
    """The full versioned definition of an agent."""
    return {
        "slug": agent.slug,
        "name": agent.name,
        "description": agent.description,
        "runtime": agent.runtime,
        "model": agent.model,
        **agent.config,
    }


def to_read_dict(agent: Agent) -> dict[str, Any]:
    cfg = agent.config or {}
    return {
        "id": agent.id,
        "slug": agent.slug,
        "name": agent.name,
        "description": agent.description,
        "runtime": agent.runtime,
        "model": agent.model,
        "instructions": cfg.get("instructions", ""),
        "tools": cfg.get("tools", []),
        "capabilities": cfg.get("capabilities", []),
        "limits": AgentLimits(**cfg.get("limits", {})),
        "status": agent.status,
        "enabled": agent.status == "active",
        "current_version": agent.current_version,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
    }


class AgentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(
        self, id_or_slug: str, *, include_archived: bool = False, for_update: bool = False
    ) -> Agent:
        as_uuid = _parse_uuid(id_or_slug)
        stmt = select(Agent).where(
            Agent.id == as_uuid if as_uuid is not None else Agent.slug == id_or_slug
        )
        if not include_archived:
            stmt = stmt.where(Agent.status != "archived")
        if for_update:
            stmt = stmt.with_for_update()
        agent = (await self.session.execute(stmt)).scalar_one_or_none()
        if agent is None:
            raise AgentNotFoundError(id_or_slug)
        return agent

    async def list_agents(
        self,
        *,
        status: str | None = None,
        runtime: str | None = None,
        capability: str | None = None,
        tool: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Agent], int]:
        stmt = select(Agent)
        if status is not None:
            stmt = stmt.where(Agent.status == status)
        else:
            stmt = stmt.where(Agent.status != "archived")
        if runtime is not None:
            stmt = stmt.where(Agent.runtime == runtime)
        if capability is not None:
            stmt = stmt.where(Agent.config["capabilities"].contains([capability]))
        if tool is not None:
            stmt = stmt.where(Agent.config["tools"].contains([tool]))

        total = (
            await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                stmt.order_by(Agent.created_at, Agent.id).limit(limit).offset(offset)
            )
        ).scalars()
        return list(rows), total

    async def create(self, data: AgentCreate, *, actor: str) -> Agent:
        agent = Agent(
            slug=data.slug,
            name=data.name,
            description=data.description,
            runtime=data.runtime,
            model=data.model,
            config=data.model_dump(mode="json", include=set(CONFIG_FIELDS)),
            status=data.status,
            current_version=1,
        )
        self.session.add(agent)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise AgentConflictError(f"An agent with slug '{data.slug}' already exists.") from exc

        self.session.add(
            AgentVersion(
                agent_id=agent.id, version=1, config_snapshot=snapshot(agent), created_by=actor
            )
        )
        self._audit(actor, "agent.created", agent, {"version": 1})
        await self.session.flush()
        await self.session.refresh(agent)
        return agent

    async def update(self, id_or_slug: str, data: AgentUpdate, *, actor: str) -> Agent:
        agent = await self.get(id_or_slug, for_update=True)
        if data.expected_version is not None and data.expected_version != agent.current_version:
            raise AgentConflictError(
                f"Agent is at version {agent.current_version}, not {data.expected_version}."
            )

        changes = data.model_dump(mode="json", exclude_unset=True, exclude={"expected_version"})
        before = snapshot(agent)
        new_config = dict(agent.config)
        for field, value in changes.items():
            if field in CONFIG_FIELDS:
                new_config[field] = value
            elif field != "status":
                setattr(agent, field, value)
        agent.config = new_config

        changed_fields = sorted(
            f for f in DEFINITION_FIELDS if snapshot(agent).get(f) != before.get(f)
        )
        status_changed = "status" in changes and changes["status"] != agent.status
        if status_changed:
            agent.status = changes["status"]

        if changed_fields:
            agent.current_version += 1
            self.session.add(
                AgentVersion(
                    agent_id=agent.id,
                    version=agent.current_version,
                    config_snapshot=snapshot(agent),
                    created_by=actor,
                )
            )
        if changed_fields or status_changed:
            self._audit(
                actor,
                "agent.updated",
                agent,
                {
                    "changed_fields": changed_fields + (["status"] if status_changed else []),
                    "version": agent.current_version,
                },
            )
            await self.session.flush()
            await self.session.refresh(agent)
        return agent

    async def archive(self, id_or_slug: str, *, actor: str) -> None:
        """Soft delete. Tasks and executions reference agents, so rows stay;
        the slug stays reserved so history keeps pointing at one agent."""
        agent = await self.get(id_or_slug, for_update=True)
        agent.status = "archived"
        self._audit(actor, "agent.archived", agent, {"version": agent.current_version})
        await self.session.flush()

    async def list_versions(self, id_or_slug: str) -> list[AgentVersion]:
        agent = await self.get(id_or_slug, include_archived=True)
        rows = await self.session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
        )
        return list(rows.scalars())

    async def get_version(self, id_or_slug: str, version: int) -> AgentVersion:
        agent = await self.get(id_or_slug, include_archived=True)
        row = (
            await self.session.execute(
                select(AgentVersion).where(
                    AgentVersion.agent_id == agent.id, AgentVersion.version == version
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise AgentNotFoundError(f"{id_or_slug} version {version}")
        return row

    def _audit(self, actor: str, action: str, agent: Agent, meta: dict[str, Any]) -> None:
        self.session.add(
            AuditLog(
                actor=actor,
                action=action,
                resource_type="agent",
                resource_id=str(agent.id),
                meta={"slug": agent.slug, **meta},
            )
        )
