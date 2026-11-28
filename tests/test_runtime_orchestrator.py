"""Tests for the runtime adapters and the sequential orchestrator.

The OpenAI adapter is exercised through the real Agents SDK ``Runner`` with a
scripted model provider, so no network or API key is needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from agents import Usage, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model, ModelProvider
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from app.agents.samples import RESEARCH_FINANCE_REVIEW, SAMPLE_AGENTS
from app.orchestrator import (
    AgentNotFoundError,
    AgentOrchestrator,
    AgentUnavailableError,
    InMemoryAgentRegistry,
    RunStatus,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowStep,
)
from app.runtimes import (
    AgentConfig,
    AgentLimits,
    AgentResultStatus,
    RuntimeNotFoundError,
    RuntimeRegistry,
)
from app.runtimes.fake_runtime import FakeAgentRuntime
from app.runtimes.openai_runtime import OpenAIAgentRuntime


def run(coro):
    return asyncio.run(coro)


# --- scripted model for the OpenAI Agents SDK ---------------------------------


def _message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg_1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def _tool_call(name: str, args: dict[str, Any]) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        id="fc_1", call_id="call_1", type="function_call", name=name, arguments=json.dumps(args)
    )


class ScriptedModel(Model):
    def __init__(self, turns: list[list[Any]], delay: float = 0.0) -> None:
        self.turns = list(turns)
        self.delay = delay
        self.seen: list[dict[str, Any]] = []

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        **kw,
    ):
        text = (
            input
            if isinstance(input, str)
            else "\n".join(str(i.get("content", "")) for i in input if isinstance(i, dict))
        )
        self.seen.append(
            {"instructions": system_instructions, "input": text, "tools": [t.name for t in tools]}
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        output = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        return ModelResponse(
            output=output,
            usage=Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15),
            response_id=None,
        )

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


class ScriptedProvider(ModelProvider):
    def __init__(self, model: ScriptedModel) -> None:
        self.model = model
        self.requested: list[str | None] = []

    def get_model(self, model_name):
        self.requested.append(model_name)
        return self.model


def openai_runtime(model: ScriptedModel, **kw) -> tuple[OpenAIAgentRuntime, ScriptedProvider]:
    provider = ScriptedProvider(model)
    return OpenAIAgentRuntime(model_provider=provider, tracing_disabled=True, **kw), provider


def agent(**overrides) -> AgentConfig:
    base = dict(id="a", name="A", instructions="Be helpful.", runtime="openai_agents")
    base.update(overrides)
    return AgentConfig(**base)


# --- AgentConfig ---------------------------------------------------------------


def test_partial_config_gets_defaults():
    cfg = AgentConfig(id="x", name="X", instructions="hi")
    assert cfg.tools == [] and cfg.capabilities == []
    assert cfg.limits.max_steps == 10 and cfg.limits.timeout_seconds == 120
    assert cfg.runtime == "openai_agents" and cfg.enabled


def test_runtime_registry_unknown_name():
    with pytest.raises(RuntimeNotFoundError):
        RuntimeRegistry([FakeAgentRuntime()]).get("langgraph")


# --- OpenAI Agents SDK adapter ---------------------------------------------------


def test_openai_runtime_returns_final_output_and_usage():
    model = ScriptedModel([[_message("findings")]])
    rt, provider = openai_runtime(model, default_model="gpt-test")
    result = run(rt.execute(agent(), {"task": "Research X", "workflow_input": {"topic": "cars"}}))

    assert result.status == AgentResultStatus.COMPLETED
    assert result.output == "findings"
    assert result.usage.total_tokens == 15 and result.usage.requests == 1
    assert provider.requested == ["gpt-test"]
    assert model.seen[0]["instructions"] == "Be helpful."
    assert "Research X" in model.seen[0]["input"] and '"topic": "cars"' in model.seen[0]["input"]


def test_openai_runtime_agent_model_overrides_default():
    rt, provider = openai_runtime(ScriptedModel([[_message("ok")]]), default_model="gpt-default")
    run(rt.execute(agent(model="gpt-custom"), {"task": "t"}))
    assert provider.requested == ["gpt-custom"]


def test_openai_runtime_runs_registered_tool():
    calls: list[int] = []

    @function_tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        calls.append(a + b)
        return a + b

    model = ScriptedModel([[_tool_call("add", {"a": 2, "b": 3})], [_message("5")]])
    rt, _ = openai_runtime(model, tool_factories={"add": lambda: add})
    result = run(rt.execute(agent(tools=["add"]), {"task": "add"}))

    assert result.ok and result.output == "5"
    assert calls == [5]
    assert model.seen[0]["tools"] == ["add"]
    assert result.usage.requests == 2


def test_openai_runtime_unknown_tool_is_denied():
    model = ScriptedModel([[_message("never")]])
    rt, _ = openai_runtime(model, tool_factories={})
    result = run(rt.execute(agent(tools=["delete_everything"]), {"task": "t"}))
    assert result.status == AgentResultStatus.FAILED
    assert "delete_everything" in result.error
    assert model.seen == []


def test_openai_runtime_step_limit():
    @function_tool
    def noop() -> str:
        """Do nothing."""
        return "again"

    # The model keeps calling the tool forever.
    model = ScriptedModel([[_tool_call("noop", {})]])
    rt, _ = openai_runtime(model, tool_factories={"noop": lambda: noop})
    result = run(rt.execute(agent(tools=["noop"], limits=AgentLimits(max_steps=2)), {"task": "t"}))
    assert result.status == AgentResultStatus.STEP_LIMIT_EXCEEDED


def test_openai_runtime_timeout():
    model = ScriptedModel([[_message("late")]], delay=0.5)
    rt, _ = openai_runtime(model)
    result = run(rt.execute(agent(limits=AgentLimits(timeout_seconds=0.05)), {"task": "t"}))
    assert result.status == AgentResultStatus.TIMED_OUT


def test_openai_runtime_model_error_becomes_failed_result():
    class Boom(ScriptedModel):
        async def get_response(self, *a, **kw):
            raise ConnectionError("provider down")

    rt, _ = openai_runtime(Boom([[_message("x")]]))
    result = run(rt.execute(agent(), {"task": "t"}))
    assert result.status == AgentResultStatus.FAILED and "provider down" in result.error


# --- orchestrator ---------------------------------------------------------------


def make_orchestrator(responders=None, agents=None, runtime=None, events=None):
    agents = agents or [a.model_copy(update={"runtime": "fake"}) for a in SAMPLE_AGENTS]
    runtime = runtime or FakeAgentRuntime(responders)
    orch = AgentOrchestrator(
        InMemoryAgentRegistry(agents),
        RuntimeRegistry([runtime]),
        on_event=events.append if events is not None else None,
    )
    return orch, runtime


def test_sample_workflow_runs_sequentially_and_passes_outputs():
    events: list[WorkflowEvent] = []
    orch, rt = make_orchestrator(
        {
            "research-agent": lambda c, i: {"options": ["Civic", "Corolla"]},
            "finance-agent": lambda c, i: (
                f"plan for {i['previous_outputs']['research']['options']}"
            ),
            "reviewer-agent": lambda c, i: (
                "final: " + " | ".join(map(str, i["previous_outputs"].values()))
            ),
        },
        events=events,
    )
    result = run(orch.run_workflow(RESEARCH_FINANCE_REVIEW, {"budget_usd": 15000}))

    assert result.status == RunStatus.COMPLETED
    assert [c[0] for c in rt.calls] == ["research-agent", "finance-agent", "reviewer-agent"]
    # finance declares uses=["research"]; reviewer gets everything before it
    assert list(rt.calls[1][1]["previous_outputs"]) == ["research"]
    assert list(rt.calls[2][1]["previous_outputs"]) == ["research", "finance"]
    assert all(c[1]["workflow_input"] == {"budget_usd": 15000} for c in rt.calls)
    assert result.output.startswith("final: {'options'")
    assert all(t.status == RunStatus.COMPLETED for t in result.tasks)
    assert [e.type for e in events] == [
        "workflow.started",
        *["task.queued", "task.started", "task.completed"] * 3,
        "workflow.completed",
    ]
    assert {e.task_id for e in events if e.step_id} == {t.id for t in result.tasks}


def test_failed_step_stops_run_and_skips_rest():
    events: list[WorkflowEvent] = []

    def boom(c, i):
        raise ValueError("bad data")

    orch, rt = make_orchestrator({"finance-agent": boom}, events=events)
    result = run(orch.run_workflow(RESEARCH_FINANCE_REVIEW, {}))

    assert result.status == RunStatus.FAILED
    assert [t.status for t in result.tasks] == [
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.SKIPPED,
    ]
    assert "bad data" in result.error and "finance" in result.error
    assert [c[0] for c in rt.calls] == ["research-agent", "finance-agent"]
    assert [e.type for e in events[-3:]] == ["task.failed", "task.skipped", "workflow.failed"]


def test_disabled_missing_and_unknown_runtime_agents_fail_the_step():
    wf = WorkflowDefinition(name="w", steps=[WorkflowStep(id="s", agent_id="x", task="t")])
    cases = [
        ([], "not found"),
        ([agent(id="x", runtime="fake", enabled=False)], "disabled"),
        ([agent(id="x", runtime="langgraph")], "langgraph"),
    ]
    for agents, needle in cases:
        orch = AgentOrchestrator(
            InMemoryAgentRegistry(agents), RuntimeRegistry([FakeAgentRuntime()])
        )
        result = run(orch.run_workflow(wf, {}))
        assert result.status == RunStatus.FAILED
        assert needle in result.tasks[0].error


def test_execute_task_direct_run_raises_on_lookup_errors():
    orch, _ = make_orchestrator()
    with pytest.raises(AgentNotFoundError):
        run(orch.execute_task("nope", {}))
    orch2 = AgentOrchestrator(
        InMemoryAgentRegistry([agent(id="off", runtime="fake", enabled=False)]),
        RuntimeRegistry([FakeAgentRuntime()]),
    )
    with pytest.raises(AgentUnavailableError):
        run(orch2.execute_task("off", {}))


def test_runtimes_are_swappable_per_agent():
    """Two agents on two different runtimes in one workflow, no orchestrator change."""
    model = ScriptedModel([[_message("from sdk")]])
    sdk_rt, _ = openai_runtime(model)
    fake = FakeAgentRuntime({"b": lambda c, i: f"fake saw {i['previous_outputs']['one']}"})
    orch = AgentOrchestrator(
        InMemoryAgentRegistry([agent(id="a"), agent(id="b", runtime="fake")]),
        RuntimeRegistry([sdk_rt, fake]),
    )
    wf = WorkflowDefinition(
        name="mixed",
        steps=[
            WorkflowStep(id="one", agent_id="a", task="t1"),
            WorkflowStep(id="two", agent_id="b", task="t2"),
        ],
    )
    result = run(orch.run_workflow(wf, {}))
    assert result.status == RunStatus.COMPLETED
    assert result.output == "fake saw from sdk"
    assert (
        result.tasks[0].result.runtime == "openai_agents"
        and result.tasks[1].result.runtime == "fake"
    )
    assert result.usage.total_tokens == 15


def test_sample_workflow_end_to_end_through_openai_adapter():
    model = ScriptedModel(
        [[_message("research out")], [_message("finance out")], [_message("final answer")]]
    )
    rt, _ = openai_runtime(
        model,
        tool_factories={
            "web_search": lambda: function_tool(lambda q: q, name_override="web_search")
        },
    )
    orch = AgentOrchestrator(InMemoryAgentRegistry(SAMPLE_AGENTS), RuntimeRegistry([rt]))
    result = run(orch.run_workflow(RESEARCH_FINANCE_REVIEW, {"request": "used car"}))
    assert result.status == RunStatus.COMPLETED
    assert result.output == "final answer"
    assert result.usage.total_tokens == 45
    assert "finance out" in model.seen[2]["input"]


def test_broken_event_handler_does_not_break_run():
    def bad(event):
        raise RuntimeError("listener bug")

    agents = [a.model_copy(update={"runtime": "fake"}) for a in SAMPLE_AGENTS]
    orch = AgentOrchestrator(
        InMemoryAgentRegistry(agents), RuntimeRegistry([FakeAgentRuntime()]), on_event=bad
    )
    assert run(orch.run_workflow(RESEARCH_FINANCE_REVIEW, {})).status == RunStatus.COMPLETED


def test_workflow_definition_validation():
    with pytest.raises(ValueError):
        WorkflowDefinition(
            name="w", steps=[WorkflowStep(id="a", agent_id="x", task="t", uses=["b"])]
        )
    with pytest.raises(ValueError):
        WorkflowDefinition(
            name="w",
            steps=[
                WorkflowStep(id="a", agent_id="x", task="t"),
                WorkflowStep(id="a", agent_id="y", task="t"),
            ],
        )
