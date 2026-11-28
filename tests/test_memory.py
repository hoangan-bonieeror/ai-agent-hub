"""Memory service: the agent tools, the PostgreSQL store and the API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.memory.store import DbMemoryStore, InMemoryMemoryStore
from app.runtimes import AgentResultStatus
from tests.test_runtime_orchestrator import (
    ScriptedModel,
    _message,
    _tool_call,
    agent,
    openai_runtime,
)


class RecordingModel(ScriptedModel):
    def __init__(self, turns):
        super().__init__(turns)
        self.raw: list[str] = []

    async def get_response(self, system_instructions, input, *args, **kw):
        self.raw.append(repr(input))
        return await super().get_response(system_instructions, input, *args, **kw)


# --- agent tools (in-memory store) -------------------------------------------------


def test_agent_saves_then_finds_a_memory():
    store = InMemoryMemoryStore()
    saver = ScriptedModel(
        [
            [_tool_call("memory_save", {"content": "Customer Acme prefers PDF reports"})],
            [_message("ok")],
        ]
    )
    rt, _ = openai_runtime(saver, memory=store)
    result = asyncio.run(rt.execute(agent(id="writer", tools=["memory"]), {"task": "remember"}))
    assert result.ok, result.error
    assert sorted(saver.seen[0]["tools"]) == ["memory_save", "memory_search"]
    assert [(m.scope, m.owner, m.created_by) for m in store.items] == [
        ("agent", "writer", "agent:writer")
    ]

    finder = RecordingModel([[_tool_call("memory_search", {"query": "acme"})], [_message("PDF")]])
    rt, _ = openai_runtime(finder, memory=store)
    asyncio.run(rt.execute(agent(id="writer", tools=["memory"]), {"task": "recall"}))
    assert "prefers PDF reports" in finder.raw[1]


def test_agents_do_not_read_each_others_private_memories():
    store = InMemoryMemoryStore()
    asyncio.run(store.save("private plan", scope="agent", owner="alice"))
    asyncio.run(store.save("shared plan", scope="shared", owner="alice"))
    found = asyncio.run(store.search("plan", agent="bob"))
    assert [m.content for m in found] == ["shared plan"]
    assert len(asyncio.run(store.search("plan", agent="alice"))) == 2


def test_memory_without_a_store_is_denied():
    model = ScriptedModel([[_message("never")]])
    rt, _ = openai_runtime(model)
    result = asyncio.run(rt.execute(agent(tools=["memory"]), {"task": "t"}))
    assert result.status == AgentResultStatus.FAILED and "memory" in result.error
    assert model.seen == []


# --- PostgreSQL store and API (need TEST_DATABASE_URL) ----------------------------------

pg = pytest.mark.usefixtures("clean_db")
BASE = "/api/v1/memories"


async def make_agent(client, slug: str) -> None:
    body = {"slug": slug, "name": slug, "runtime": "fake", "model": "m", "instructions": "i"}
    assert (await client.post("/api/v1/agents", json=body)).status_code == 201


@pg
async def test_db_store_search_scopes_and_expiry():
    store = DbMemoryStore()
    await store.save("Khách hàng Acme thích báo cáo PDF", scope="agent", owner="alice")
    await store.save("Acme renewal is in March", scope="shared", owner=None)
    await store.save(
        "Acme old note",
        scope="shared",
        owner=None,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    await store.save("Unrelated budget note", scope="agent", owner="bob")

    alice = await store.search("acme", agent="alice")
    assert {m.content for m in alice} == {
        "Khách hàng Acme thích báo cáo PDF",
        "Acme renewal is in March",
    }
    assert [m.content for m in await store.search("acme", agent="bob")] == [
        "Acme renewal is in March"
    ]
    # Vietnamese words match without stemming; partial words match by substring
    assert len(await store.search("báo cáo", agent="alice")) == 1
    assert len(await store.search("renew", agent=None)) == 1
    # "%" is a literal, not a wildcard
    assert await store.search("%", agent=None) == []


@pg
async def test_api_crud_and_search(client):
    await make_agent(client, "alice")
    created = await client.post(
        BASE,
        json={"content": "Use metric units", "scope": "agent", "owner": "alice", "tags": ["style"]},
    )
    assert created.status_code == 201, created.text
    memory = created.json()
    assert memory["created_by"] == "anonymous" and memory["tags"] == ["style"]

    shared = await client.post(BASE, json={"content": "Company fiscal year starts in April"})
    assert shared.status_code == 201 and shared.json()["scope"] == "shared"

    found = (await client.get(BASE, params={"q": "metric"})).json()
    assert found["total"] == 1 and found["items"][0]["id"] == memory["id"]
    assert (await client.get(BASE, params={"agent": "bob"})).json()["total"] == 1
    assert (await client.get(BASE, params={"agent": "alice"})).json()["total"] == 2

    patched = await client.patch(f"{BASE}/{memory['id']}", json={"content": "Use SI units"})
    assert patched.json()["content"] == "Use SI units"

    assert (await client.delete(f"{BASE}/{memory['id']}")).status_code == 204
    assert (await client.get(f"{BASE}/{memory['id']}")).status_code == 404
    assert (await client.get(f"{BASE}/not-a-uuid")).status_code == 404


@pg
async def test_api_validation(client):
    assert (await client.post(BASE, json={"content": "x", "scope": "agent"})).status_code == 422
    assert (await client.post(BASE, json={"content": "x", "owner": "a"})).status_code == 422
    assert (await client.post(BASE, json={"content": "   "})).status_code == 422
    missing = await client.post(BASE, json={"content": "x", "scope": "agent", "owner": "ghost"})
    assert missing.status_code == 422 and "ghost" in missing.json()["detail"]


@pg
async def test_memory_is_listed_as_a_tool(client):
    names = [t["name"] for t in (await client.get("/api/v1/tools")).json()["items"]]
    assert "memory" in names
