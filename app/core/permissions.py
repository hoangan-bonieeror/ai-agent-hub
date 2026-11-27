"""Permission / policy engine (plan section 11): deny by default.

Placeholder until Phase 3. Grants will be checked per agent, user, tenant and tool.
"""


def is_tool_allowed(agent_id: str, tool_name: str, granted_tools: list[str]) -> bool:
    return tool_name in granted_tools
