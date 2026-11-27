"""Agent Registry API against a real PostgreSQL (see conftest: TEST_DATABASE_URL)."""

import uuid

import pytest
from sqlalchemy import select, text

from app.models import AuditLog
from app.registry.agent_registry import AgentNotFoundError, AgentRegistry

pytestmark = pytest.mark.usefixtures("clean_db")

BASE = "/api/v1/agents"


def research_agent(**overrides):
    body = {
        "slug": "research-agent",
        "name": "Research Agent",
        "description": "Research topics and collect evidence",
        "runtime": "openai_agents",
        "model": "configured-model",
        "instructions": "Research the assigned task and return structured findings.",
        "tools": ["web_search", "document_reader"],
        "capabilities": ["research", "summarization"],
        "limits": {"max_steps": 10, "timeout_seconds": 120},
    }
    return {**body, **overrides}


async def create(client, **overrides):
    resp = await client.post(BASE, json=research_agent(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- create -----------------------------------------------------------------


async def test_create_returns_full_agent_at_version_1(client):
    agent = await create(client)
    assert uuid.UUID(agent["id"])
    assert agent["slug"] == "research-agent"
    assert agent["tools"] == ["web_search", "document_reader"]
    assert agent["limits"] == {"max_steps": 10, "timeout_seconds": 120}
    assert agent["status"] == "active"
    assert agent["enabled"] is True
    assert agent["current_version"] == 1


async def test_create_applies_defaults(client):
    resp = await client.post(
        BASE, json={"slug": "min", "name": "Min", "runtime": "custom", "model": "m"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["instructions"] == ""
    assert body["tools"] == [] and body["capabilities"] == []
    assert body["limits"] == {"max_steps": 10, "timeout_seconds": 120}


async def test_duplicate_slug_is_409(client):
    await create(client)
    resp = await client.post(BASE, json=research_agent(name="Other"))
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "overrides",
    [
        {"slug": "Has Spaces"},
        {"slug": "-leading-dash"},
        {"name": ""},
        {"runtime": "Not Valid!"},
        {"limits": {"max_steps": 0}},
        {"limits": {"timeout_seconds": 99999}},
        {"api_key": "sk-should-not-be-stored"},  # unknown fields are rejected
        {"limits": {"max_steps": 5, "api_key": "x"}},
        {"status": "archived"},  # archive only via DELETE
    ],
)
async def test_create_rejects_invalid_input(client, overrides):
    resp = await client.post(BASE, json=research_agent(**overrides))
    assert resp.status_code == 422, resp.text


# --- read -------------------------------------------------------------------


async def test_get_by_uuid_and_by_slug(client):
    agent = await create(client)
    by_id = await client.get(f"{BASE}/{agent['id']}")
    by_slug = await client.get(f"{BASE}/research-agent")
    assert by_id.status_code == by_slug.status_code == 200
    assert by_id.json() == by_slug.json() == agent


async def test_get_missing_is_404(client):
    assert (await client.get(f"{BASE}/nope")).status_code == 404
    assert (await client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404


async def test_list_filters_and_pagination(client):
    await create(client)
    await create(client, slug="writer", name="Writer", capabilities=["writing"], tools=[])
    await create(client, slug="coder", name="Coder", runtime="custom", capabilities=["code"])
    await client.patch(f"{BASE}/coder", json={"status": "disabled"})

    all_ = (await client.get(BASE)).json()
    assert all_["total"] == 3
    assert [a["slug"] for a in all_["items"]] == ["research-agent", "writer", "coder"]

    def slugs(resp):
        return [a["slug"] for a in resp.json()["items"]]

    assert slugs(await client.get(BASE, params={"capability": "writing"})) == ["writer"]
    assert slugs(await client.get(BASE, params={"tool": "web_search"})) == [
        "research-agent",
        "coder",
    ]
    assert slugs(await client.get(BASE, params={"runtime": "custom"})) == ["coder"]
    assert slugs(await client.get(BASE, params={"status": "active"})) == [
        "research-agent",
        "writer",
    ]

    page = (await client.get(BASE, params={"limit": 1, "offset": 1})).json()
    assert page["total"] == 3 and [a["slug"] for a in page["items"]] == ["writer"]

    assert (await client.get(BASE, params={"limit": 0})).status_code == 422
    assert (await client.get(BASE, params={"status": "bogus"})).status_code == 422


# --- update + versions --------------------------------------------------------


async def test_patch_definition_creates_new_version(client):
    agent = await create(client)
    resp = await client.patch(
        f"{BASE}/{agent['id']}",
        json={"instructions": "Be brief.", "limits": {"max_steps": 3, "timeout_seconds": 30}},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["current_version"] == 2
    assert updated["instructions"] == "Be brief."
    assert updated["tools"] == agent["tools"]  # untouched fields are kept
    assert updated["limits"] == {"max_steps": 3, "timeout_seconds": 30}

    versions = (await client.get(f"{BASE}/research-agent/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["config_snapshot"]["instructions"] == agent["instructions"]
    assert versions[0]["config_snapshot"]["instructions"] == "Be brief."

    v1 = (await client.get(f"{BASE}/research-agent/versions/1")).json()
    assert v1["config_snapshot"]["limits"] == {"max_steps": 10, "timeout_seconds": 120}
    assert (await client.get(f"{BASE}/research-agent/versions/9")).status_code == 404


async def test_patch_status_only_or_no_change_keeps_version(client):
    await create(client)
    resp = await client.patch(f"{BASE}/research-agent", json={"status": "disabled"})
    assert resp.json()["current_version"] == 1
    assert resp.json()["enabled"] is False

    same = await client.patch(f"{BASE}/research-agent", json={"name": "Research Agent"})
    assert same.json()["current_version"] == 1
    assert len((await client.get(f"{BASE}/research-agent/versions")).json()) == 1


async def test_patch_expected_version_conflict(client):
    await create(client)
    ok = await client.patch(f"{BASE}/research-agent", json={"model": "m2", "expected_version": 1})
    assert ok.status_code == 200 and ok.json()["current_version"] == 2
    stale = await client.patch(
        f"{BASE}/research-agent", json={"model": "m3", "expected_version": 1}
    )
    assert stale.status_code == 409
    assert (await client.get(f"{BASE}/research-agent")).json()["model"] == "m2"


async def test_patch_rejects_slug_change_and_unknown_fields(client):
    await create(client)
    assert (await client.patch(f"{BASE}/research-agent", json={"slug": "x"})).status_code == 422
    assert (await client.patch(f"{BASE}/research-agent", json={"secret": "x"})).status_code == 422
    assert (await client.patch(f"{BASE}/missing", json={"name": "x"})).status_code == 404
    assert (await client.patch(f"{BASE}/research-agent", json={"name": None})).status_code == 422
    cleared = await client.patch(f"{BASE}/research-agent", json={"description": None})
    assert cleared.status_code == 200 and cleared.json()["description"] is None


# --- archive --------------------------------------------------------------------


async def test_delete_archives_agent(client):
    agent = await create(client)
    assert (await client.delete(f"{BASE}/research-agent")).status_code == 204

    assert (await client.get(f"{BASE}/research-agent")).status_code == 404
    assert (await client.get(BASE)).json()["total"] == 0
    archived = (await client.get(BASE, params={"status": "archived"})).json()
    assert [a["id"] for a in archived["items"]] == [agent["id"]]
    # History survives, and the slug stays reserved.
    assert len((await client.get(f"{BASE}/{agent['id']}/versions")).json()) == 1
    assert (await client.post(BASE, json=research_agent())).status_code == 409
    assert (await client.delete(f"{BASE}/research-agent")).status_code == 404
    assert (await client.patch(f"{BASE}/research-agent", json={"name": "x"})).status_code == 404


# --- audit ----------------------------------------------------------------------


async def test_writes_are_audited_with_actor(client, db_session):
    agent = await create(client)
    await client.patch(f"{BASE}/research-agent", json={"model": "m2"}, headers={"X-Actor": "dana"})
    await client.delete(f"{BASE}/research-agent", headers={"X-Actor": "dana"})

    rows = (await db_session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    assert [(r.actor, r.action) for r in rows] == [
        ("anonymous", "agent.created"),
        ("dana", "agent.updated"),
        ("dana", "agent.archived"),
    ]
    assert all(r.resource_type == "agent" and r.resource_id == agent["id"] for r in rows)
    assert rows[1].meta["changed_fields"] == ["model"]


async def test_failed_create_leaves_no_rows(client, db_session):
    await create(client)
    await client.post(BASE, json=research_agent())  # 409
    counts = (
        await db_session.execute(
            text(
                "SELECT (SELECT count(*) FROM agents), (SELECT count(*) FROM agent_versions),"
                " (SELECT count(*) FROM audit_logs)"
            )
        )
    ).one()
    assert tuple(counts) == (1, 1, 1)


# --- registry (orchestrator read path) -----------------------------------------


async def test_registry_returns_agent_config(client, db_session):
    agent = await create(client)
    registry = AgentRegistry(db_session)

    cfg = await registry.get_agent("research-agent")
    assert cfg.id == "research-agent"
    assert cfg.tools == ["web_search", "document_reader"]
    assert cfg.limits.max_steps == 10
    assert cfg.enabled is True
    assert (await registry.get_agent(agent["id"])).id == "research-agent"


async def test_registry_disabled_and_archived(client, db_session):
    await create(client)
    await client.patch(f"{BASE}/research-agent", json={"status": "disabled"})
    assert (await AgentRegistry(db_session).get_agent("research-agent")).enabled is False

    await client.delete(f"{BASE}/research-agent")
    db_session.expire_all()
    with pytest.raises(AgentNotFoundError):
        await AgentRegistry(db_session).get_agent("research-agent")
    with pytest.raises(LookupError):  # what the orchestrator catches
        await AgentRegistry(db_session).get_agent("never-existed")
