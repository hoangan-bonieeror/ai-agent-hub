"""Sample agents and workflows from the plan (sections 1.2 and 7.1).

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

PLANNER_AGENT = AgentConfig(
    id="planner-agent",
    name="Planner Agent",
    description="Break a request into focused questions for specialist agents",
    runtime="openai_agents",
    instructions=(
        "You are a planner. Break the user's request into a short plan: the goal, the "
        "questions each specialist (research, competitors, architecture) should answer, and "
        "what a good final report must contain. Do not answer the questions yourself."
    ),
    tools=[],
    capabilities=["planning"],
    limits=AgentLimits(max_steps=4, timeout_seconds=60),
)

COMPETITOR_AGENT = AgentConfig(
    id="competitor-agent",
    name="Competitor Agent",
    description="Map competing products and how they differ",
    runtime="openai_agents",
    instructions=(
        "You are a competitive analyst. Following the plan you are given, list the main "
        "competing products or approaches, what each does well and badly, and where the gaps "
        "are. Mark anything you are not sure of."
    ),
    tools=["web_search"],
    capabilities=["research", "competitive-analysis"],
    limits=AgentLimits(max_steps=10, timeout_seconds=120),
)

ARCHITECTURE_AGENT = AgentConfig(
    id="architecture-agent",
    name="Architecture Agent",
    description="Propose a technical architecture and its trade-offs",
    runtime="openai_agents",
    instructions=(
        "You are a software architect. Following the plan you are given, propose an "
        "architecture: the main components, how data flows between them, the technology "
        "choices, and the trade-offs and risks of each choice."
    ),
    tools=[],
    capabilities=["architecture", "design"],
    limits=AgentLimits(max_steps=6, timeout_seconds=120),
)

SAMPLE_AGENTS: list[AgentConfig] = [
    RESEARCH_AGENT,
    FINANCE_AGENT,
    REVIEWER_AGENT,
    PLANNER_AGENT,
    COMPETITOR_AGENT,
    ARCHITECTURE_AGENT,
]

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

# Fan-out / fan-in from plan section 7.1: a planner, three specialists in
# parallel, a reviewer that can send work back once, and an aggregated report.
RESEARCH_AND_REPORT = WorkflowDefinition(
    name="research-and-report",
    description="Plan, research in parallel, review, and assemble a report.",
    steps=[
        WorkflowStep(
            id="plan",
            agent_id="planner-agent",
            task="Plan a report on the topic in workflow_input.",
        ),
        WorkflowStep(
            id="research",
            agent_id="research-agent",
            task="Research the topic following the research questions in the plan.",
            depends_on=["plan"],
        ),
        WorkflowStep(
            id="competitors",
            agent_id="competitor-agent",
            task="Analyze competitors following the competitor questions in the plan.",
            depends_on=["plan"],
        ),
        WorkflowStep(
            id="architecture",
            agent_id="architecture-agent",
            task="Propose an architecture following the architecture questions in the plan.",
            depends_on=["plan"],
        ),
        WorkflowStep(
            id="review",
            kind="review",
            agent_id="reviewer-agent",
            task=(
                "Review the research, competitor and architecture outputs for errors, gaps "
                "and contradictions between them. Add a short summary of the findings."
            ),
            depends_on=["research", "competitors", "architecture"],
            max_revisions=1,
        ),
        WorkflowStep(
            id="report",
            kind="aggregate",
            depends_on=["review"],
            uses=["plan", "research", "competitors", "architecture", "review"],
        ),
    ],
)

SAMPLE_WORKFLOWS: dict[str, WorkflowDefinition] = {
    wf.name: wf for wf in (RESEARCH_FINANCE_REVIEW, RESEARCH_AND_REPORT)
}
