"""Starts workflow runs for the API, inline or in the background.

Background runs are asyncio tasks in the API process: good enough for one
server and local use. They do not survive a restart; on shutdown they are
cancelled and recorded as cancelled. A durable queue and worker pool are
Phase 4 of the plan.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.orchestrator.engine import AgentOrchestrator
from app.orchestrator.events import RunEventBroker
from app.orchestrator.persistence import RunRecorder
from app.orchestrator.workflow import RunStatus, WorkflowDefinition, WorkflowEvent, WorkflowRun
from app.registry.agent_registry import AgentRegistry
from app.runtimes import RuntimeRegistry

logger = logging.getLogger(__name__)


class RunManager:
    def __init__(self, broker: RunEventBroker | None = None) -> None:
        self.broker = broker or RunEventBroker()
        self._background: dict[str, asyncio.Task[None]] = {}

    async def start(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        workflow_id: uuid.UUID,
        definition: WorkflowDefinition,
        workflow_input: dict,
        runtimes: RuntimeRegistry,
        requested_by: str | None,
        max_parallel: int,
        wait: bool,
    ) -> WorkflowRun:
        """Record the run, then execute it. With ``wait`` the call returns once
        the run has finished; otherwise it returns the pending run at once."""
        run = AgentOrchestrator.new_run(definition, workflow_input)
        recorder = RunRecorder(sessionmaker)
        try:
            await recorder.create(workflow_id, run, requested_by)
        except BaseException:
            await recorder.close()
            raise
        self.broker.open(run.id)
        job = self._execute(sessionmaker, definition, run, runtimes, recorder, max_parallel)
        if wait:
            await job
        else:
            task = asyncio.create_task(job, name=f"workflow-run-{run.id}")
            self._background[run.id] = task
            task.add_done_callback(lambda _: self._background.pop(run.id, None))
        return run

    def is_running(self, run_id: str) -> bool:
        return run_id in self._background

    async def cancel(self, run_id: str) -> bool:
        task = self._background.get(run_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def shutdown(self) -> None:
        tasks = list(self._background.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        definition: WorkflowDefinition,
        run: WorkflowRun,
        runtimes: RuntimeRegistry,
        recorder: RunRecorder,
        max_parallel: int,
    ) -> None:
        async def on_event(event: WorkflowEvent) -> None:
            await recorder.record(event, run)  # store first: clients may query on the event
            await self.broker.publish(event)

        try:
            async with sessionmaker() as registry_session:
                orchestrator = AgentOrchestrator(
                    AgentRegistry(registry_session),
                    runtimes,
                    on_event=on_event,
                    max_parallel=max_parallel,
                )
                await orchestrator.run_workflow(definition, run.input, run=run)
        except asyncio.CancelledError:
            if not run.finished:  # cancelled before the orchestrator could record it
                run.status, run.error = RunStatus.CANCELLED, "Run was cancelled"
            raise
        except Exception as exc:  # orchestrator bug: keep the record consistent
            logger.exception("Workflow run %s crashed", run.id)
            run.status = RunStatus.FAILED
            run.error = f"Internal error while running the workflow: {type(exc).__name__}"
            for task in run.tasks:
                if task.status in (RunStatus.PENDING, RunStatus.QUEUED, RunStatus.RUNNING):
                    task.status = RunStatus.CANCELLED
            await self.broker.publish(
                WorkflowEvent(type="workflow.failed", run_id=run.id, data={"error": run.error})
            )
        finally:
            try:
                await recorder.finish(run)
            except Exception:
                logger.exception("Could not store the final state of run %s", run.id)
            finally:
                await recorder.close()
                await self.broker.close(run.id)


@lru_cache
def get_run_manager() -> RunManager:
    return RunManager()
