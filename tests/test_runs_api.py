"""Phase 2 API: background runs, SSE progress, run/task/execution status.

Needs PostgreSQL (TEST_DATABASE_URL); agents use the fake runtime.
"""

import asyncio
import json
import uuid

import pytest

from app.agents.samples import RESEARCH_AND_REPORT, SAMPLE_AGENTS
from app.main import app
from app.orchestrator.dependencies import get_runtime_registry
from app.orchestrator.runner import RunManager, get_run_manager
from app.runtimes import RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime
from tests.test_run_api import seed_agents

pytestmark = pytest.mark.usefixtures("clean_db")


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if fields:
            events.append(
                {
                    "id": fields.get("id"),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return events


class Verdicts:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)

    def __call__(self, config, task_input):
        return self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]


@pytest.fixture
def runtime():
    rt = FakeAgentRuntime(
        {
            "reviewer-agent": Verdicts(
                {"approved": False, "feedback": "cite sources"},
                {"approved": True, "summary": "solid"},
            )
        }
    )
    app.dependency_overrides[get_runtime_registry] = lambda: RuntimeRegistry([rt])
    yield rt
    app.dependency_overrides.pop(get_runtime_registry, None)


async def create_report_workflow(client):
    resp = await client.post("/api/v1/workflows", json=RESEARCH_AND_REPORT.model_dump(mode="json"))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def start_background(client, name="research-and-report", **input_):
    resp = await client.post(
        f"/api/v1/workflows/{name}/run",
        json={"input": input_ or {"topic": "AI Agent Hub"}, "wait": False},
        headers={"X-Actor": "an"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def read_events(client, run_id, **headers):
    resp = await client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


async def test_background_run_streams_progress_and_records_state(client, runtime):
    await seed_agents(client)
    await create_report_workflow(client)

    started = await start_background(client)
    run_id = started["run_id"]
    assert started["links"]["events"] == f"/api/v1/runs/{run_id}/events"
    assert [t["step_id"] for t in started["tasks"]] == [s.id for s in RESEARCH_AND_REPORT.steps]

    events = await read_events(client, run_id)
    types = [e["event"] for e in events]
    assert types[0] == "workflow.started" and types[-1] == "workflow.completed"
    assert [int(e["id"]) for e in events] == list(range(1, len(events) + 1))
    assert types.count("task.revision_requested") == 3
    assert types.count("review.completed") == 2
    assert all(e["data"]["run_id"] == run_id for e in events)

    body = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert body["status"] == "succeeded" and body["requested_by"] == "an"
    assert set(body["output"]) == {"plan", "research", "competitors", "architecture", "review"}
    tasks = {t["step_id"]: t for t in body["tasks"]}
    assert list(tasks) == ["plan", "research", "competitors", "architecture", "review", "report"]
    assert tasks["report"]["kind"] == "aggregate" and tasks["report"]["executions"] == []
    assert tasks["research"]["depends_on"] == ["plan"]
    assert tasks["research"]["retry_count"] == 1 and len(tasks["research"]["executions"]) == 2
    assert tasks["review"]["output"] == {"approved": True, "summary": "solid", "revisions": 1}
    assert tasks["plan"]["agent_id"] == "planner-agent"
    assert all(t["status"] == "succeeded" for t in body["tasks"])

    # task and execution detail
    task = (await client.get(f"/api/v1/tasks/{tasks['research']['task_id']}")).json()
    assert task["run_id"] == run_id and task["input"]["revision"]["feedback"] == "cite sources"
    first, second = task["executions"]
    execution = (await client.get(f"/api/v1/executions/{second['execution_id']}")).json()
    assert execution["status"] == "succeeded" and execution["agent_id"] == "research-agent"
    assert execution["run_id"] == run_id and execution["step_id"] == "research"
    assert execution["agent_version"] == 1
    assert execution["logs"] == [
        {"revision": {"round": 1, "reviewer": "review", "feedback": "cite sources"}}
    ]
    assert (await client.get(f"/api/v1/executions/{first['execution_id']}")).json()["logs"] == []

    # resume after event 3, and a server that no longer holds the events
    resumed = await read_events(client, run_id, **{"Last-Event-ID": "3"})
    assert resumed[0]["id"] == "4" and len(resumed) == len(events) - 3
    app.dependency_overrides[get_run_manager] = lambda: RunManager()
    try:
        snapshot = await read_events(client, run_id)
    finally:
        app.dependency_overrides.pop(get_run_manager)
    assert snapshot == [
        {
            "id": None,
            "event": "run.status",
            "data": {"run_id": run_id, "status": "succeeded", "error": None},
        }
    ]


async def test_live_status_and_cancel(client, runtime):
    gate = asyncio.Event()

    async def blocked(config, task_input):
        await gate.wait()
        return "never"

    runtime.responders["competitor-agent"] = blocked
    await seed_agents(client)
    await create_report_workflow(client)
    run_id = (await start_background(client))["run_id"]

    for _ in range(100):  # wait until the parallel steps are underway
        body = (await client.get(f"/api/v1/runs/{run_id}")).json()
        statuses = {t["step_id"]: t["status"] for t in body["tasks"]}
        if statuses["competitors"] == "running" and statuses["research"] == "succeeded":
            break
        await asyncio.sleep(0.02)
    assert body["status"] == "running"
    assert statuses["plan"] == "succeeded" and statuses["review"] == "pending"
    running = next(t for t in body["tasks"] if t["step_id"] == "competitors")
    assert running["executions"][0]["status"] == "running"

    resp = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert resp.status_code == 202
    body = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert body["status"] == "cancelled"
    statuses = {t["step_id"]: t["status"] for t in body["tasks"]}
    assert statuses["competitors"] == "cancelled" and statuses["report"] == "skipped"
    cancelled = next(t for t in body["tasks"] if t["step_id"] == "competitors")
    assert cancelled["executions"][0]["status"] == "cancelled"
    assert (await client.post(f"/api/v1/runs/{run_id}/cancel")).status_code == 409

    events = await read_events(client, run_id)
    assert events[-1]["event"] == "workflow.cancelled"


async def test_failed_background_run(client, runtime):
    def boom(config, task_input):
        raise RuntimeError("search quota exceeded")

    runtime.responders["research-agent"] = boom
    await seed_agents(client)
    await create_report_workflow(client)
    run_id = (await start_background(client))["run_id"]
    events = await read_events(client, run_id)
    assert events[-1]["event"] == "workflow.failed"
    failed = next(e for e in events if e["event"] == "task.failed")
    assert failed["data"]["step_id"] == "research"

    body = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert body["status"] == "failed" and "search quota exceeded" in body["error"]
    research = next(t for t in body["tasks"] if t["step_id"] == "research")
    assert "search quota exceeded" in research["error"]
    assert research["executions"][0]["status"] == "failed"


async def test_list_runs_and_workflows(client, runtime):
    await seed_agents(client)
    await create_report_workflow(client)
    resp = await client.post(
        "/api/v1/workflows/research-and-report/run", json={"input": {"topic": "x"}}
    )
    assert resp.status_code == 200 and resp.json()["status"] == "succeeded"

    runs = (await client.get("/api/v1/runs", params={"workflow": "research-and-report"})).json()
    assert len(runs["items"]) == 1 and runs["items"][0]["status"] == "succeeded"
    assert (await client.get("/api/v1/runs", params={"status": "failed"})).json()["items"] == []

    listed = (await client.get("/api/v1/workflows")).json()["items"]
    assert [w["name"] for w in listed] == ["research-and-report"]
    one = (await client.get("/api/v1/workflows/research-and-report")).json()
    assert one["definition"]["steps"][4]["kind"] == "review"


async def test_invalid_dag_rejected(client):
    body = {
        "name": "loop",
        "steps": [
            {"id": "a", "agent_id": "x", "task": "t", "depends_on": ["b"]},
            {"id": "b", "agent_id": "x", "task": "t", "depends_on": ["a"]},
        ],
    }
    resp = await client.post("/api/v1/workflows", json=body)
    assert resp.status_code == 422 and "cycle" in resp.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/runs/not-a-uuid",
        f"/api/v1/runs/{uuid.uuid4()}",
        f"/api/v1/runs/{uuid.uuid4()}/events",
        "/api/v1/tasks/not-a-uuid",
        f"/api/v1/tasks/{uuid.uuid4()}",
        f"/api/v1/executions/{uuid.uuid4()}",
        "/api/v1/workflows/nope",
    ],
)
async def test_unknown_ids_404(client, path):
    assert (await client.get(path)).status_code == 404


def test_sample_agents_include_report_team():
    assert {"planner-agent", "competitor-agent", "architecture-agent"} <= {
        a.id for a in SAMPLE_AGENTS
    }
