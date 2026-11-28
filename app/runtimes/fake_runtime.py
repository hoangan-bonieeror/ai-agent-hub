"""Deterministic, offline runtime for tests, local demos and CI."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.runtimes.base import (
    AgentConfig,
    AgentResult,
    AgentResultStatus,
    AgentRuntime,
    TokenUsage,
)

Responder = Callable[[AgentConfig, dict[str, Any]], Any | Awaitable[Any]]


def _default_responder(config: AgentConfig, task_input: dict[str, Any]) -> str:
    task = task_input.get("task", "")
    prior = ", ".join(task_input.get("previous_outputs", {}).keys()) or "none"
    return f"[{config.name}] handled: {task} (prior steps: {prior})"


class FakeAgentRuntime(AgentRuntime):
    """Returns responder output without calling any model.

    ``responders`` maps agent ids to callables; a responder that raises makes
    the task fail, which is handy for testing orchestrator error paths. Calls
    are recorded in ``calls`` for assertions.
    """

    def __init__(
        self,
        responders: dict[str, Responder] | None = None,
        *,
        name: str = "fake",
        delay_seconds: float = 0.0,
    ) -> None:
        self.name = name
        self.responders = responders or {}
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _respond(self, config: AgentConfig, task_input: dict[str, Any]) -> Any:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        responder = self.responders.get(config.id, _default_responder)
        value = responder(config, task_input)
        if asyncio.iscoroutine(value):
            value = await value
        return value

    async def execute(self, agent_config: AgentConfig, task_input: dict[str, Any]) -> AgentResult:
        started = time.monotonic()
        self.calls.append((agent_config.id, task_input))
        status, output, error = AgentResultStatus.COMPLETED, None, None
        try:
            output = await asyncio.wait_for(
                self._respond(agent_config, task_input), timeout=agent_config.limits.timeout_seconds
            )
        except TimeoutError:
            status, error = (
                AgentResultStatus.TIMED_OUT,
                f"Timed out after {agent_config.limits.timeout_seconds}s",
            )
        except Exception as exc:
            status, error = AgentResultStatus.FAILED, f"{type(exc).__name__}: {exc}"
        return AgentResult(
            agent_id=agent_config.id,
            runtime=self.name,
            status=status,
            output=output,
            error=error,
            usage=estimate_usage(task_input, output),
            model=agent_config.model,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def estimate_usage(task_input: dict[str, Any], output: Any) -> TokenUsage:
    """Rough token counts (about 4 characters a token), so cost tracking has
    something to show when runs use the fake runtime."""
    input_tokens = max(1, len(json.dumps(task_input, default=str)) // 4)
    output_tokens = 0 if output is None else max(1, len(json.dumps(output, default=str)) // 4)
    return TokenUsage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
