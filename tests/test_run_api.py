"""Run endpoints against a real PostgreSQL (see conftest: TEST_DATABASE_URL).

Agents are registered with runtime "fake" and the runtime registry dependency
is overridden, so no model provider is called.
"""

import pytest
from sqlalchemy import select

from app.agents.samples import RESEARCH_FINANCE_REVIEW, SAMPLE_AGENTS
from app.main import app
from app.models import Execution, Task, WorkflowRun
from app.orchestrator.dependencies import get_runtime_registry
from app.runtimes import RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime

pytestmark = pytest.mark.usefixtures("clean_db")


def _fail(config, task_input):
    raise ValueError("finance data unavailable")


@pytest.fixture
def fake_runtime():
    runtime = FakeAgentRuntime(
        {
            "research-agent": lambda c, i: {"options": ["Civic", "Corolla"]},
            "finance-agent": lambda c, i: f"plan for {i['previous_outputs']['research']}",
        }
    )
    app.dependency_overrides[get_runtime_registry] = lambda: RuntimeRegistry([runtime])
    yield runtime
    app.dependency_overrides.pop(get_runtime_registry, None)


async def seed_agents(client, **status_by_slug):
    for agent in SAMPLE_AGENTS:
        body = {
            "slug": agent.id,
            "name": agent.name,
            "description": agent.description,
            "runtime": "fake",
            "model": "test-model",
            "instructions": agent.instructions,
            "tools": agent.tools,
            "capabilities": agent.capabilities,
            "limits": agent.limits.model_dump(),
            "status": status_by_slug.get(agent.id, "active"),
        }
        resp = await client.post("/api/v1/agents", json=body)
        assert resp.status_code == 201, resp.text


async def create_sample_workflow(client):
    resp = await client.post(
        "/api/v1/workflows", json=RESEARCH_FINANCE_REVIEW.model_dump(mode="json")
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_workflow_versions_by_name(client):
    first = await create_sample_workflow(client)
    second = await create_sample_workflow(client)
    assert (first["version"], second["version"]) == (1, 2)
    assert first["definition"]["steps"][0]["agent_id"] == "research-agent"


async def test_create_workflow_rejects_bad_step_reference(client):
    body = {"name": "bad", "steps": [{"id": "a", "agent_id": "x", "task": "t", "uses": ["zz"]}]}
    resp = await client.post("/api/v1/workflows", json=body)
    assert resp.status_code == 422


async def test_run_sample_workflow_end_to_end_and_persist(client, db_session, fake_runtime):
    await seed_agents(client)
    wf = await create_sample_workflow(client)

    resp = await client.post(
        f"/api/v1/workflows/{wf['name']}/run",
        json={"input": {"budget_usd": 15000}},
        headers={"X-Actor": "an"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert [t["status"] for t in body["tasks"]] == ["succeeded"] * 3
    assert body["tasks"][0]["output"] == {"options": ["Civic", "Corolla"]}
    assert [c[0] for c in fake_runtime.calls] == [
        "research-agent",
        "finance-agent",
        "reviewer-agent",
    ]

    run = (await db_session.execute(select(WorkflowRun))).scalar_one()
    assert str(run.id) == body["run_id"]
    assert run.status == "succeeded" and run.requested_by == "an"
    assert run.input == {"budget_usd": 15000}
    assert run.output == {"result": body["output"]}
    assert run.started_at is not None and run.completed_at is not None
    tasks = (await db_session.execute(select(Task))).scalars().all()
    assert sorted(t.name for t in tasks) == ["finance", "research", "review"]
    assert all(t.status == "succeeded" and t.assigned_agent_id for t in tasks)
    executions = (await db_session.execute(select(Execution))).scalars().all()
    assert len(executions) == 3
    assert all(e.status == "succeeded" and e.agent_version == 1 for e in executions)


async def test_failed_step_is_recorded(client, db_session, fake_runtime):
    fake_runtime.responders["finance-agent"] = _fail
    await seed_agents(client)
    wf = await create_sample_workflow(client)

    resp = await client.post(f"/api/v1/workflows/{wf['id']}/run", json={"input": {}})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "failed" and "finance data unavailable" in body["error"]
    assert [t["status"] for t in body["tasks"]] == ["succeeded", "failed", "skipped"]

    run = (await db_session.execute(select(WorkflowRun))).scalar_one()
    assert run.status == "failed"
    statuses = {t.name: t.status for t in (await db_session.execute(select(Task))).scalars()}
    assert statuses == {"research": "succeeded", "finance": "failed", "review": "skipped"}
    executions = (await db_session.execute(select(Execution))).scalars().all()
    assert sorted(e.status for e in executions) == ["failed", "succeeded"]


async def test_disabled_agent_fails_the_run(client, fake_runtime):
    await seed_agents(client, **{"reviewer-agent": "disabled"})
    wf = await create_sample_workflow(client)
    body = (await client.post(f"/api/v1/workflows/{wf['id']}/run", json={})).json()
    assert body["status"] == "failed" and "disabled" in body["tasks"][2]["error"]


async def test_run_unknown_workflow_404(client, fake_runtime):
    resp = await client.post("/api/v1/workflows/nope/run", json={})
    assert resp.status_code == 404


async def test_run_agent_directly(client, fake_runtime):
    await seed_agents(client, **{"finance-agent": "disabled"})

    resp = await client.post(
        "/api/v1/agents/research-agent/run", json={"task": "find cars", "input": {"zip": "94103"}}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed" and body["output"] == {"options": ["Civic", "Corolla"]}
    assert fake_runtime.calls[-1][1] == {"task": "find cars", "zip": "94103"}

    assert (await client.post("/api/v1/agents/nope/run", json={"task": "x"})).status_code == 404
    disabled = await client.post("/api/v1/agents/finance-agent/run", json={"task": "x"})
    assert disabled.status_code == 409
    assert (await client.post("/api/v1/agents/research-agent/run", json={})).status_code == 422
