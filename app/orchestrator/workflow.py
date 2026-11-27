"""Workflow definitions and run state for the sequential orchestrator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.runtimes.base import AgentResult, TokenUsage


def _now() -> datetime:
    return datetime.now(UTC)


class WorkflowStep(BaseModel):
    """One step: which agent runs and what it is asked to do.

    ``uses`` lists earlier step ids whose outputs are passed in. Leave it
    unset to pass every earlier output (the default for a simple pipeline).
    """

    id: str
    agent_id: str
    task: str
    uses: list[str] | None = None


class WorkflowDefinition(BaseModel):
    name: str
    description: str = ""
    version: int = 1
    steps: list[WorkflowStep] = Field(min_length=1)
    #: Step whose output becomes the workflow output; defaults to the last step.
    output_step: str | None = None

    @model_validator(mode="after")
    def _check_steps(self) -> WorkflowDefinition:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"Duplicate step id '{step.id}'")
            for ref in step.uses or []:
                if ref not in seen:
                    raise ValueError(f"Step '{step.id}' uses '{ref}', which is not an earlier step")
            seen.add(step.id)
        if self.output_step is not None and self.output_step not in seen:
            raise ValueError(f"output_step '{self.output_step}' is not a step")
        return self


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    step_id: str
    agent_id: str
    status: RunStatus = RunStatus.PENDING
    input: dict[str, Any] = Field(default_factory=dict)
    result: AgentResult | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


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
            if task.result is not None:
                total = total + task.result.usage
        return total


class WorkflowEvent(BaseModel):
    """Progress event (plan section 8.2); ready to stream over SSE later."""

    #: workflow.started, task.started, task.completed, task.failed,
    #: workflow.completed, workflow.failed
    type: str
    run_id: str
    step_id: str | None = None
    agent_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=_now)
