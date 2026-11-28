"""Runtime adapter for the OpenAI Agents SDK (``openai-agents``)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

from agents import Agent, ModelProvider, RunConfig, Runner, Tool, WebSearchTool
from agents.exceptions import AgentsException, MaxTurnsExceeded
from agents.mcp import MCPServer

from app.runtimes.base import AgentConfig, AgentResult, AgentResultStatus, AgentRuntime, TokenUsage
from app.runtimes.prompting import render_task_input
from app.tools.mcp import (
    McpConfigError,
    McpServerStore,
    build_server,
    describe_error,
    effective_tools,
    grants_from_tools,
    is_mcp_ref,
)

ToolFactory = Callable[[], Tool]

DEFAULT_TOOL_FACTORIES: dict[str, ToolFactory] = {
    "web_search": WebSearchTool,
}


class UnknownToolError(LookupError):
    pass


class OpenAIAgentRuntime(AgentRuntime):
    """Runs an :class:`AgentConfig` with the OpenAI Agents SDK.

    * ``limits.max_steps`` maps to the SDK's ``max_turns``.
    * ``limits.timeout_seconds`` is enforced with ``asyncio.wait_for``.
    * Tools are resolved by name from ``tool_factories``. A tool name with no
      factory fails the run (deny by default) rather than being skipped.
    * ``model_provider`` lets callers route to another provider or a fake
      model in tests; by default the SDK reads ``OPENAI_API_KEY`` from env.
    * Tools named ``mcp:<server>`` or ``mcp:<server>/<tool>`` come from MCP
      servers looked up in ``mcp_servers``. Each run connects to the servers it
      needs and disconnects when it ends. Only the granted tools are exposed.
    """

    name = "openai_agents"

    def __init__(
        self,
        *,
        default_model: str | None = None,
        tool_factories: dict[str, ToolFactory] | None = None,
        model_provider: ModelProvider | None = None,
        tracing_disabled: bool = False,
        mcp_servers: McpServerStore | None = None,
        allow_stdio_mcp: bool = False,
    ) -> None:
        self.default_model = default_model
        self.tool_factories = dict(
            DEFAULT_TOOL_FACTORIES if tool_factories is None else tool_factories
        )
        self.model_provider = model_provider
        self.tracing_disabled = tracing_disabled
        self.mcp_servers = mcp_servers
        self.allow_stdio_mcp = allow_stdio_mcp

    def build_agent(self, config: AgentConfig, mcp_servers: list[MCPServer] | None = None) -> Agent:
        tools: list[Tool] = []
        for tool_name in config.tools:
            if is_mcp_ref(tool_name):
                continue  # served by mcp_servers
            factory = self.tool_factories.get(tool_name)
            if factory is None:
                raise UnknownToolError(
                    f"Agent '{config.id}' requests tool '{tool_name}', which is not registered"
                )
            tools.append(factory())
        return Agent(
            name=config.name,
            instructions=config.instructions,
            model=config.model or self.default_model,
            tools=tools,
            mcp_servers=list(mcp_servers or []),
        )

    async def resolve_mcp_servers(self, config: AgentConfig) -> list[MCPServer]:
        """Unconnected servers for the agent's ``mcp:`` tools, each limited to
        what the agent was granted. Raises UnknownToolError or McpConfigError."""
        grants = grants_from_tools(config.tools)
        if not grants:
            return []
        if self.mcp_servers is None:
            raise UnknownToolError(
                f"Agent '{config.id}' requests MCP tools, but no MCP servers are configured"
            )
        servers: list[MCPServer] = []
        for name, grant in grants.items():
            spec = await self.mcp_servers.get(name)
            if spec is None:
                raise UnknownToolError(
                    f"Agent '{config.id}' requests MCP server '{name}', which is not registered"
                )
            if not spec.enabled:
                raise McpConfigError(f"MCP server '{name}' is disabled")
            allowed = effective_tools(grant, spec)
            if grant.tools is not None and len(allowed or []) < len(grant.tools):
                blocked = sorted(grant.tools - set(allowed or []))
                raise UnknownToolError(
                    f"MCP server '{name}' does not allow tools {blocked} for agents"
                )
            servers.append(
                build_server(spec, allowed_tools=allowed, allow_stdio=self.allow_stdio_mcp)
            )
        return servers

    def _run_config(self, config: AgentConfig) -> RunConfig:
        kwargs: dict[str, Any] = {
            "workflow_name": f"agent-hub:{config.id}",
            "tracing_disabled": self.tracing_disabled,
        }
        if self.model_provider is not None:
            kwargs["model_provider"] = self.model_provider
        return RunConfig(**kwargs)

    async def execute(self, agent_config: AgentConfig, task_input: dict[str, Any]) -> AgentResult:
        started = time.monotonic()

        def result(status: AgentResultStatus, **kw: Any) -> AgentResult:
            return AgentResult(
                agent_id=agent_config.id,
                runtime=self.name,
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
                **kw,
            )

        try:
            mcp_servers = await self.resolve_mcp_servers(agent_config)
            agent = self.build_agent(agent_config, mcp_servers)
        except (UnknownToolError, McpConfigError) as exc:
            return result(AgentResultStatus.FAILED, error=str(exc))

        async def run_with_servers():
            # Connect, run and disconnect in one task: the MCP client's
            # cancel scopes must be exited by the task that entered them.
            async with AsyncExitStack() as stack:
                for server in mcp_servers:
                    stack.push_async_callback(server.cleanup)
                    await server.connect()
                return await Runner.run(
                    agent,
                    render_task_input(task_input),
                    max_turns=agent_config.limits.max_steps,
                    run_config=self._run_config(agent_config),
                )

        try:
            run = await asyncio.wait_for(
                run_with_servers(), timeout=agent_config.limits.timeout_seconds
            )
        except TimeoutError:
            return result(
                AgentResultStatus.TIMED_OUT,
                error=f"Timed out after {agent_config.limits.timeout_seconds}s",
            )
        except MaxTurnsExceeded as exc:
            return result(AgentResultStatus.STEP_LIMIT_EXCEEDED, error=str(exc))
        except AgentsException as exc:
            return result(AgentResultStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # provider/network errors, MCP connection failures
            return result(AgentResultStatus.FAILED, error=describe_error(exc))

        sdk_usage = run.context_wrapper.usage
        output = run.final_output
        if hasattr(output, "model_dump"):
            output = output.model_dump()
        return result(
            AgentResultStatus.COMPLETED,
            output=output,
            usage=TokenUsage(
                requests=sdk_usage.requests,
                input_tokens=sdk_usage.input_tokens,
                output_tokens=sdk_usage.output_tokens,
                total_tokens=sdk_usage.total_tokens,
            ),
            metadata={"last_agent": run.last_agent.name, "last_response_id": run.last_response_id},
        )
