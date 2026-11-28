"""Workflow definitions (a DAG of steps) and run state for the orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.runtimes.base import AgentResult, TokenUsage


def _now() -> datetime:
    return datetime.now(UTC)


StepKind = Literal["agent", "review", "aggregate"]


class WorkflowStep(BaseModel):
    """One step of a workflow.

    Ordering and data flow:

    - ``depends_on`` lists the steps that must finish before this one starts.
      Steps whose dependencies are all done run in parallel. ``[]`` makes a
      root step. Left unset, it defaults to ``uses`` when that is set, and
      otherwise to every earlier step (a plain sequential pipeline, which is
      how Phase 1 workflows behave).
    - ``uses`` lists the steps whose outputs are passed in as
      ``previous_outputs``. Left unset, it is the step's ``depends_on``. Every
      step in ``uses`` must be an ancestor of this step.

    Kinds:

    - ``agent`` (default): the agent named by ``agent_id`` runs ``task``.
    - ``review``: the agent checks the outputs in ``uses`` and replies with a
      verdict ``{"approved": bool, "feedback": str, ...}``. When it rejects and
      ``max_revisions`` allows, the reviewed steps run again with the feedback
      and the review repeats. ``require_approval`` fails the step (and the
      run) if the last verdict is still a rejection.
    - ``aggregate``: no agent. The output is ``{step_id: output}`` for the
      steps in ``uses`` (fan-in into one result).
    """

    id: str
    kind: StepKind = "agent"
    agent_id: str | None = None
    task: str = ""
    depends_on: list[str] | None = None
    uses: list[str] | None = None
    max_revisions: int = Field(default=0, ge=0, le=5)
    require_approval: bool = False

    @model_validator(mode="after")
    def _check_kind(self) -> WorkflowStep:
        if self.kind == "aggregate":
            if self.agent_id is not None:
                raise ValueError(f"Aggregate step '{self.id}' does not take an agent_id")
        else:
            if not self.agent_id:
                raise ValueError(f"Step '{self.id}' needs an agent_id")
            if not self.task:
                raise ValueError(f"Step '{self.id}' needs a task")
        if self.kind != "review" and (self.max_revisions or self.require_approval):
            raise ValueError(
                f"Step '{self.id}': max_revisions and require_approval are for review steps"
            )
        return self


class WorkflowDefinition(BaseModel):
    name: str
    description: str = ""
    version: int = 1
    steps: list[WorkflowStep] = Field(min_length=1)
    #: Step whose output becomes the workflow output; defaults to the last step.
    output_step: str | None = None
    #: Most steps running at once in one run (None: the orchestrator's default).
    max_parallel: int | None = Field(default=None, ge=1, le=32)
    #: Wall-clock limit for the whole run (None: no limit beyond agent timeouts).
    timeout_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_graph(self) -> WorkflowDefinition:
        ids: set[str] = set()
        for step in self.steps:
            if step.id in ids:
                raise ValueError(f"Duplicate step id '{step.id}'")
            ids.add(step.id)
        for step in self.steps:
            for ref in [*(step.depends_on or []), *(step.uses or [])]:
                if ref not in ids:
                    raise ValueError(f"Step '{step.id}' refers to '{ref}', which is not a step")
                if ref == step.id:
                    raise ValueError(f"Step '{step.id}' cannot depend on itself")
        self.topological_order()  # raises on cycles

        ancestors = self.ancestors()
        for step in self.steps:
            missing = [ref for ref in self.inputs_of(step.id) if ref not in ancestors[step.id]]
            if missing:
                raise ValueError(
                    f"Step '{step.id}' uses {missing}, which it does not depend on; "
                    "add them to depends_on"
                )
            if step.kind == "review" and not self.inputs_of(step.id):
                raise ValueError(f"Review step '{step.id}' has nothing to review")

        # Revised steps produce new outputs, so nothing may read them before
        # the review that requested the revision has finished.
        for review in self.steps:
            if review.kind != "review" or not review.max_revisions:
                continue
            for target in self.inputs_of(review.id):
                if self.step(target).kind != "agent":
                    raise ValueError(
                        f"Review '{review.id}' can only send agent steps back for revision; "
                        f"'{target}' is a {self.step(target).kind} step"
                    )
                for other in self.steps:
                    if other.id == review.id or review.id in ancestors[other.id]:
                        continue
                    if target in self.dependencies_of(other.id) or target in self.inputs_of(
                        other.id
                    ):
                        raise ValueError(
                            f"Step '{other.id}' reads '{target}', which review '{review.id}' "
                            "may send back for revision; make it depend on the review"
                        )

        if self.output_step is not None and self.output_step not in ids:
            raise ValueError(f"output_step '{self.output_step}' is not a step")
        return self

    # --- graph helpers -------------------------------------------------------

    def step(self, step_id: str) -> WorkflowStep:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    def dependencies_of(self, step_id: str) -> list[str]:
        step = self.step(step_id)
        if step.depends_on is not None:
            return list(step.depends_on)
        if step.uses is not None:
            return list(step.uses)
        index = next(i for i, s in enumerate(self.steps) if s.id == step_id)
        return [s.id for s in self.steps[:index]]

    def inputs_of(self, step_id: str) -> list[str]:
        step = self.step(step_id)
        return list(step.uses) if step.uses is not None else self.dependencies_of(step_id)

    def topological_order(self) -> list[str]:
        """Step ids in an order where dependencies come first (stable by list order)."""
        remaining = {s.id: set(self.dependencies_of(s.id)) for s in self.steps}
        order: list[str] = []
        while remaining:
            ready = [s.id for s in self.steps if s.id in remaining and not remaining[s.id]]
            if not ready:
                raise ValueError(f"Steps form a dependency cycle: {sorted(remaining)}")
            for step_id in ready:
                order.append(step_id)
                del remaining[step_id]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order

    def ancestors(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for step_id in self.topological_order():
            found: set[str] = set()
            for dep in self.dependencies_of(step_id):
                found.add(dep)
                found |= result[dep]
            result[step_id] = found
        return result


class RunStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"  # dependencies done, waiting for a parallel slot
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskAttempt(BaseModel):
    """One agent call for a task. A step sent back by a reviewer has several."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input: dict[str, Any] = Field(default_factory=dict)
    result: AgentResult | None = None
    error: str | None = None
    cancelled: bool = False
    started_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class TaskRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    step_id: str
    kind: StepKind = "agent"
    agent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.PENDING
    input: dict[str, Any] = Field(default_factory=dict)
    #: Result of the latest attempt.
    result: AgentResult | None = None
    output: Any = None
    error: str | None = None
    attempts: list[TaskAttempt] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for attempt in self.attempts:
            if attempt.result is not None:
                total = total + attempt.result.usage
        return total


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow: str
    status: RunStatus = RunStatus.PENDING
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: str | None = None
    tasks: list[TaskRun] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for task in self.tasks:
            total = total + task.usage
        return total

    def task(self, step_id: str) -> TaskRun:
        for task in self.tasks:
            if task.step_id == step_id:
                return task
        raise KeyError(step_id)

    @property
    def finished(self) -> bool:
        return self.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


class WorkflowEvent(BaseModel):
    """Progress event (plan section 8.2), streamed to clients over SSE.

    Types: workflow.started, task.queued, task.started, task.completed,
    task.failed, task.skipped, task.cancelled, review.completed,
    task.revision_requested, workflow.completed, workflow.failed,
    workflow.cancelled.
    """

    type: str
    run_id: str
    step_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=_now)
