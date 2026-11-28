"""Phase 2 orchestrator: DAG dependencies, parallel steps, review loops, aggregation."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.samples import RESEARCH_AND_REPORT, SAMPLE_AGENTS
from app.orchestrator import (
    AgentOrchestrator,
    InMemoryAgentRegistry,
    RunStatus,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowStep,
)
from app.orchestrator.review import parse_verdict
from app.runtimes import AgentConfig, AgentLimits, RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime


def agents(*ids: str, timeout: float = 5) -> list[AgentConfig]:
    return [
        AgentConfig(
            id=i,
            name=i.upper(),
            instructions="x",
            runtime="fake",
            limits=AgentLimits(timeout_seconds=timeout),
        )
        for i in ids
    ]


def orchestrator(responders, ids, events=None, **kw):
    runtime = FakeAgentRuntime(responders)
    orch = AgentOrchestrator(
        InMemoryAgentRegistry(agents(*ids)),
        RuntimeRegistry([runtime]),
        on_event=events.append if events is not None else None,
        **kw,
    )
    return orch, runtime


class Concurrency:
    """Responder factory that records how many calls overlap."""

    def __init__(self, delay: float = 0.1) -> None:
        self.delay = delay
        self.now = 0
        self.peak = 0
        self.order: list[str] = []

    def __call__(self, name: str, output=None):
        async def respond(config, task_input):
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.order.append(f"start:{name}")
            await asyncio.sleep(self.delay)
            self.now -= 1
            self.order.append(f"end:{name}")
            return output if output is not None else f"{name} out"

        return respond


def fan_out(**step_kw) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="fan",
        steps=[
            WorkflowStep(id="a", agent_id="a", task="t", depends_on=[]),
            WorkflowStep(id="b", agent_id="b", task="t", depends_on=[]),
            WorkflowStep(id="c", agent_id="c", task="t", depends_on=[]),
            WorkflowStep(id="join", agent_id="j", task="t", depends_on=["a", "b", "c"]),
        ],
        **step_kw,
    )


# --- parallel execution and dependencies -----------------------------------------


async def test_independent_steps_run_in_parallel():
    c = Concurrency(delay=0.2)
    orch, rt = orchestrator({k: c(k) for k in "abc"} | {"j": c("j")}, "abcj")
    started = time.monotonic()
    result = await orch.run_workflow(fan_out(), {})
    elapsed = time.monotonic() - started

    assert result.status == RunStatus.COMPLETED
    assert c.peak == 3
    assert elapsed < 0.6  # 3 parallel + 1 joined step, not 4 in a row
    assert c.order[-2:] == ["start:j", "end:j"]
    assert list(rt.calls[-1][1]["previous_outputs"]) == ["a", "b", "c"]


async def test_max_parallel_limits_concurrency():
    c = Concurrency(delay=0.02)
    orch, _ = orchestrator({k: c(k) for k in "abcj"}, "abcj")
    result = await orch.run_workflow(fan_out(max_parallel=1), {})
    assert result.status == RunStatus.COMPLETED and c.peak == 1

    c2 = Concurrency(delay=0.02)
    orch2, _ = orchestrator({k: c2(k) for k in "abcj"}, "abcj", max_parallel=2)
    assert (await orch2.run_workflow(fan_out(), {})).status == RunStatus.COMPLETED
    assert c2.peak == 2


async def test_diamond_passes_only_used_outputs():
    wf = WorkflowDefinition(
        name="diamond",
        steps=[
            WorkflowStep(id="root", agent_id="a", task="t"),
            WorkflowStep(id="left", agent_id="b", task="t", depends_on=["root"]),
            WorkflowStep(id="right", agent_id="c", task="t", depends_on=["root"]),
            WorkflowStep(
                id="end",
                agent_id="j",
                task="t",
                depends_on=["left", "right"],
                uses=["root", "left"],
            ),
        ],
    )
    orch, rt = orchestrator({"j": lambda c, i: sorted(i["previous_outputs"])}, "abcj")
    result = await orch.run_workflow(wf, {})
    assert result.output == ["left", "root"]
    assert [t.depends_on for t in result.tasks] == [[], ["root"], ["root"], ["left", "right"]]


async def test_failure_cancels_running_siblings_and_skips_dependents():
    events: list[WorkflowEvent] = []

    async def slow(c, i):
        await asyncio.sleep(5)

    def boom(c, i):
        raise ValueError("no data")

    orch, _ = orchestrator({"a": boom, "b": slow, "c": slow}, "abcj", events=events)
    started = time.monotonic()
    result = await orch.run_workflow(fan_out(), {})

    assert time.monotonic() - started < 1
    assert result.status == RunStatus.FAILED and "'a' failed: ValueError: no data" in result.error
    statuses = {t.step_id: t.status for t in result.tasks}
    assert statuses == {
        "a": RunStatus.FAILED,
        "b": RunStatus.CANCELLED,
        "c": RunStatus.CANCELLED,
        "join": RunStatus.SKIPPED,
    }
    types = [e.type for e in events]
    assert types.count("task.cancelled") == 2 and types[-1] == "workflow.failed"


async def test_workflow_timeout_fails_the_run():
    async def slow(c, i):
        await asyncio.sleep(5)

    orch, _ = orchestrator({"a": slow}, "abcj")
    wf = fan_out(timeout_seconds=0.1)
    result = await orch.run_workflow(wf, {})
    assert result.status == RunStatus.FAILED and "timed out after 0.1s" in result.error
    assert result.task("a").status == RunStatus.CANCELLED
    assert result.task("join").status == RunStatus.SKIPPED


async def test_cancelling_the_call_cancels_the_run():
    events: list[WorkflowEvent] = []

    async def slow(c, i):
        await asyncio.sleep(5)

    orch, _ = orchestrator({"a": slow, "b": slow, "c": slow}, "abcj", events=events)
    run = orch.new_run(fan_out(), {})
    job = asyncio.create_task(orch.run_workflow(fan_out(), {}, run=run))
    await asyncio.sleep(0.05)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert run.status == RunStatus.CANCELLED
    assert [t.status for t in run.tasks] == [RunStatus.CANCELLED] * 3 + [RunStatus.SKIPPED]
    assert events[-1].type == "workflow.cancelled"


# --- aggregate and review steps ---------------------------------------------------


async def test_aggregate_step_merges_outputs_without_an_agent():
    wf = WorkflowDefinition(
        name="agg",
        steps=[
            WorkflowStep(id="a", agent_id="a", task="t", depends_on=[]),
            WorkflowStep(id="b", agent_id="b", task="t", depends_on=[]),
            WorkflowStep(id="all", kind="aggregate", depends_on=["a", "b"]),
        ],
    )
    orch, rt = orchestrator({"a": lambda c, i: 1, "b": lambda c, i: {"x": 2}}, "ab")
    result = await orch.run_workflow(wf, {})
    assert result.output == {"a": 1, "b": {"x": 2}}
    assert len(rt.calls) == 2 and result.task("all").attempts == []


def review_wf(**review_kw) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="rev",
        steps=[
            WorkflowStep(id="draft", agent_id="a", task="write"),
            WorkflowStep(id="facts", agent_id="b", task="check", depends_on=[]),
            WorkflowStep(id="review", kind="review", agent_id="r", task="review", **review_kw),
        ],
    )


async def test_review_rejection_sends_steps_back_with_feedback():
    events: list[WorkflowEvent] = []
    verdicts = iter(
        [
            '```json\n{"approved": false, "feedback": "add sources"}\n```',
            {"approved": True, "feedback": "", "summary": "good"},
        ]
    )

    def draft(c, i):
        rev = i.get("revision")
        return "v1" if rev is None else f"v2 ({rev['feedback']}, was {rev['previous_output']})"

    orch, rt = orchestrator({"a": draft, "r": lambda c, i: next(verdicts)}, "abr", events=events)
    result = await orch.run_workflow(review_wf(max_revisions=2), {})

    assert result.status == RunStatus.COMPLETED
    assert result.output == {"approved": True, "feedback": "", "summary": "good", "revisions": 1}
    assert result.task("draft").output == "v2 (add sources, was v1)"
    assert [len(result.task(s).attempts) for s in ("draft", "facts", "review")] == [2, 2, 2]
    # the second review saw the revised outputs, and the review format was asked for
    assert rt.calls[-1][1]["previous_outputs"]["draft"].startswith("v2")
    assert '"approved"' in rt.calls[-1][1]["task"]
    types = [e.type for e in events]
    assert types.count("review.completed") == 2 and types.count("task.revision_requested") == 2
    first_review = next(e for e in events if e.type == "review.completed")
    assert first_review.data == {"approved": False, "feedback": "add sources", "round": 1}


async def test_review_stops_after_max_revisions():
    reject = {"approved": False, "feedback": "still wrong"}
    orch, rt = orchestrator({"r": lambda c, i: reject}, "abr")
    result = await orch.run_workflow(review_wf(max_revisions=1), {})
    assert result.status == RunStatus.COMPLETED
    assert result.output == {**reject, "revisions": 1}
    assert len(result.task("review").attempts) == 2

    orch, _ = orchestrator({"r": lambda c, i: reject}, "abr")
    strict = await orch.run_workflow(review_wf(max_revisions=1, require_approval=True), {})
    assert strict.status == RunStatus.FAILED and "did not approve: still wrong" in strict.error
    assert strict.task("review").status == RunStatus.FAILED


async def test_failed_revision_fails_the_review():
    calls = {"n": 0}

    def flaky(c, i):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("model down")
        return "v1"

    orch, _ = orchestrator(
        {"a": flaky, "r": lambda c, i: {"approved": False, "feedback": "redo"}}, "abr"
    )
    result = await orch.run_workflow(review_wf(max_revisions=1), {})
    assert result.status == RunStatus.FAILED and "Revision failed" in result.error
    assert result.task("draft").status == RunStatus.FAILED
    assert result.task("review").status == RunStatus.FAILED


async def test_free_form_review_passes_through():
    orch, _ = orchestrator({"r": lambda c, i: "Looks fine to me."}, "abr")
    result = await orch.run_workflow(review_wf(max_revisions=2), {})
    assert result.status == RunStatus.COMPLETED and result.output == "Looks fine to me."


def test_parse_verdict():
    assert parse_verdict({"approved": True}) == {"approved": True}
    assert parse_verdict('{"approved": false, "feedback": "x"}')["feedback"] == "x"
    assert parse_verdict('Notes first.\n{"approved": true}')["approved"] is True
    assert parse_verdict('```json\n{"approved": true}\n```') == {"approved": True}
    assert parse_verdict({"approved": "yes"}) is None
    assert parse_verdict("no json here") is None
    assert parse_verdict(["approved"]) is None


async def test_sample_research_and_report_workflow():
    events: list[WorkflowEvent] = []
    fake_agents = [a.model_copy(update={"runtime": "fake"}) for a in SAMPLE_AGENTS]
    verdicts = iter(
        [{"approved": False, "feedback": "cite sources"}, {"approved": True, "summary": "ok"}]
    )
    runtime = FakeAgentRuntime({"reviewer-agent": lambda c, i: next(verdicts)}, delay_seconds=0.05)
    orch = AgentOrchestrator(
        InMemoryAgentRegistry(fake_agents), RuntimeRegistry([runtime]), on_event=events.append
    )
    result = await orch.run_workflow(RESEARCH_AND_REPORT, {"topic": "AI Agent Hub"})

    assert result.status == RunStatus.COMPLETED
    assert set(result.output) == {"plan", "research", "competitors", "architecture", "review"}
    assert result.output["review"]["revisions"] == 1
    starts = [e.step_id for e in events if e.type == "task.started"]
    assert starts[0] == "plan"
    assert set(starts[1:4]) == {"research", "competitors", "architecture"}
    # the three specialists overlapped: all started before the first finished
    first_done = next(
        i for i, e in enumerate(events) if e.type == "task.completed" and e.step_id != "plan"
    )
    assert sum(1 for e in events[:first_done] if e.type == "task.started") == 4


# --- validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "steps, needle",
    [
        (
            [
                {"id": "a", "agent_id": "x", "task": "t", "depends_on": ["b"]},
                {"id": "b", "agent_id": "x", "task": "t", "depends_on": ["a"]},
            ],
            "cycle",
        ),
        ([{"id": "a", "agent_id": "x", "task": "t", "depends_on": ["a"]}], "itself"),
        (
            [
                {"id": "a", "agent_id": "x", "task": "t"},
                {"id": "b", "agent_id": "x", "task": "t", "depends_on": [], "uses": ["a"]},
            ],
            "does not depend on",
        ),
        ([{"id": "a", "kind": "aggregate", "agent_id": "x"}], "does not take an agent_id"),
        ([{"id": "a", "task": "t"}], "needs an agent_id"),
        ([{"id": "a", "kind": "review", "agent_id": "x", "task": "t"}], "nothing to review"),
        ([{"id": "a", "agent_id": "x", "task": "t", "max_revisions": 1}], "review steps"),
        (
            [
                {"id": "a", "agent_id": "x", "task": "t"},
                {"id": "r", "kind": "review", "agent_id": "x", "task": "t", "max_revisions": 1},
                {"id": "side", "agent_id": "x", "task": "t", "depends_on": ["a"]},
            ],
            "make it depend on the review",
        ),
        (
            [
                {"id": "a", "agent_id": "x", "task": "t"},
                {"id": "g", "kind": "aggregate"},
                {
                    "id": "r",
                    "kind": "review",
                    "agent_id": "x",
                    "task": "t",
                    "uses": ["g"],
                    "max_revisions": 1,
                },
            ],
            "only send agent steps back",
        ),
    ],
)
def test_invalid_graphs_are_rejected(steps, needle):
    with pytest.raises(ValueError, match=needle):
        WorkflowDefinition(name="w", steps=steps)


def test_default_dependencies():
    wf = WorkflowDefinition(
        name="w",
        steps=[
            WorkflowStep(id="a", agent_id="x", task="t"),
            WorkflowStep(id="b", agent_id="x", task="t"),
            WorkflowStep(id="c", agent_id="x", task="t", uses=["a"]),
        ],
    )
    assert [wf.dependencies_of(s) for s in "abc"] == [[], ["a"], ["a"]]
    assert wf.topological_order() == ["a", "b", "c"]
