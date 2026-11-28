import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- Database fixtures (Agent Registry tests) -----------------------------------
# Tests that use `db_session` or `clean_db` need a real PostgreSQL. Point
# TEST_DATABASE_URL at a throwaway database; it is migrated to head once per run
# and every table is truncated before each test. Without it, those tests skip.

import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.core.database import get_sessionmaker, reset_engine  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_TABLES = (
    "audit_logs, executions, tasks, workflow_runs, workflows, agent_versions, agents, tools, "
    "mcp_servers"
)


@pytest.fixture(scope="session")
def migrated_db():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env, check=True
    )
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    yield TEST_DATABASE_URL


@pytest.fixture
async def clean_db(migrated_db):
    await reset_engine()  # re-read DATABASE_URL
    async with get_sessionmaker()() as session:
        await session.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
        await session.commit()
    yield
    await reset_engine()


@pytest.fixture
async def db_session(clean_db):
    async with get_sessionmaker()() as session:
        yield session
