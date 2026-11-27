# AI Agent Hub

A central hub to register, configure, run and orchestrate specialist AI agents.
Architecture and roadmap: see the project's *AI Agent Hub Architecture and Implementation Plan*.

This repository is the Phase 1 skeleton: FastAPI backend, PostgreSQL, Redis and docker-compose.

## Run with Docker

```bash
cp .env.example .env        # optional; add OPENAI_API_KEY when needed
docker compose up --build
```

- API: http://localhost:8000 (OpenAPI docs at `/docs`)
- Liveness: `GET /health`
- Readiness (checks PostgreSQL and Redis): `GET /health/ready`

The `api` container runs `alembic upgrade head` before starting.

## Run locally

```bash
docker compose up -d postgres redis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
# DB-backed tests need a throwaway database; they are skipped when this is unset.
createdb -h localhost -U hub agent_hub_test
export TEST_DATABASE_URL=postgresql+asyncpg://hub:hub@localhost:5432/agent_hub_test
pytest
```

## Layout

```
app/
  main.py               FastAPI app factory, routers, lifespan
  api/health.py         /health and /health/ready
  api/v1/               agents, workflows, tasks, executions (plan section 8)
  core/                 config (env settings), database (Base, engine, session), redis, security, permissions
  models/               SQLAlchemy models (plan section 6)
  repositories/         data access
  registry/             agent and tool registry
  orchestrator/         planner, router, engine, aggregator
  runtimes/             AgentRuntime interface and adapters (plan section 9)
  workflows/ memory/ tools/ observability/ schemas/   placeholders for later phases
migrations/             Alembic (async); env.py imports app.models for autogenerate
tests/
frontend/               React + TypeScript dashboard (Phase 3)
```

## Status

Endpoints that are not built yet return `501 Not Implemented`.

Secrets (API keys) come from environment variables only; never store them in agent config rows.
