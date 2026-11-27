"""Sequential workflow orchestrator (Phase 1 of the plan).

The orchestrator never imports an agent framework. It looks agents up in the
registry, picks the runtime named by each agent's ``runtime`` field and
passes the task through the :class:`~app.runtimes.base.AgentRuntime`
interface. Agents never call each other: the orchestrator hands each step
the outputs of earlier steps.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.orchestrator.registry import AgentLookup
from app.orchestrator.workflow import (
    RunStatus,
    TaskRun,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowRun,
)
from app.runtimes.base import (
    AgentConfig,
    AgentResult,
    AgentResultStatus,
    RuntimeNotFoundError,
    RuntimeRegistry,
)

logger = logging.getLogger(__name__)

EventHandler = Callable[[WorkflowEvent], None | Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


class AgentUnavailableError(RuntimeError):
    pass


class AgentOrchestrator:
    def __init__(
        self,
        registry: AgentLookup,
        runtimes: RuntimeRegistry,
        *,
        on_event: EventHandler | None = None,
    ) -> None:
        self.registry = registry
        self.runtimes = runtimes
        self.on_event = on_event

    async def _emit(self, event: WorkflowEvent) -> None:
        logger.info("%s run=%s step=%s", event.type, event.run_id, event.step_id)
        if self.on_event is None:
            return
        try:
            maybe = self.on_event(event)
            if inspect.isawaitable(maybe):
                await maybe
        except Exception:  # a broken listener must not break the run
            logger.exception("Workflow event handler failed for %s", event.type)

    async def _resolve(self, agent_id: str) -> AgentConfig:
        agent = await self.registry.get_agent(agent_id)
        if not agent.enabled:
            raise AgentUnavailableError(f"Agent '{agent_id}' is disabled")
        return agent

    async def execute_task(self, agent_id: str, task_input: dict[str, Any]) -> AgentResult:
        """Run one agent directly (backs ``POST /api/v1/agents/{id}/run``).

        Lookup and runtime errors are raised; agent failures come back as a
        non-completed :class:`AgentResult`.
        """
        agent = await self._resolve(agent_id)
        runtime = self.runtimes.get(agent.runtime)
        return await runtime.execute(agent_config=agent, task_input=task_input)

    async def run_workflow(
        self, workflow: WorkflowDefinition, workflow_input: dict[str, Any]
    ) -> WorkflowRun:
        """Run steps in order. The first failed step fails the run and the
        remaining steps are marked skipped."""
        run = WorkflowRun(
            workflow=workflow.name,
            input=workflow_input,
            status=RunStatus.RUNNING,
            started_at=_now(),
            tasks=[TaskRun(step_id=s.id, agent_id=s.agent_id) for s in workflow.steps],
        )
        await self._emit(
            WorkflowEvent(type="workflow.started", run_id=run.id, data={"workflow": workflow.name})
        )

        outputs: dict[str, Any] = {}
        for step, task in zip(workflow.steps, run.tasks, strict=True):
            refs = list(outputs) if step.uses is None else step.uses
            task.input = {
                "task": step.task,
                "workflow_input": workflow_input,
                "previous_outputs": {ref: outputs[ref] for ref in refs},
            }
            task.status = RunStatus.RUNNING
            task.started_at = _now()
            await self._emit(
                WorkflowEvent(
                    type="task.started", run_id=run.id, step_id=step.id, agent_id=step.agent_id
                )
            )

            try:
                result = await self.execute_task(step.agent_id, task.input)
            except (LookupError, AgentUnavailableError, RuntimeNotFoundError) as exc:
                result = None
                task.error = str(exc)
            except Exception as exc:  # adapter bug; record it instead of crashing the run
                logger.exception("Runtime raised while running step %s", step.id)
                result = None
                task.error = f"{type(exc).__name__}: {exc}"
            else:
                task.result = result
                if not result.ok:
                    task.error = result.error or result.status.value

            task.completed_at = _now()
            if result is not None and result.ok:
                task.status = RunStatus.COMPLETED
                outputs[step.id] = result.output
                await self._emit(
                    WorkflowEvent(
                        type="task.completed",
                        run_id=run.id,
                        step_id=step.id,
                        agent_id=step.agent_id,
                        data={
                            "duration_ms": result.duration_ms,
                            "usage": result.usage.model_dump(),
                        },
                    )
                )
                continue

            task.status = RunStatus.FAILED
            await self._emit(
                WorkflowEvent(
                    type="task.failed",
                    run_id=run.id,
                    step_id=step.id,
                    agent_id=step.agent_id,
                    data={
                        "error": task.error,
                        "status": result.status.value if result else AgentResultStatus.FAILED.value,
                    },
                )
            )
            for later in run.tasks:
                if later.status == RunStatus.PENDING:
                    later.status = RunStatus.SKIPPED
            run.status = RunStatus.FAILED
            run.error = f"Step '{step.id}' failed: {task.error}"
            run.completed_at = _now()
            await self._emit(
                WorkflowEvent(type="workflow.failed", run_id=run.id, data={"error": run.error})
            )
            return run

        run.output = outputs[workflow.output_step or workflow.steps[-1].id]
        run.status = RunStatus.COMPLETED
        run.completed_at = _now()
        await self._emit(
            WorkflowEvent(
                type="workflow.completed", run_id=run.id, data={"usage": run.usage.model_dump()}
            )
        )
        return run
