"""Application settings, read from environment variables or a local .env file."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Agent Hub"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql+asyncpg://hub:hub@localhost:5432/agent_hub"
    redis_url: str = "redis://localhost:6379/0"

    # Secrets stay in the environment, never in agent config rows (plan section 5).
    openai_api_key: SecretStr | None = None

    # Default execution limits (plan section 11); agents can override per config.
    default_task_timeout_seconds: int = 120
    default_max_steps: int = 10
    # Most workflow steps one run executes at the same time (a workflow's
    # max_parallel overrides it).
    workflow_max_parallel: int = 4

    # Development only: also serve agents whose runtime is "fake", which answer
    # without calling a model, after a delay so progress events are visible.
    enable_fake_runtime: bool = False
    fake_runtime_delay_seconds: float = 1.0

    # Built dashboard (`npm run build` in frontend/). When the folder exists the
    # API also serves the dashboard at "/". Relative paths are from the repo root.
    frontend_dist_dir: str = "frontend/dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
