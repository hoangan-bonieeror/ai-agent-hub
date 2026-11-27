"""The slice of the Agent Registry the orchestrator depends on.

The real, database-backed registry lives in ``app/registry``. The
orchestrator only needs :class:`AgentLookup`, so any object with a matching
``get_agent`` works, including the in-memory one below used by tests and the
offline demo.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.runtimes.base import AgentConfig


class AgentNotFoundError(LookupError):
    pass


@runtime_checkable
class AgentLookup(Protocol):
    async def get_agent(self, agent_id: str) -> AgentConfig:
        """Return the agent's current config or raise ``LookupError``."""
        ...


class InMemoryAgentRegistry:
    def __init__(self, agents: list[AgentConfig] | None = None) -> None:
        self._agents: dict[str, AgentConfig] = {}
        for agent in agents or []:
            self.upsert(agent)

    def upsert(self, agent: AgentConfig) -> None:
        self._agents[agent.id] = agent

    async def get_agent(self, agent_id: str) -> AgentConfig:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise AgentNotFoundError(f"Agent '{agent_id}' not found") from None

    async def list_agents(self) -> list[AgentConfig]:
        return list(self._agents.values())
