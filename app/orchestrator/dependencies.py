"""FastAPI wiring for the orchestrator: which runtimes the API serves."""

from __future__ import annotations

from functools import lru_cache

from agents import set_default_openai_key

from app.core.config import get_settings
from app.memory.store import DbMemoryStore
from app.policy.engine import DbPolicyStore, PolicyEngine
from app.runtimes import RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime
from app.runtimes.openai_runtime import OpenAIAgentRuntime
from app.tools.mcp import DbMcpServerStore


@lru_cache
def get_runtime_registry() -> RuntimeRegistry:
    """Runtimes available to API-triggered runs. Override this dependency in
    tests (e.g. with a FakeAgentRuntime) to run without a model provider."""
    settings = get_settings()
    if settings.openai_api_key is not None:
        # Settings may come from .env, which the SDK does not read on its own.
        set_default_openai_key(settings.openai_api_key.get_secret_value())
    runtimes = RuntimeRegistry(
        [
            OpenAIAgentRuntime(
                mcp_servers=DbMcpServerStore(),
                allow_stdio_mcp=settings.mcp_allow_stdio,
                memory=DbMemoryStore(),
            )
        ]
    )
    if settings.enable_fake_runtime:
        runtimes.register(FakeAgentRuntime(delay_seconds=settings.fake_runtime_delay_seconds))
    return runtimes


@lru_cache
def get_policy_engine() -> PolicyEngine:
    """Checks budgets, tool rules and model rules before each agent call."""
    return PolicyEngine(DbPolicyStore())
