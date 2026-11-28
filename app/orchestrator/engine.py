"""Workflow orchestrator: runs a DAG of steps, in parallel where it can.

The orchestrator never imports an agent framework. It looks agents up in the
registry, picks the runtime named by each agent's ``runtime`` field and
passes the task through the :class:`~app.runtimes.base.AgentRuntime`
interface. Agents never call each other: the orchestrator hands each step
the outputs of the steps it uses, and carries a reviewer's feedback back to
the steps it reviewed.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.orchestrator.registry import AgentLookup
from app.orchestrator.review import REVIEW_FORMAT, parse_verdict
from app.orchestrator.workflow import (
    RunStatus,
    TaskAttempt,
    TaskRun,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStep,
)
from app.policy.engine import PolicyDenied, PolicyEngine
from app.runtimes.base import (
    AgentConfig,
    AgentResult,
    AgentResultStatus,
    RuntimeNotFoundError,
    RuntimeRegistry,
)

logger = logging.getLogger(__name__)

EventHandler = Callable[[WorkflowEvent], None | Awaitable[None]]

DEFAULT_MAX_PARALLEL = 4


def _now() -> datetime:
    return datetime.now(UTC)


class AgentUnavailableError(RuntimeError):
    pass


class _StepFailed(Exception):
    """Internal: a step ended in failure; the message is the run error."""


class AgentOrchestrator:
    def __init__(
        self,
        registry: AgentLookup,
        runtimes: RuntimeRegistry,
        *,
        on_event: EventHandler | None = None,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.registry = registry
        self.runtimes = runtimes
        self.policy = policy
        self.on_event = on_event
        self.max_parallel = max_parallel
        # Registries may share one DB session, which allows no concurrent use.
        self._lookup_lock = asyncio.Lock()

    # --- events --------------------------------------------------------------

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

    async def _task_event(self, type_: str, run: WorkflowRun, task: TaskRun, **data: Any) -> None:
        await self._emit(
            WorkflowEvent(
                type=type_,
                run_id=run.id,
                step_id=task.step_id,
                task_id=task.id,
                agent_id=task.agent_id,
                data=data,
            )
        )

    # --- single agent ----------------------------------------------------------

    async def _resolve(self, agent_id: str) -> AgentConfig:
        async with self._lookup_lock:
            agent = await self.registry.get_agent(agent_id)
        if not agent.enabled:
            raise AgentUnavailableError(f"Agent '{agent_id}' is disabled")
        return agent

    async def execute_task(
        self, agent_id: str, task_input: dict[str, Any], *, workflow: str | None = None
    ) -> AgentResult:
        """Run one agent (directly for ``POST /api/v1/agents/{id}/run``, or as
        a step of ``workflow``), after the policy engine has checked the call.

        Lookup, runtime and policy errors (:class:`PolicyDenied`) are raised;
        agent failures come back as a non-completed :class:`AgentResult`.
        """
        agent = await self._resolve(agent_id)
        runtime = self.runtimes.get(agent.runtime)
        decision = None
        if self.policy is not None:
            decision = await self.policy.check(agent, workflow=workflow)
            agent = decision.agent
            for warning in decision.warnings:
                logger.warning("%s (agent %s)", warning, agent.id)
        result = await runtime.execute(agent_config=agent, task_input=task_input)
        if result.model is None:
            result.model = agent.model
        policy_meta = decision.metadata() if decision else None
        if policy_meta:
            result.metadata = {**result.metadata, "policy": policy_meta}
        return result

    # --- workflows -------------------------------------------------------------

    @staticmethod
    def new_run(
        workflow: WorkflowDefinition,
        workflow_input: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> WorkflowRun:
        """Build the pending run (with task ids) so a caller can store it
        before :meth:`run_workflow` starts."""
        tasks = [
            TaskRun(
                step_id=s.id,
                kind=s.kind,
                agent_id=s.agent_id,
                depends_on=workflow.dependencies_of(s.id),
            )
            for s in workflow.steps
        ]
        run = WorkflowRun(workflow=workflow.name, input=workflow_input, tasks=tasks)
        if run_id is not None:
            run.id = run_id
        return run

    async def run_workflow(
        self,
        workflow: WorkflowDefinition,
        workflow_input: dict[str, Any],
        *,
        run: WorkflowRun | None = None,
    ) -> WorkflowRun:
        """Run the workflow's steps as a DAG.

        A step starts once every step it depends on has completed; independent
        steps run at the same time, up to ``max_parallel``. The first failed
        step fails the run: steps still running are cancelled and steps not
        started yet are skipped. Cancelling the call cancels the run.
        """
        run = run or self.new_run(workflow, workflow_input)
        run.status = RunStatus.RUNNING
        run.started_at = _now()
        await self._emit(
            WorkflowEvent(
                type="workflow.started",
                run_id=run.id,
                data={"workflow": workflow.name, "steps": [t.step_id for t in run.tasks]},
            )
        )

        outputs: dict[str, Any] = {}
        sem = asyncio.Semaphore(workflow.max_parallel or self.max_parallel)
        try:
            async with asyncio.timeout(workflow.timeout_seconds):
                error = await self._schedule(workflow, run, outputs, sem)
        except TimeoutError:
            error = f"Workflow timed out after {workflow.timeout_seconds}s"
        except asyncio.CancelledError:
            await self._finish_unstarted(run)
            run.status = RunStatus.CANCELLED
            run.error = "Run was cancelled"
            run.completed_at = _now()
            await self._emit(WorkflowEvent(type="workflow.cancelled", run_id=run.id))
            raise

        await self._finish_unstarted(run)
        run.completed_at = _now()
        if error is not None:
            run.status = RunStatus.FAILED
            run.error = error
            await self._emit(
                WorkflowEvent(type="workflow.failed", run_id=run.id, data={"error": run.error})
            )
            return run

        run.output = outputs[workflow.output_step or workflow.steps[-1].id]
        run.status = RunStatus.COMPLETED
        await self._emit(
            WorkflowEvent(
                type="workflow.completed", run_id=run.id, data={"usage": run.usage.model_dump()}
            )
        )
        return run

    async def _schedule(
        self,
        workflow: WorkflowDefinition,
        run: WorkflowRun,
        outputs: dict[str, Any],
        sem: asyncio.Semaphore,
    ) -> str | None:
        """Start steps as their dependencies complete. Returns the run error, if any."""
        order = workflow.topological_order()
        waiting = list(order)
        running: dict[asyncio.Task[None], str] = {}
        error: str | None = None
        try:
            while True:
                if error is None:
                    for step_id in list(waiting):
                        deps = workflow.dependencies_of(step_id)
                        if all(run.task(d).status == RunStatus.COMPLETED for d in deps):
                            waiting.remove(step_id)
                            task = run.task(step_id)
                            task.status = RunStatus.QUEUED
                            await self._task_event("task.queued", run, task)
                            coro = self._run_step(
                                workflow, run, workflow.step(step_id), outputs, sem
                            )
                            running[asyncio.create_task(coro)] = step_id
                if not running:
                    return error
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    step_id = running.pop(finished)
                    if finished.cancelled():
                        continue
                    exc = finished.exception()
                    if exc is None or error is not None:
                        continue
                    if isinstance(exc, _StepFailed):
                        error = str(exc)
                    else:  # orchestrator bug; keep the run's record consistent
                        logger.error("Step %s crashed", step_id, exc_info=exc)
                        error = f"Step '{step_id}' crashed: {type(exc).__name__}: {exc}"
                        task = run.task(step_id)
                        task.status, task.error = RunStatus.FAILED, error
                    await self._cancel(running)
                    running.clear()
        finally:
            await self._cancel(running)

    @staticmethod
    async def _cancel(running: dict[asyncio.Task[None], str]) -> None:
        for pending in running:
            pending.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def _finish_unstarted(self, run: WorkflowRun) -> None:
        for task in run.tasks:
            if task.status in (RunStatus.PENDING, RunStatus.QUEUED):
                task.status = RunStatus.SKIPPED
                await self._task_event("task.skipped", run, task)

    # --- steps -----------------------------------------------------------------

    def _step_input(
        self,
        workflow: WorkflowDefinition,
        step: WorkflowStep,
        run: WorkflowRun,
        outputs: dict[str, Any],
        *,
        revision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = step.task if step.kind != "review" else f"{step.task}\n\n{REVIEW_FORMAT}"
        task_input: dict[str, Any] = {
            "task": task,
            "workflow_input": run.input,
            "previous_outputs": {ref: outputs[ref] for ref in workflow.inputs_of(step.id)},
        }
        if revision is not None:
            task_input["revision"] = revision
        return task_input

    async def _run_step(
        self,
        workflow: WorkflowDefinition,
        run: WorkflowRun,
        step: WorkflowStep,
        outputs: dict[str, Any],
        sem: asyncio.Semaphore,
    ) -> None:
        task = run.task(step.id)
        if step.kind == "aggregate":
            task.started_at = _now()
            task.input = {"uses": workflow.inputs_of(step.id)}
            self._complete(task, {ref: outputs[ref] for ref in workflow.inputs_of(step.id)})
            outputs[step.id] = task.output
            await self._task_event("task.completed", run, task, duration_ms=0)
            return

        attempt = await self._attempt(
            run, task, step, self._step_input(workflow, step, run, outputs), sem
        )
        if step.kind == "agent":
            await self._settle(run, task, attempt, outputs)
            return
        await self._review(workflow, run, step, task, attempt, outputs, sem)

    async def _attempt(
        self,
        run: WorkflowRun,
        task: TaskRun,
        step: WorkflowStep,
        task_input: dict[str, Any],
        sem: asyncio.Semaphore,
    ) -> TaskAttempt:
        """One agent call. Agent failures are recorded on the attempt, not raised."""
        assert step.agent_id is not None
        async with sem:
            attempt = TaskAttempt(input=task_input)
            task.attempts.append(attempt)
            task.input = task_input
            task.status = RunStatus.RUNNING
            task.started_at = task.started_at or attempt.started_at
            task.completed_at = None
            await self._task_event(
                "task.started", run, task, attempt=len(task.attempts), attempt_id=attempt.id
            )
            try:
                result = await self.execute_task(step.agent_id, task_input, workflow=run.workflow)
            except (
                LookupError,
                AgentUnavailableError,
                RuntimeNotFoundError,
                PolicyDenied,
            ) as exc:
                attempt.error = str(exc)
            except asyncio.CancelledError:
                attempt.cancelled = True
                attempt.error = "Cancelled"
                attempt.completed_at = task.completed_at = _now()
                task.status, task.error = RunStatus.CANCELLED, attempt.error
                await self._task_event("task.cancelled", run, task)
                raise
            except Exception as exc:  # adapter bug; record it instead of crashing the run
                logger.exception("Runtime raised while running step %s", step.id)
                attempt.error = f"{type(exc).__name__}: {exc}"
            else:
                attempt.result = result
                if not result.ok:
                    attempt.error = result.error or result.status.value
        attempt.completed_at = _now()
        task.result, task.error = attempt.result, attempt.error
        return attempt

    @staticmethod
    def _complete(task: TaskRun, output: Any) -> None:
        task.status = RunStatus.COMPLETED
        task.output = output
        task.error = None
        task.completed_at = _now()

    async def _fail(self, run: WorkflowRun, task: TaskRun, error: str) -> None:
        task.status = RunStatus.FAILED
        task.error = error
        task.completed_at = _now()
        status = task.result.status.value if task.result else AgentResultStatus.FAILED.value
        await self._task_event("task.failed", run, task, error=error, status=status)

    async def _settle(
        self, run: WorkflowRun, task: TaskRun, attempt: TaskAttempt, outputs: dict[str, Any]
    ) -> None:
        """Record an agent step's attempt; raise _StepFailed if it failed."""
        result = attempt.result
        if result is None or not result.ok:
            await self._fail(run, task, attempt.error or "failed")
            raise _StepFailed(f"Step '{task.step_id}' failed: {task.error}")
        self._complete(task, result.output)
        outputs[task.step_id] = result.output
        await self._task_event(
            "task.completed",
            run,
            task,
            attempt=len(task.attempts),
            duration_ms=result.duration_ms,
            usage=result.usage.model_dump(),
        )

    async def _review(
        self,
        workflow: WorkflowDefinition,
        run: WorkflowRun,
        step: WorkflowStep,
        task: TaskRun,
        attempt: TaskAttempt,
        outputs: dict[str, Any],
        sem: asyncio.Semaphore,
    ) -> None:
        targets = workflow.inputs_of(step.id)
        revisions = 0
        while True:
            result = attempt.result
            if result is None or not result.ok:
                await self._fail(run, task, attempt.error or "failed")
                raise _StepFailed(f"Step '{step.id}' failed: {task.error}")
            verdict = parse_verdict(result.output)
            await self._task_event(
                "review.completed",
                run,
                task,
                approved=None if verdict is None else verdict["approved"],
                feedback=None if verdict is None else verdict.get("feedback"),
                round=revisions + 1,
            )
            if verdict is None or verdict["approved"] or revisions >= step.max_revisions:
                break

            revisions += 1
            feedback = verdict.get("feedback")
            try:
                async with asyncio.TaskGroup() as group:
                    for target in targets:
                        group.create_task(
                            self._revise(
                                workflow,
                                run,
                                workflow.step(target),
                                outputs,
                                sem,
                                reviewer=step.id,
                                feedback=feedback,
                                round_=revisions,
                            )
                        )
            except* _StepFailed as failures:
                reason = "; ".join(str(e) for e in failures.exceptions)
                await self._fail(run, task, f"Revision failed: {reason}")
                raise _StepFailed(f"Step '{step.id}' failed: {task.error}") from None
            attempt = await self._attempt(
                run, task, step, self._step_input(workflow, step, run, outputs), sem
            )

        if verdict is None:  # free-form review: pass it through unchanged
            output: Any = result.output
        else:
            output = {**verdict, "revisions": revisions}
            if not verdict["approved"] and step.require_approval:
                task.output = output
                await self._fail(run, task, f"Reviewer did not approve: {verdict.get('feedback')}")
                raise _StepFailed(f"Step '{step.id}' failed: {task.error}")
        self._complete(task, output)
        outputs[step.id] = output
        await self._task_event(
            "task.completed",
            run,
            task,
            attempt=len(task.attempts),
            duration_ms=result.duration_ms,
            usage=result.usage.model_dump(),
        )

    async def _revise(
        self,
        workflow: WorkflowDefinition,
        run: WorkflowRun,
        step: WorkflowStep,
        outputs: dict[str, Any],
        sem: asyncio.Semaphore,
        *,
        reviewer: str,
        feedback: Any,
        round_: int,
    ) -> None:
        task = run.task(step.id)
        await self._task_event(
            "task.revision_requested", run, task, reviewer=reviewer, feedback=feedback, round=round_
        )
        revision = {
            "round": round_,
            "reviewer": reviewer,
            "feedback": feedback,
            "previous_output": outputs[step.id],
        }
        task_input = self._step_input(workflow, step, run, outputs, revision=revision)
        attempt = await self._attempt(run, task, step, task_input, sem)
        await self._settle(run, task, attempt, outputs)
