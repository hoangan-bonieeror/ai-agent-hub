"""MCP servers and the tool registry: /api/v1/mcp-servers, /api/v1/tools"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import ActorDep
from app.core.config import get_settings
from app.core.database import get_session
from app.memory.tools import MEMORY_GRANT
from app.models import AuditLog, McpServer, Tool
from app.runtimes.openai_runtime import DEFAULT_TOOL_FACTORIES
from app.tools.mcp import (
    McpConfigError,
    McpServerSpec,
    describe_error,
    discover_tools,
    spec_from_row,
    tool_ref,
)

router = APIRouter(tags=["tools"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

BUILTIN_TOOL_DESCRIPTIONS = {
    "web_search": "Search the web (OpenAI hosted tool).",
    "memory": "memory_search and memory_save: the agent's own and shared notes from earlier runs.",
}


class McpServerUpdate(BaseModel):
    """PATCH body: only the fields sent change. ``name`` and ``transport`` are fixed."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    command: str | None = None
    args: list[str] | None = None
    cwd: str | None = None
    env_from_host: list[str] | None = None
    url: str | None = None
    headers_from_env: dict[str, str] | None = None
    allowed_tools: list[str] | None = None
    timeout_seconds: float | None = None
    enabled: bool | None = None


def tool_view(row: Tool, server: str | None) -> dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "source": "mcp" if row.mcp_server_id else "registry",
        "server": server,
        "input_schema": row.schema,
        "enabled": row.enabled,
        "updated_at": row.updated_at,
    }


def server_view(row: McpServer, tools: list[Tool] | None = None, count: int = 0) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "transport": row.transport,
        "config": row.config,
        "allowed_tools": row.allowed_tools,
        "enabled": row.enabled,
        "grant": f"mcp:{row.name}",
        "tool_count": len(tools) if tools is not None else count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if tools is not None:
        view["tools"] = [tool_view(t, row.name) for t in tools]
    return view


async def load_server(session: AsyncSession, name: str) -> McpServer:
    row = await session.scalar(select(McpServer).where(McpServer.name == name))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    return row


async def server_tools(session: AsyncSession, row: McpServer) -> list[Tool]:
    return list(
        await session.scalars(select(Tool).where(Tool.mcp_server_id == row.id).order_by(Tool.name))
    )


def audit(session: AsyncSession, actor: str, action: str, row: McpServer, **meta: Any) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type="mcp_server",
            resource_id=str(row.id),
            meta={"name": row.name, **meta},
        )
    )


@router.post("/mcp-servers", status_code=status.HTTP_201_CREATED)
async def create_server(body: McpServerSpec, session: SessionDep, actor: ActorDep) -> dict:
    """Register an MCP server. Then call ``/discover`` to load its tools."""
    row = McpServer(
        name=body.name,
        description=body.description,
        transport=body.transport,
        config=body.connection_config(),
        allowed_tools=body.allowed_tools,
        enabled=body.enabled,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"An MCP server named '{body.name}' already exists"
        ) from exc
    audit(session, actor, "mcp_server.created", row)
    await session.commit()
    await session.refresh(row)
    return server_view(row, [])


@router.get("/mcp-servers")
async def list_servers(session: SessionDep) -> dict:
    rows = list(await session.scalars(select(McpServer).order_by(McpServer.name)))
    counts = dict(
        (
            await session.execute(
                select(Tool.mcp_server_id, func.count())
                .where(Tool.mcp_server_id.is_not(None))
                .group_by(Tool.mcp_server_id)
            )
        ).all()
    )
    return {"items": [server_view(r, count=counts.get(r.id, 0)) for r in rows]}


@router.get("/mcp-servers/{name}")
async def get_server(name: str, session: SessionDep) -> dict:
    row = await load_server(session, name)
    return server_view(row, await server_tools(session, row))


@router.patch("/mcp-servers/{name}")
async def update_server(
    name: str, body: McpServerUpdate, session: SessionDep, actor: ActorDep
) -> dict:
    row = await load_server(session, name)
    current = spec_from_row(row).model_dump()
    changes = body.model_dump(exclude_unset=True)
    try:
        spec = McpServerSpec.model_validate({**current, **changes})
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", *e["loc"]], "msg": e["msg"]} for e in exc.errors()],
        ) from exc
    row.description = spec.description
    row.config = spec.connection_config()
    row.allowed_tools = spec.allowed_tools
    row.enabled = spec.enabled
    audit(session, actor, "mcp_server.updated", row, fields=sorted(changes))
    await session.commit()
    await session.refresh(row)
    return server_view(row, await server_tools(session, row))


@router.delete("/mcp-servers/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(name: str, session: SessionDep, actor: ActorDep) -> Response:
    """Remove the server and its discovered tools. Agents that still list
    ``mcp:<name>`` fail until their tools are changed."""
    row = await load_server(session, name)
    audit(session, actor, "mcp_server.deleted", row)
    await session.execute(delete(Tool).where(Tool.mcp_server_id == row.id))
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/mcp-servers/{name}/discover")
async def discover(name: str, session: SessionDep, actor: ActorDep) -> dict:
    """Connect to the server, list its tools and store them in the tool
    registry (as ``mcp:<server>/<tool>``). Tools it no longer lists are removed."""
    row = await load_server(session, name)
    spec = spec_from_row(row)
    try:
        found = await discover_tools(spec, allow_stdio=get_settings().mcp_allow_stdio)
    except McpConfigError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:  # connection refused, bad command, protocol error...
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not list tools of MCP server '{name}': {describe_error(exc)}",
        ) from exc

    existing = {t.name: t for t in await server_tools(session, row)}
    seen: set[str] = set()
    for tool in found:
        ref = tool_ref(row.name, tool.name)
        seen.add(ref)
        stored = existing.get(ref)
        if stored is None:
            session.add(
                Tool(
                    name=ref,
                    description=tool.description,
                    schema=tool.input_schema,
                    mcp_server_id=row.id,
                )
            )
        else:
            stored.description = tool.description
            stored.schema = tool.input_schema
    for ref, stored in existing.items():
        if ref not in seen:
            await session.delete(stored)
    audit(session, actor, "mcp_server.discovered", row, tools=sorted(seen))
    await session.commit()
    return server_view(row, await server_tools(session, row))


@router.get("/tools")
async def list_tools(session: SessionDep) -> dict:
    """Every tool an agent's ``tools`` list can name: built-in tools, each
    enabled MCP server as a whole (``mcp:<server>``), and each discovered MCP
    tool (``mcp:<server>/<tool>``)."""
    items: list[dict[str, Any]] = [
        {
            "name": name,
            "description": BUILTIN_TOOL_DESCRIPTIONS.get(name),
            "source": "builtin",
            "server": None,
            "input_schema": None,
            "enabled": True,
        }
        for name in sorted([*DEFAULT_TOOL_FACTORIES, MEMORY_GRANT])
    ]
    servers = {s.id: s for s in await session.scalars(select(McpServer).order_by(McpServer.name))}
    for server in servers.values():
        items.append(
            {
                "name": f"mcp:{server.name}",
                "description": f"Every allowed tool of MCP server '{server.name}'",
                "source": "mcp_server",
                "server": server.name,
                "input_schema": None,
                "enabled": server.enabled,
            }
        )
    for tool in await session.scalars(select(Tool).order_by(Tool.name)):
        server = servers.get(tool.mcp_server_id) if tool.mcp_server_id else None
        view = tool_view(tool, server.name if server else None)
        if server is not None:
            view["enabled"] = tool.enabled and server.enabled
        items.append(view)
    return {"items": items}
