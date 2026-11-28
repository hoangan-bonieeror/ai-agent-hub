"""Model Context Protocol (MCP) servers as a source of agent tools (plan section 10).

Agents are granted MCP tools by name in their ``tools`` list:

- ``mcp:<server>`` grants every tool the server allows,
- ``mcp:<server>/<tool>`` grants one tool.

A server's ``allowed_tools`` caps what any agent can be granted, so the
effective set is the intersection. Anything not granted is not exposed to the
model (deny by default). Unknown or disabled servers fail the agent's run
rather than being skipped silently.

Secrets never live in a server's stored config. ``env_from_host`` names
environment variables of the Hub process to pass to a stdio server, and
``headers_from_env`` maps HTTP headers to environment variable names.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
    create_static_tool_filter,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MCP_PREFIX = "mcp:"

ServerName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
HeaderName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,100}$")]
Transport = Literal["stdio", "streamable_http", "sse"]


class McpConfigError(ValueError):
    """The server cannot be used as configured (or is not allowed here)."""


class McpServerSpec(BaseModel):
    """How to reach one MCP server. No field accepts secret values."""

    model_config = ConfigDict(extra="forbid")

    name: ServerName
    transport: Transport
    description: str | None = Field(default=None, max_length=2000)
    # stdio
    command: str | None = Field(default=None, max_length=500)
    args: list[Annotated[str, StringConstraints(max_length=2000)]] = Field(
        default_factory=list, max_length=100
    )
    cwd: str | None = Field(default=None, max_length=1000)
    env_from_host: list[EnvName] = Field(default_factory=list, max_length=50)
    # streamable_http / sse
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://")
    headers_from_env: dict[HeaderName, EnvName] = Field(default_factory=dict)
    # both
    allowed_tools: list[Annotated[str, StringConstraints(min_length=1, max_length=200)]] | None = (
        None
    )
    timeout_seconds: float = Field(default=10, gt=0, le=300)
    enabled: bool = True

    @model_validator(mode="after")
    def _check_transport(self) -> McpServerSpec:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("A stdio server needs a command")
            if self.url or self.headers_from_env:
                raise ValueError("url and headers_from_env are for HTTP servers")
        else:
            if not self.url:
                raise ValueError(f"A {self.transport} server needs a url")
            if self.command or self.args or self.cwd or self.env_from_host:
                raise ValueError("command, args, cwd and env_from_host are for stdio servers")
        return self

    def connection_config(self) -> dict[str, Any]:
        """The part stored in ``mcp_servers.config``."""
        return self.model_dump(
            exclude={"name", "transport", "description", "allowed_tools", "enabled"},
            exclude_defaults=True,
        )


def is_mcp_ref(tool: str) -> bool:
    return tool.startswith(MCP_PREFIX)


def parse_mcp_ref(tool: str) -> tuple[str, str | None]:
    """``"mcp:fs/read_file"`` -> ``("fs", "read_file")``; ``"mcp:fs"`` -> ``("fs", None)``."""
    if not is_mcp_ref(tool):
        raise ValueError(f"Not an MCP tool reference: {tool!r}")
    server, _, name = tool[len(MCP_PREFIX) :].partition("/")
    if not server:
        raise ValueError(f"MCP tool reference has no server: {tool!r}")
    return server, (name or None)


def tool_ref(server: str, tool: str) -> str:
    return f"{MCP_PREFIX}{server}/{tool}"


@dataclass
class McpGrant:
    """Tools one agent may use from one server. ``tools=None``: all allowed."""

    server: str
    tools: set[str] | None = field(default_factory=set)


def grants_from_tools(tools: Iterable[str]) -> dict[str, McpGrant]:
    grants: dict[str, McpGrant] = {}
    for tool in tools:
        if not is_mcp_ref(tool):
            continue
        server, name = parse_mcp_ref(tool)
        grant = grants.setdefault(server, McpGrant(server))
        if name is None:
            grant.tools = None
        elif grant.tools is not None:
            grant.tools.add(name)
    return grants


def effective_tools(grant: McpGrant, spec: McpServerSpec) -> list[str] | None:
    """Names the agent may call (None: everything the server lists)."""
    if grant.tools is None:
        return None if spec.allowed_tools is None else list(spec.allowed_tools)
    if spec.allowed_tools is None:
        return sorted(grant.tools)
    return sorted(grant.tools & set(spec.allowed_tools))


def _env(names: Iterable[str]) -> dict[str, str]:
    missing = [n for n in names if n not in os.environ]
    if missing:
        raise McpConfigError(f"Environment variables not set on the Hub: {', '.join(missing)}")
    return {n: os.environ[n] for n in names}


def build_server(
    spec: McpServerSpec,
    *,
    allowed_tools: list[str] | None = None,
    allow_stdio: bool,
) -> MCPServer:
    """An unconnected SDK server object, limited to ``allowed_tools``."""
    tool_filter = (
        create_static_tool_filter(allowed_tool_names=allowed_tools)
        if allowed_tools is not None
        else None
    )
    common: dict[str, Any] = {
        "name": spec.name,
        "client_session_timeout_seconds": spec.timeout_seconds,
        "tool_filter": tool_filter,
        "cache_tools_list": True,
    }
    if spec.transport == "stdio":
        if not allow_stdio:
            raise McpConfigError(
                f"MCP server '{spec.name}' runs a local command, and stdio servers are off. "
                "Set MCP_ALLOW_STDIO=true to allow them."
            )
        params: dict[str, Any] = {"command": spec.command, "args": spec.args}
        if spec.cwd:
            params["cwd"] = spec.cwd
        # The MCP client starts the process with a minimal safe environment
        # (PATH, HOME, SYSTEMROOT...) plus these; the Hub's other secrets stay out.
        params["env"] = _env(spec.env_from_host)
        return MCPServerStdio(params, **common)  # type: ignore[arg-type]

    http: dict[str, Any] = {"url": spec.url, "timeout": spec.timeout_seconds}
    headers = {h: os.environ.get(env) for h, env in spec.headers_from_env.items()}
    missing = [spec.headers_from_env[h] for h, v in headers.items() if v is None]
    if missing:
        raise McpConfigError(f"Environment variables not set on the Hub: {', '.join(missing)}")
    if headers:
        http["headers"] = headers
    cls = MCPServerStreamableHttp if spec.transport == "streamable_http" else MCPServerSse
    return cls(http, **common)  # type: ignore[arg-type]


class DiscoveredTool(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


async def discover_tools(spec: McpServerSpec, *, allow_stdio: bool) -> list[DiscoveredTool]:
    """Connect, list the server's tools (as limited by ``allowed_tools``), disconnect."""
    server = build_server(spec, allowed_tools=spec.allowed_tools, allow_stdio=allow_stdio)
    try:
        await server.connect()
        tools = await server.list_tools()
    finally:
        await server.cleanup()
    return [
        DiscoveredTool(name=t.name, description=t.description, input_schema=_schema_of(t))
        for t in tools
    ]


def _schema_of(tool: Any) -> dict[str, Any]:
    # mcp 2.x names it input_schema; 1.x used inputSchema.
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return dict(schema or {})


def describe_error(exc: BaseException) -> str:
    """The innermost real error, unwrapping anyio's exception groups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


class McpServerStore(Protocol):
    async def get(self, name: str) -> McpServerSpec | None: ...


class InMemoryMcpServerStore:
    def __init__(self, specs: Iterable[McpServerSpec] = ()) -> None:
        self._specs = {s.name: s for s in specs}

    def upsert(self, spec: McpServerSpec) -> None:
        self._specs[spec.name] = spec

    async def get(self, name: str) -> McpServerSpec | None:
        return self._specs.get(name)


def spec_from_row(row: Any) -> McpServerSpec:
    return McpServerSpec(
        name=row.name,
        transport=row.transport,
        description=row.description,
        allowed_tools=row.allowed_tools,
        enabled=row.enabled,
        **row.config,
    )


class DbMcpServerStore:
    """Reads server specs from the ``mcp_servers`` table, one short session per lookup."""

    async def get(self, name: str) -> McpServerSpec | None:
        from sqlalchemy import select

        from app.core.database import get_sessionmaker
        from app.models import McpServer

        async with get_sessionmaker()() as session:
            row = await session.scalar(select(McpServer).where(McpServer.name == name))
            return spec_from_row(row) if row is not None else None
