"""Runtime-agnostic contracts between the Hub Core and agent runtimes.

The orchestrator only talks to :class:`AgentRuntime`. Each concrete runtime
(OpenAI Agents SDK, LangGraph, a custom LLM loop, a local model...) is an
adapter that turns an :class:`AgentConfig` plus a task input into an
:class:`AgentResult`. Swapping frameworks means adding an adapter and pointing
an agent's ``runtime`` field at it; Hub Core code does not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentLimits(BaseModel):
    max_steps: int = Field(default=10, ge=1)
    timeout_seconds: float = Field(default=120, gt=0)


class AgentConfig(BaseModel):
    """Agent definition as stored in the Agent Registry (plan section 5).

    Secrets never live here: model credentials come from the environment or a
    secret manager and are resolved by the runtime adapter.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str = ""
    runtime: str = "openai_agents"
    model: str | None = None
    instructions: str
    tools: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    enabled: bool = True


class TokenUsage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class AgentResultStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"


class AgentResult(BaseModel):
    """Uniform output of any runtime, whatever framework produced it."""

    agent_id: str
    runtime: str
    status: AgentResultStatus
    output: Any = None
    error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == AgentResultStatus.COMPLETED


class AgentRuntime(ABC):
    """Interface every runtime adapter implements.

    Adapters must not raise for expected agent failures (timeouts, step
    limits, model errors). They return an :class:`AgentResult` with a
    non-completed status so the orchestrator can record and decide.
    """

    #: Value of ``AgentConfig.runtime`` this adapter serves.
    name: str

    @abstractmethod
    async def execute(
        self, agent_config: AgentConfig, task_input: dict[str, Any]
    ) -> AgentResult: ...


class RuntimeNotFoundError(LookupError):
    pass


class RuntimeRegistry:
    """Maps ``AgentConfig.runtime`` names to runtime adapters."""

    def __init__(self, runtimes: list[AgentRuntime] | None = None) -> None:
        self._runtimes: dict[str, AgentRuntime] = {}
        for runtime in runtimes or []:
            self.register(runtime)

    def register(self, runtime: AgentRuntime) -> None:
        self._runtimes[runtime.name] = runtime

    def get(self, name: str) -> AgentRuntime:
        try:
            return self._runtimes[name]
        except KeyError:
            raise RuntimeNotFoundError(
                f"No runtime registered for '{name}'. Registered: {sorted(self._runtimes)}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._runtimes)
