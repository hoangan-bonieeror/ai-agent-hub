"""Cost tracking and the policy engine.

Unit tests need nothing; API tests need PostgreSQL (TEST_DATABASE_URL) and use
the fake runtime, whose token counts are a rough estimate of text length.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.main import app
from app.orchestrator import AgentOrchestrator, InMemoryAgentRegistry, WorkflowDefinition
from app.orchestrator.dependencies import get_runtime_registry
from app.orchestrator.workflow import RunStatus
from app.policy.engine import InMemoryPolicyStore, PolicyDenied, PolicyEngine
from app.policy.pricing import Price, PriceBook
from app.policy.rules import PolicySpec, period_start, tool_denied
from app.runtimes import AgentConfig, AgentResultStatus, RuntimeRegistry, TokenUsage
from app.runtimes.fake_runtime import FakeAgentRuntime
from tests.test_mcp import RecordingModel, echo_spec, mcp_runtime
from tests.test_runtime_orchestrator import _message

# --- pricing -----------------------------------------------------------------------


def test_price_lookup_prefers_exact_then_longest_pattern():
    book = PriceBook(
        [
            Price("gpt-4.1*", Decimal(2), Decimal(8)),
            Price("gpt-4.1-mini*", Decimal("0.4"), Decimal("1.6")),
            Price("gpt-4.1-mini-special", Decimal(1), Decimal(1)),
        ]
    )
    assert book.find("gpt-4.1-mini-special").model == "gpt-4.1-mini-special"
    assert book.find("gpt-4.1-mini-2025-04-14").model == "gpt-4.1-mini*"
    assert book.find("gpt-4.1").model == "gpt-4.1*"
    assert book.find("o3") is None and book.find(None) is None


def test_cost_is_per_million_tokens():
    book = PriceBook([Price("m", Decimal("2.50"), Decimal("10"))])
    usage = TokenUsage(requests=1, input_tokens=1000, output_tokens=500, total_tokens=1500)
    # 1000 * 2.50 / 1e6 + 500 * 10 / 1e6
    assert book.cost("m", usage) == Decimal("0.007500")
    assert book.cost("m", usage.model_dump()) == Decimal("0.007500")
    assert book.cost("other", usage) is None


# --- rules -------------------------------------------------------------------------


def spec(**kw) -> PolicySpec:
    return PolicySpec.model_validate({"name": "p", **kw})


def test_policy_validation():
    assert spec(kind="budget", rules={"limit_usd": 5}).rules == {
        "limit_usd": 5,
        "period": "month",
        "action": "block",
    }
    with pytest.raises(ValidationError, match="needs a target"):
        spec(kind="budget", scope="agent", rules={"limit_usd": 5})
    with pytest.raises(ValidationError, match="no target"):
        spec(kind="budget", target="x", rules={"limit_usd": 5})
    with pytest.raises(ValidationError):
        spec(kind="budget", rules={"limit_usd": 0})
    with pytest.raises(ValidationError):
        spec(kind="tools", rules={"deny": []})
    with pytest.raises(ValidationError):
        spec(kind="models", rules={"allow": ["x"], "extra": 1})


def test_tool_patterns():
    assert tool_denied("web_search", ["web_search"])
    assert tool_denied("mcp:files/read", ["mcp:files"])  # a server denies its tools
    assert not tool_denied("mcp:files2/read", ["mcp:files"])
    assert tool_denied("mcp:files/write_text", ["mcp:*/write_*"])
    assert not tool_denied("memory", ["mcp:*"])


def test_period_start_is_utc():
    now = datetime(2026, 11, 28, 15, 30, tzinfo=UTC)
    assert period_start("day", now) == datetime(2026, 11, 28, tzinfo=UTC)
    assert period_start("month", now) == datetime(2026, 11, 1, tzinfo=UTC)
    assert period_start("total", now) is None


# --- engine ------------------------------------------------------------------------


def agent(**kw) -> AgentConfig:
    return AgentConfig(
        **{"id": "writer", "name": "Writer", "instructions": "x", "runtime": "fake", **kw}
    )


def check(policies, a=None, spend=None, workflow=None):
    engine = PolicyEngine(InMemoryPolicyStore(policies, spend))
    return asyncio.run(engine.check(a or agent(), workflow=workflow))


def test_no_policies_changes_nothing():
    decision = check([], agent(tools=["web_search"]))
    assert decision.agent.tools == ["web_search"] and decision.metadata() is None


def test_model_rules():
    rule = spec(kind="models", rules={"allow": ["gpt-4.1-mini*"]})
    assert check([rule], agent(model="gpt-4.1-mini")).applied == ["p"]
    with pytest.raises(PolicyDenied, match="does not allow model 'gpt-4.1'"):
        check([rule], agent(model="gpt-4.1"))
    with pytest.raises(PolicyDenied, match="default model"):
        check([rule], agent())


def test_tool_rules_remove_grants_and_block_server_tools():
    rule = spec(kind="tools", rules={"deny": ["web_search", "mcp:echo/shout"]})
    decision = check([rule], agent(tools=["web_search", "memory", "mcp:echo", "mcp:echo/shout"]))
    assert decision.agent.tools == ["memory", "mcp:echo"]
    assert decision.removed_tools == ["web_search", "mcp:echo/shout"]
    assert decision.agent.blocked_tools == ["mcp:echo/shout", "web_search"]


def test_policies_apply_by_scope():
    rule = {"kind": "models", "rules": {"allow": ["none"]}}
    other_agent = spec(scope="agent", target="someone-else", **rule)
    this_workflow = spec(scope="workflow", target="weekly", **rule)
    assert check([other_agent, this_workflow], agent(model="m")).applied == []
    with pytest.raises(PolicyDenied):
        check([this_workflow], agent(model="m"), workflow="weekly")
    disabled = spec(enabled=False, **rule)
    assert check([disabled], agent(model="m")).applied == []


def test_budgets_block_or_warn():
    block = spec(kind="budget", scope="agent", target="writer", rules={"limit_usd": 1})
    warn = spec(kind="budget", name="soft", rules={"limit_usd": 1, "action": "warn"})
    under = {("agent", "writer"): Decimal("0.99")}
    over = {("agent", "writer"): Decimal("1.00"), ("global", None): Decimal(3)}
    assert check([block], spend=under).warnings == []
    with pytest.raises(PolicyDenied, match=r"Budget 'p' reached: \$1.00 of \$1.00 this month"):
        check([block], spend=over)
    decision = check([warn], spend=over)
    assert decision.warnings == ["Budget 'soft' reached: $3.00 of $1.00 this month"]
    assert decision.metadata()["warnings"] == decision.warnings


def test_denied_step_fails_the_workflow_run():
    runtime = FakeAgentRuntime()
    agents = [agent(id="a", model="cheap"), agent(id="b", model="expensive")]
    engine = PolicyEngine(InMemoryPolicyStore([spec(kind="models", rules={"allow": ["cheap"]})]))
    orchestrator = AgentOrchestrator(
        InMemoryAgentRegistry(agents), RuntimeRegistry([runtime]), policy=engine
    )
    wf = WorkflowDefinition(
        name="two",
        steps=[
            {"id": "first", "agent_id": "a", "task": "t"},
            {"id": "second", "agent_id": "b", "task": "t", "uses": ["first"]},
        ],
    )
    run = asyncio.run(orchestrator.run_workflow(wf, {}))
    assert run.status == RunStatus.FAILED
    assert run.task("first").status == RunStatus.COMPLETED
    assert "does not allow model 'expensive'" in run.task("second").error
    assert [c[0] for c in runtime.calls] == ["a"]  # b never reached the runtime


def test_blocked_mcp_tools_are_hidden_from_the_model():
    model = RecordingModel([[_message("ok")]])
    rt = mcp_runtime(model, echo_spec())
    config = AgentConfig(
        id="a",
        name="A",
        instructions="x",
        tools=["mcp:echo"],
        blocked_tools=["mcp:echo/shout", "mcp:*/secret_*"],
    )
    result = asyncio.run(rt.execute(config, {"task": "t"}))
    assert result.status == AgentResultStatus.COMPLETED, result.error
    assert model.seen[0]["tools"] == ["add"]


# --- API (PostgreSQL) --------------------------------------------------------------


@pytest.fixture
def fake_runtime():
    runtime = FakeAgentRuntime()
    app.dependency_overrides[get_runtime_registry] = lambda: RuntimeRegistry([runtime])
    yield runtime
    app.dependency_overrides.pop(get_runtime_registry, None)


async def make_agent(client, slug, model="test-model", tools=()):
    body = {
        "slug": slug,
        "name": slug.title(),
        "runtime": "fake",
        "model": model,
        "instructions": "Do the task.",
        "tools": list(tools),
    }
    resp = await client.post("/api/v1/agents", json=body)
    assert resp.status_code == 201, resp.text


async def set_price(client, model, inp, out):
    resp = await client.put(
        "/api/v1/model-prices",
        json={"model": model, "input_per_mtok": inp, "output_per_mtok": out},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.usefixtures("clean_db", "fake_runtime")
async def test_direct_runs_are_recorded_and_priced(client):
    await make_agent(client, "writer")
    await set_price(client, "test-*", 1000, 2000)  # dear, so the numbers are visible

    resp = await client.post("/api/v1/agents/writer/run", json={"task": "Write a haiku"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    usage = body["usage"]
    expected = (usage["input_tokens"] * 1000 + usage["output_tokens"] * 2000) / 1_000_000
    assert body["model"] == "test-model"
    assert body["cost_usd"] == pytest.approx(expected)

    execution = (await client.get(f"/api/v1/executions/{body['execution_id']}")).json()
    assert execution["task_id"] is None and execution["run_id"] is None
    assert execution["cost_usd"] == pytest.approx(expected)

    costs = (await client.get("/api/v1/costs?days=7")).json()
    assert costs["total"]["executions"] == 1
    assert costs["total"]["cost_usd"] == pytest.approx(expected)
    assert costs["by_agent"][0]["key"] == "writer"
    assert costs["by_model"][0]["key"] == "test-model"
    assert costs["by_workflow"][0]["key"] is None  # direct run
    assert len(costs["by_day"]) == 7 and costs["by_day"][-1]["executions"] == 1
    assert costs["unpriced_models"] == []


@pytest.mark.usefixtures("clean_db", "fake_runtime")
async def test_unpriced_calls_can_be_repriced(client):
    await make_agent(client, "writer", model="new-model")
    await client.post("/api/v1/agents/writer/run", json={"task": "t"})
    costs = (await client.get("/api/v1/costs")).json()
    assert costs["total"]["unpriced_executions"] == 1
    assert costs["unpriced_models"][0]["key"] == "new-model"

    await set_price(client, "new-model", 1, 1)
    assert (await client.post("/api/v1/costs/reprice")).json() == {"updated": 1}
    costs = (await client.get("/api/v1/costs")).json()
    assert costs["total"]["unpriced_executions"] == 0 and costs["total"]["cost_usd"] > 0


@pytest.mark.usefixtures("clean_db", "fake_runtime")
async def test_workflow_runs_carry_their_cost(client):
    await make_agent(client, "writer")
    await set_price(client, "test-model", 1000, 1000)
    wf = {"name": "weekly", "steps": [{"id": "a", "agent_id": "writer", "task": "go"}]}
    assert (await client.post("/api/v1/workflows", json=wf)).status_code == 201
    run = (await client.post("/api/v1/workflows/weekly/run", json={"input": {}})).json()
    assert run["status"] == "succeeded", run

    detail = (await client.get(f"/api/v1/runs/{run['run_id']}")).json()
    assert detail["cost_usd"] > 0 and detail["unpriced_executions"] == 0
    assert detail["tasks"][0]["executions"][0]["model"] == "test-model"
    listed = (await client.get("/api/v1/runs")).json()["items"][0]
    assert listed["cost_usd"] == pytest.approx(detail["cost_usd"])
    by_workflow = (await client.get("/api/v1/costs")).json()["by_workflow"]
    assert by_workflow[0]["key"] == "weekly"


@pytest.mark.usefixtures("clean_db", "fake_runtime")
async def test_budget_blocks_direct_and_workflow_runs(client):
    await make_agent(client, "writer")
    await set_price(client, "test-model", 100_000, 100_000)
    policy = {
        "name": "writer-cap",
        "kind": "budget",
        "scope": "agent",
        "target": "writer",
        "rules": {"limit_usd": 0.01, "period": "day"},
    }
    resp = await client.post("/api/v1/policies", json=policy)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"]["spent_usd"] == 0

    assert (await client.post("/api/v1/agents/writer/run", json={"task": "t"})).status_code == 200
    blocked = await client.post("/api/v1/agents/writer/run", json={"task": "t"})
    assert blocked.status_code == 403
    assert "Budget 'writer-cap' reached" in blocked.json()["detail"]

    status = (await client.get("/api/v1/policies")).json()["items"][0]["status"]
    assert status["reached"] and status["spent_usd"] > 0.01

    wf = {"name": "w", "steps": [{"id": "a", "agent_id": "writer", "task": "go"}]}
    await client.post("/api/v1/workflows", json=wf)
    run = (await client.post("/api/v1/workflows/w/run", json={"input": {}})).json()
    assert run["status"] == "failed" and "writer-cap" in run["error"]

    check = await client.post("/api/v1/policies/check", json={"agent": "writer"})
    assert check.json()["allowed"] is False

    # The refused attempt is recorded on the run but is not a priced call.
    total = (await client.get("/api/v1/costs")).json()["total"]
    assert total["executions"] == 1 and total["unpriced_executions"] == 0


@pytest.mark.usefixtures("clean_db", "fake_runtime")
async def test_policy_crud_is_audited(client):
    await make_agent(client, "writer", tools=["web_search", "memory"])
    resp = await client.post(
        "/api/v1/policies",
        json={"name": "no-web", "kind": "tools", "rules": {"deny": ["web_search"]}},
        headers={"X-Actor": "an"},
    )
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    dup = await client.post(
        "/api/v1/policies", json={"name": "no-web", "kind": "tools", "rules": {"deny": ["x"]}}
    )
    assert dup.status_code == 409

    check = (await client.post("/api/v1/policies/check", json={"agent": "writer"})).json()
    assert check["allowed"] and check["tools"] == ["memory"]
    assert check["removed_tools"] == ["web_search"]

    bad = await client.patch(f"/api/v1/policies/{pid}", json={"scope": "agent"})
    assert bad.status_code == 422
    resp = await client.patch(f"/api/v1/policies/{pid}", json={"enabled": False})
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    check = (await client.post("/api/v1/policies/check", json={"agent": "writer"})).json()
    assert check["tools"] == ["web_search", "memory"]

    assert (await client.delete(f"/api/v1/policies/{pid}")).status_code == 204
    log = (await client.get("/api/v1/audit-logs?resource_type=policy")).json()["items"]
    assert [e["action"] for e in log] == ["policy.deleted", "policy.updated", "policy.created"]
    assert log[-1]["actor"] == "an"
    assert log[1]["metadata"]["before"] == {"enabled": True}


@pytest.mark.usefixtures("clean_db")
async def test_prices_crud(client):
    first = await set_price(client, "gpt-x", 1.25, 10)
    again = await set_price(client, "gpt-x", 2, 10)
    assert first["id"] == again["id"] and again["input_per_mtok"] == 2
    items = (await client.get("/api/v1/model-prices")).json()["items"]
    assert [i["model"] for i in items] == ["gpt-x"]
    bad = await client.put(
        "/api/v1/model-prices", json={"model": "m", "input_per_mtok": -1, "output_per_mtok": 1}
    )
    assert bad.status_code == 422
    assert (await client.delete(f"/api/v1/model-prices/{first['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/model-prices/{first['id']}")).status_code == 404
