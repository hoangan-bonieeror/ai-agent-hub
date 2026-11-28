"""Policy definitions: what each kind of policy checks and how its rules look.

* ``budget``: stop (or warn) once spend in a period reaches a dollar limit.
* ``tools``: tools agents may not use (patterns of tool names).
* ``models``: the only models agents may use (patterns of model names).

A policy applies to every agent (``scope: global``), to one agent
(``scope: agent``, ``target`` = agent slug) or to the agents of one workflow
(``scope: workflow``, ``target`` = workflow name).
"""

from __future__ import annotations

from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PolicyKind = Literal["budget", "tools", "models"]
PolicyScope = Literal["global", "agent", "workflow"]
BudgetPeriod = Literal["day", "month", "total"]
Pattern = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class BudgetRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit_usd: float = Field(gt=0, le=1_000_000, description="Dollar limit for the period.")
    period: BudgetPeriod = Field(
        default="month", description="day and month start at 00:00 UTC; total never resets."
    )
    action: Literal["block", "warn"] = Field(
        default="block",
        description="block: refuse agent calls once the limit is reached. warn: only flag them.",
    )


class ToolRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deny: list[Pattern] = Field(
        min_length=1,
        description="Tool names or patterns, e.g. web_search, mcp:files (a whole server), "
        "mcp:files/write_*, mcp:*/delete_*.",
    )


class ModelRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[Pattern] = Field(
        min_length=1, description="Model names or patterns, e.g. gpt-4.1-mini, gpt-4.1*."
    )


RULES_BY_KIND: dict[str, type[BaseModel]] = {
    "budget": BudgetRules,
    "tools": ToolRules,
    "models": ModelRules,
}


class PolicySpec(BaseModel):
    """A policy as the API accepts it and the engine checks it."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: str | None = Field(default=None, max_length=500)
    kind: PolicyKind
    scope: PolicyScope = "global"
    target: str | None = Field(default=None, max_length=200)
    rules: dict[str, Any]
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self) -> PolicySpec:
        if self.scope == "global":
            if self.target:
                raise ValueError("A global policy has no target")
            self.target = None
        elif not (self.target or "").strip():
            raise ValueError(
                f"A {self.scope} policy needs a target (the {self.scope} it applies to)"
            )
        else:
            self.target = self.target.strip()
        self.rules = RULES_BY_KIND[self.kind].model_validate(self.rules).model_dump()
        return self

    def applies_to(self, agent_id: str, workflow: str | None) -> bool:
        if not self.enabled:
            return False
        if self.scope == "global":
            return True
        if self.scope == "agent":
            return self.target == agent_id
        return workflow is not None and self.target == workflow

    @property
    def budget(self) -> BudgetRules:
        return BudgetRules.model_validate(self.rules)


def tool_denied(tool: str, patterns: list[str]) -> bool:
    """Whether a tool name matches a deny pattern. Denying a whole MCP server
    (``mcp:files``) also denies each of its tools (``mcp:files/read``)."""
    for pattern in patterns:
        if fnmatchcase(tool, pattern):
            return True
        if pattern.startswith("mcp:") and "/" not in pattern and tool.startswith(pattern + "/"):
            return True
    return False


def model_allowed(model: str | None, patterns: list[str]) -> bool:
    return model is not None and any(fnmatchcase(model, p) for p in patterns)


def period_start(period: BudgetPeriod, now: datetime | None = None) -> datetime | None:
    """Start of the budget period containing ``now`` (UTC); None for ``total``."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None
