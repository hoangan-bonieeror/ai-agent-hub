"""Run the sample research -> finance -> review workflow.

python scripts/run_sample_workflow.py            # offline, fake runtime
OPENAI_API_KEY=... python scripts/run_sample_workflow.py --live [--model gpt-4.1-mini]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.samples import RESEARCH_FINANCE_REVIEW, SAMPLE_AGENTS
from app.orchestrator import AgentOrchestrator, InMemoryAgentRegistry, WorkflowEvent
from app.runtimes import RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime


def print_event(event: WorkflowEvent) -> None:
    extra = f" {event.step_id} ({event.agent_id})" if event.step_id else ""
    print(f"  {event.type}{extra}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Call OpenAI through the Agents SDK")
    parser.add_argument("--model", default=os.environ.get("AGENT_HUB_DEFAULT_MODEL"))
    args = parser.parse_args()

    agents = SAMPLE_AGENTS
    if args.live:
        from app.runtimes.openai_runtime import OpenAIAgentRuntime

        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("Set OPENAI_API_KEY to use --live")
        runtimes = RuntimeRegistry([OpenAIAgentRuntime(default_model=args.model)])
    else:
        # Same agents, pointed at the offline runtime: only the runtime name changes.
        agents = [a.model_copy(update={"runtime": "fake"}) for a in SAMPLE_AGENTS]
        runtimes = RuntimeRegistry([FakeAgentRuntime()])

    orchestrator = AgentOrchestrator(InMemoryAgentRegistry(agents), runtimes, on_event=print_event)
    run = await orchestrator.run_workflow(
        RESEARCH_FINANCE_REVIEW,
        {
            "request": "Find a used car in California under $15,000 and plan how to pay for it",
            "budget_usd": 15000,
        },
    )
    print(f"\nstatus: {run.status.value}")
    if run.error:
        print(f"error: {run.error}")
    print(f"usage: {run.usage.model_dump()}")
    print("output:")
    print(run.output if isinstance(run.output, str) else json.dumps(run.output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
