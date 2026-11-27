"""Read-side registry used by the orchestrator: turns a stored agent into the
runtime's AgentConfig."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_repository import (
    AgentNotFoundError,
    AgentRepository,
    to_read_dict,
)
from app.runtimes.base import AgentConfig

__all__ = ["AgentNotFoundError", "AgentRegistry"]


class AgentRegistry:
    def __init__(self, session: AsyncSession):
        self._repo = AgentRepository(session)

    async def get_agent(self, agent_id: str) -> AgentConfig:
        """Look up by slug or UUID. Raises AgentNotFoundError (a KeyError) if
        the agent is missing or archived. Disabled agents are returned with
        enabled=False so the caller decides whether to refuse them."""
        agent = await self._repo.get(agent_id)
        data = to_read_dict(agent)
        return AgentConfig(
            id=agent.slug,
            name=data["name"],
            description=data["description"] or "",
            runtime=data["runtime"],
            model=data["model"],
            instructions=data["instructions"],
            tools=data["tools"],
            capabilities=data["capabilities"],
            limits=data["limits"].model_dump(),
            enabled=data["enabled"],
        )
