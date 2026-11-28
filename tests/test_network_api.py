"""GET /api/v1/network: agents linked by the runs they shared.

Needs PostgreSQL (TEST_DATABASE_URL); agents use the fake runtime.
"""

import pytest

from app.agents.samples import RESEARCH_FINANCE_REVIEW, SAMPLE_AGENTS
from tests.test_run_api import fake_runtime, seed_agents  # noqa: F401

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_empty_network_lists_agents_without_links(client):
    await seed_agents(client)
    resp = await client.get("/api/v1/network")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["runs"] == 0 and body["links"] == []
    assert sorted(n["id"] for n in body["nodes"]) == sorted(a.id for a in SAMPLE_AGENTS)
    assert all(n["runs"] == 0 and n["last_status"] is None for n in body["nodes"])


async def test_links_count_shared_runs_and_handoffs(client, fake_runtime):  # noqa: F811
    await seed_agents(client)
    resp = await client.post(
        "/api/v1/workflows", json=RESEARCH_FINANCE_REVIEW.model_dump(mode="json")
    )
    assert resp.status_code == 201, resp.text
    for _ in range(2):
        run = await client.post(
            f"/api/v1/workflows/{RESEARCH_FINANCE_REVIEW.name}/run", json={"input": {}}
        )
        assert run.json()["status"] == "succeeded"

    body = (await client.get("/api/v1/network?days=7")).json()
    assert body["runs"] == 2
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["research-agent"]["runs"] == 2
    assert nodes["research-agent"]["last_status"] == "succeeded"
    assert nodes["research-agent"]["last_active_at"]

    links = {(link["source"], link["target"]): link for link in body["links"]}
    assert set(links) == {
        ("finance-agent", "research-agent"),
        ("finance-agent", "reviewer-agent"),
        ("research-agent", "reviewer-agent"),
    }
    assert all(link["runs"] == 2 for link in links.values())
    # finance waits for research in every run, never the other way round
    assert links[("finance-agent", "research-agent")]["handoffs"] == {
        "research-agent": 2,
        "finance-agent": 0,
    }


async def test_archived_agents_are_left_out(client):
    await seed_agents(client)
    await client.delete("/api/v1/agents/finance-agent")
    body = (await client.get("/api/v1/network")).json()
    assert "finance-agent" not in {n["id"] for n in body["nodes"]}
