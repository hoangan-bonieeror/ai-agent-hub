"""Request/response models for the Agent Registry API.

The API exposes the agent definition flat (matching plan section 5); the
behavioural fields (instructions, tools, capabilities, limits) are stored in
the `agents.config` JSONB column.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Runtime = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,50}$")]
ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

# Fields that belong to the versioned definition. A change to any of them
# creates a new agent_versions row; a status change alone does not.
CONFIG_FIELDS = ("instructions", "tools", "capabilities", "limits")
DEFINITION_FIELDS = ("name", "description", "runtime", "model", *CONFIG_FIELDS)


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=10, ge=1, le=100)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class AgentCreate(BaseModel):
    """No field accepts arbitrary keys, so secrets have nowhere to land in
    config; credentials belong in env vars or a secret manager (plan section 5)."""

    model_config = ConfigDict(extra="forbid")

    slug: Slug = Field(description="Stable, human-readable id, e.g. 'research-agent'.")
    name: Name
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    runtime: Runtime = Field(examples=["openai_agents"])
    model: ModelName
    instructions: Annotated[str, StringConstraints(max_length=50_000)] = ""
    tools: list[Identifier] = Field(default_factory=list, max_length=100)
    capabilities: list[Identifier] = Field(default_factory=list, max_length=100)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    status: Literal["active", "disabled"] = "active"


class AgentUpdate(BaseModel):
    """PATCH body. Only the fields sent are changed. `slug` is immutable."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    runtime: Runtime | None = None
    model: ModelName | None = None
    instructions: Annotated[str, StringConstraints(max_length=50_000)] | None = None
    tools: list[Identifier] | None = Field(default=None, max_length=100)
    capabilities: list[Identifier] | None = Field(default=None, max_length=100)
    limits: AgentLimits | None = None
    status: Literal["active", "disabled"] | None = None
    expected_version: int | None = Field(
        default=None,
        ge=1,
        description="Reject with 409 unless the agent is still at this version.",
    )

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "AgentUpdate":
        # Omit a field to leave it unchanged; only `description` can be cleared.
        nulls = [
            f for f in self.model_fields_set if f != "description" and getattr(self, f) is None
        ]
        if nulls:
            raise ValueError(f"These fields cannot be null: {', '.join(sorted(nulls))}")
        return self


class AgentRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    runtime: str
    model: str
    instructions: str
    tools: list[str]
    capabilities: list[str]
    limits: AgentLimits
    status: str
    enabled: bool
    current_version: int
    created_at: datetime
    updated_at: datetime


class AgentList(BaseModel):
    items: list[AgentRead]
    total: int
    limit: int
    offset: int


class AgentVersionRead(BaseModel):
    agent_id: uuid.UUID
    version: int
    config_snapshot: dict[str, Any]
    created_by: str | None
    created_at: datetime
