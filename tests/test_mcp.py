"""MCP servers as tool sources: config rules, the runtime, and the API.

The runtime and API tests start a real MCP server over stdio
(tests/fixtures/echo_mcp_server.py), so they exercise the actual protocol.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.runtimes import AgentResultStatus
from app.tools.mcp import (
    InMemoryMcpServerStore,
    McpGrant,
    McpServerSpec,
    build_server,
    effective_tools,
    grants_from_tools,
    parse_mcp_ref,
)
from tests.test_runtime_orchestrator import (
    ScriptedModel,
    _message,
    _tool_call,
    agent,
    openai_runtime,
)

ECHO = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


def echo_spec(**overrides) -> McpServerSpec:
    body = {"name": "echo", "transport": "stdio", "command": sys.executable, "args": [ECHO]}
    return McpServerSpec(**{**body, **overrides})


# --- config rules ------------------------------------------------------------------


def test_parse_refs_and_grants():
    assert parse_mcp_ref("mcp:fs/read_file") == ("fs", "read_file")
    assert parse_mcp_ref("mcp:fs") == ("fs", None)
    with pytest.raises(ValueError):
        parse_mcp_ref("mcp:/x")
    grants = grants_from_tools(["web_search", "mcp:fs/read", "mcp:fs/list", "mcp:git"])
    assert grants["fs"].tools == {"read", "list"}
    assert grants["git"].tools is None
    # a whole-server grant wins over single tools
    assert grants_from_tools(["mcp:fs/read", "mcp:fs"])["fs"].tools is None


def test_effective_tools_is_the_intersection():
    spec = echo_spec(allowed_tools=["add", "shout"])
    assert effective_tools(McpGrant("echo", None), spec) == ["add", "shout"]
    assert effective_tools(McpGrant("echo", {"add", "secret_env"}), spec) == ["add"]
    assert effective_tools(McpGrant("echo", {"add"}), echo_spec()) == ["add"]
    assert effective_tools(McpGrant("echo", None), echo_spec()) is None


def test_spec_validation():
    with pytest.raises(ValidationError):
        McpServerSpec(name="x", transport="stdio")  # no command
    with pytest.raises(ValidationError):
        McpServerSpec(name="x", transport="streamable_http", url="http://h", command="node")
    with pytest.raises(ValidationError):
        McpServerSpec(name="x", transport="sse", url="file:///etc/passwd")
    with pytest.raises(ValidationError):  # no field takes a secret value
        McpServerSpec(name="x", transport="stdio", command="c", env={"TOKEN": "abc"})
    spec = McpServerSpec(
        name="gh",
        transport="streamable_http",
        url="https://mcp.example/x",
        headers_from_env={"Authorization": "GH_TOKEN"},
    )
    assert spec.connection_config() == {
        "url": "https://mcp.example/x",
        "headers_from_env": {"Authorization": "GH_TOKEN"},
    }


def test_stdio_is_off_unless_allowed():
    with pytest.raises(ValueError, match="MCP_ALLOW_STDIO"):
        build_server(echo_spec(), allow_stdio=False)


def test_missing_env_var_is_reported(monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    with pytest.raises(ValueError, match="NOT_SET_ANYWHERE"):
        build_server(echo_spec(env_from_host=["NOT_SET_ANYWHERE"]), allow_stdio=True)


# --- runtime -----------------------------------------------------------------------


class RecordingModel(ScriptedModel):
    """Also keeps each turn's raw input, which includes tool results."""

    def __init__(self, turns):
        super().__init__(turns)
        self.raw: list[str] = []

    async def get_response(self, system_instructions, input, *args, **kw):
        self.raw.append(repr(input))
        return await super().get_response(system_instructions, input, *args, **kw)


def mcp_runtime(model, *specs, allow_stdio=True):
    store = InMemoryMcpServerStore(specs or [echo_spec()])
    return openai_runtime(model, mcp_servers=store, allow_stdio_mcp=allow_stdio)[0]


def test_agent_calls_a_granted_mcp_tool():
    model = RecordingModel([[_tool_call("add", {"a": 2, "b": 40})], [_message("42")]])
    rt = mcp_runtime(model)
    result = asyncio.run(rt.execute(agent(tools=["mcp:echo/add"]), {"task": "add"}))
    assert result.status == AgentResultStatus.COMPLETED, result.error
    assert result.output == "42"
    assert model.seen[0]["tools"] == ["add"]  # shout and secret_env are not exposed
    assert "42" in model.raw[1]  # the MCP server's answer went back to the model


def test_whole_server_grant_respects_allowed_tools():
    model = ScriptedModel([[_message("ok")]])
    rt = mcp_runtime(model, echo_spec(allowed_tools=["add", "shout"]))
    result = asyncio.run(rt.execute(agent(tools=["mcp:echo"]), {"task": "t"}))
    assert result.ok, result.error
    assert sorted(model.seen[0]["tools"]) == ["add", "shout"]


def test_only_named_env_vars_reach_the_server(monkeypatch):
    monkeypatch.setenv("HUB_TEST_TOKEN", "t0ken")
    model = RecordingModel([[_tool_call("secret_env", {})], [_message("done")]])
    rt = mcp_runtime(model, echo_spec())
    asyncio.run(rt.execute(agent(tools=["mcp:echo/secret_env"]), {"task": "t"}))
    assert "<missing>" in model.raw[1]

    model = RecordingModel([[_tool_call("secret_env", {})], [_message("done")]])
    rt = mcp_runtime(model, echo_spec(env_from_host=["HUB_TEST_TOKEN"]))
    asyncio.run(rt.execute(agent(tools=["mcp:echo/secret_env"]), {"task": "t"}))
    assert "t0ken" in model.raw[1]


@pytest.mark.parametrize(
    ("tools", "specs", "allow", "message"),
    [
        (["mcp:nope"], None, True, "not registered"),
        (["mcp:echo/secret_env"], [echo_spec(allowed_tools=["add"])], True, "does not allow"),
        (["mcp:echo"], [echo_spec(enabled=False)], True, "disabled"),
        (["mcp:echo"], None, False, "MCP_ALLOW_STDIO"),
        (
            ["mcp:broken"],
            [echo_spec(name="broken", command="no-such-program-xyz")],
            True,
            "FileNotFoundError",
        ),
    ],
)
def test_mcp_problems_fail_the_run(tools, specs, allow, message):
    model = ScriptedModel([[_message("never")]])
    rt = mcp_runtime(model, *(specs or []), allow_stdio=allow)
    result = asyncio.run(rt.execute(agent(tools=tools), {"task": "t"}))
    assert result.status == AgentResultStatus.FAILED
    assert message in result.error
    assert model.seen == []


# --- API (needs TEST_DATABASE_URL) ---------------------------------------------------


@pytest.fixture
def allow_stdio(monkeypatch):
    monkeypatch.setattr(get_settings(), "mcp_allow_stdio", True)


BASE = "/api/v1/mcp-servers"
ECHO_BODY = {"name": "echo", "transport": "stdio", "command": sys.executable, "args": [ECHO]}


@pytest.mark.usefixtures("clean_db", "allow_stdio")
async def test_register_discover_and_list_tools(client):
    created = await client.post(BASE, json={**ECHO_BODY, "allowed_tools": ["add", "shout"]})
    assert created.status_code == 201, created.text
    assert created.json()["grant"] == "mcp:echo"
    assert (await client.post(BASE, json=ECHO_BODY)).status_code == 409

    found = await client.post(f"{BASE}/echo/discover")
    assert found.status_code == 200, found.text
    tools = {t["name"]: t for t in found.json()["tools"]}
    assert set(tools) == {"mcp:echo/add", "mcp:echo/shout"}
    assert tools["mcp:echo/add"]["input_schema"]["required"] == ["a", "b"]

    registry = (await client.get("/api/v1/tools")).json()["items"]
    names = [t["name"] for t in registry]
    assert {"web_search", "mcp:echo", "mcp:echo/add", "mcp:echo/shout"} <= set(names)

    # narrowing allowed_tools and discovering again drops the tool
    patched = await client.patch(f"{BASE}/echo", json={"allowed_tools": ["add"]})
    assert patched.status_code == 200, patched.text
    again = (await client.post(f"{BASE}/echo/discover")).json()
    assert [t["name"] for t in again["tools"]] == ["mcp:echo/add"]

    listing = (await client.get(BASE)).json()["items"]
    assert listing[0]["tool_count"] == 1

    assert (await client.delete(f"{BASE}/echo")).status_code == 204
    assert (await client.get(f"{BASE}/echo")).status_code == 404
    names = [t["name"] for t in (await client.get("/api/v1/tools")).json()["items"]]
    assert not any(n.startswith("mcp:echo") for n in names)


@pytest.mark.usefixtures("clean_db")
async def test_discover_errors(client, monkeypatch):
    await client.post(BASE, json=ECHO_BODY)
    monkeypatch.setattr(get_settings(), "mcp_allow_stdio", False)
    blocked = await client.post(f"{BASE}/echo/discover")
    assert blocked.status_code == 409 and "MCP_ALLOW_STDIO" in blocked.json()["detail"]

    monkeypatch.setattr(get_settings(), "mcp_allow_stdio", True)
    await client.post(BASE, json={**ECHO_BODY, "name": "broken", "command": "no-such-program-xyz"})
    failed = await client.post(f"{BASE}/broken/discover")
    assert failed.status_code == 502 and "FileNotFoundError" in failed.json()["detail"]


@pytest.mark.usefixtures("clean_db")
async def test_patch_is_validated(client):
    await client.post(BASE, json=ECHO_BODY)
    bad = await client.patch(f"{BASE}/echo", json={"url": "https://x.example"})
    assert bad.status_code == 422
    assert (await client.patch(f"{BASE}/echo", json={"name": "other"})).status_code == 422
