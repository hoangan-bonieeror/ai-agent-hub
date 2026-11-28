"""Run a sample workflow in-process, printing its progress events.

python scripts/run_sample_workflow.py                                  # offline, fake runtime
python scripts/run_sample_workflow.py --workflow research-and-report   # parallel fan-out sample
python scripts/run_sample_workflow.py --live [--model gpt-4.1-mini]    # needs OPENAI_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.samples import SAMPLE_AGENTS, SAMPLE_WORKFLOWS
from app.orchestrator import AgentOrchestrator, InMemoryAgentRegistry, WorkflowEvent
from app.runtimes import RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime

STARTED = time.monotonic()

SAMPLE_INPUTS = {
    "research-finance-review": {
        "request": "Find a used car in California under $15,000 and plan how to pay for it",
        "budget_usd": 15000,
    },
    "research-and-report": {"topic": "AI Agent Hub", "depth": "deep"},
}


def print_event(event: WorkflowEvent) -> None:
    extra = f" {event.step_id}" if event.step_id else ""
    extra += f" ({event.agent_id})" if event.agent_id else ""
    print(f"  {time.monotonic() - STARTED:6.2f}s  {event.type}{extra}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Call OpenAI through the Agents SDK")
    parser.add_argument("--model", default=os.environ.get("AGENT_HUB_DEFAULT_MODEL"))
    parser.add_argument(
        "--workflow", default="research-finance-review", choices=sorted(SAMPLE_WORKFLOWS)
    )
    args = parser.parse_args()
    workflow = SAMPLE_WORKFLOWS[args.workflow]

    agents = SAMPLE_AGENTS
    if args.live:
        from app.runtimes.openai_runtime import OpenAIAgentRuntime

        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("Set OPENAI_API_KEY to use --live")
        runtimes = RuntimeRegistry([OpenAIAgentRuntime(default_model=args.model)])
    else:
        # Same agents, pointed at the offline runtime: only the runtime name changes.
        agents = [a.model_copy(update={"runtime": "fake"}) for a in SAMPLE_AGENTS]
        # The fake reviewer asks for one revision, so the review loop shows up.
        verdicts = iter([{"approved": False, "feedback": "Add sources for every figure."}])

        def reviewer(config, task_input):
            if workflow.step("review").kind != "review":
                return "[Reviewer Agent] final answer"
            return next(verdicts, {"approved": True, "summary": "Ready to publish."})

        runtimes = RuntimeRegistry(
            [FakeAgentRuntime({"reviewer-agent": reviewer}, delay_seconds=0.5)]
        )

    orchestrator = AgentOrchestrator(InMemoryAgentRegistry(agents), runtimes, on_event=print_event)
    run = await orchestrator.run_workflow(workflow, SAMPLE_INPUTS[workflow.name])
    print(f"\nstatus: {run.status.value}")
    if run.error:
        print(f"error: {run.error}")
    print(f"usage: {run.usage.model_dump()}")
    print("output:")
    print(run.output if isinstance(run.output, str) else json.dumps(run.output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
