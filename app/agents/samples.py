"""The three sample agents and sample workflow from the plan (section 1.2).

These are seed data. In the running Hub they are loaded into the Agent
Registry (database), so editing them there needs no orchestrator change.
"""

from __future__ import annotations

from app.orchestrator.workflow import WorkflowDefinition, WorkflowStep
from app.runtimes.base import AgentConfig, AgentLimits

RESEARCH_AGENT = AgentConfig(
    id="research-agent",
    name="Research Agent",
    description="Research topics and collect evidence",
    runtime="openai_agents",
    instructions=(
        "You are a research specialist. Research the assigned task and return structured "
        "findings: key facts, figures with their sources, and open questions. Say clearly "
        "when data is missing or uncertain instead of guessing."
    ),
    tools=["web_search"],
    capabilities=["research", "summarization"],
    limits=AgentLimits(max_steps=10, timeout_seconds=120),
)

FINANCE_AGENT = AgentConfig(
    id="finance-agent",
    name="Finance Agent",
    description="Analyze budgets, costs and financing options",
    runtime="openai_agents",
    instructions=(
        "You are a personal finance analyst. Using the user's input and the research provided, "
        "analyze affordability, total cost of ownership and financing options. Show your "
        "calculations and state every assumption."
    ),
    tools=[],
    capabilities=["finance", "budgeting"],
    limits=AgentLimits(max_steps=6, timeout_seconds=90),
)

REVIEWER_AGENT = AgentConfig(
    id="reviewer-agent",
    name="Reviewer Agent",
    description=(
        "Check earlier results for quality, gaps and contradictions, then write the final answer"
    ),
    runtime="openai_agents",
    instructions=(
        "You are a reviewer and editor. Check the earlier agents' outputs for errors, gaps "
        "and contradictions, then write the final answer for the user: a short summary, "
        "recommendations, and a list of caveats you found."
    ),
    tools=[],
    capabilities=["review", "aggregation"],
    limits=AgentLimits(max_steps=4, timeout_seconds=90),
)

SAMPLE_AGENTS: list[AgentConfig] = [RESEARCH_AGENT, FINANCE_AGENT, REVIEWER_AGENT]

RESEARCH_FINANCE_REVIEW = WorkflowDefinition(
    name="research-finance-review",
    description="Research a purchase, analyze the budget, then review and summarize.",
    steps=[
        WorkflowStep(
            id="research",
            agent_id="research-agent",
            task=(
                "Research the market described in workflow_input and "
                "list options that fit the stated budget."
            ),
        ),
        WorkflowStep(
            id="finance",
            agent_id="finance-agent",
            task=(
                "Build a financial plan for the options found "
                "by the research step, within the user's budget."
            ),
            uses=["research"],
        ),
        WorkflowStep(
            id="review",
            agent_id="reviewer-agent",
            task=(
                "Review the research and financial plan, fix "
                "or flag problems, and write the final answer."
            ),
        ),
    ],
)

SAMPLE_WORKFLOWS: dict[str, WorkflowDefinition] = {
    RESEARCH_FINANCE_REVIEW.name: RESEARCH_FINANCE_REVIEW
}
