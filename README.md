# AI Agent Hub

A central hub to register, configure, run and orchestrate specialist AI agents.
Architecture and roadmap: see the project's *AI Agent Hub Architecture and Implementation Plan*.

Built so far: Phase 1 (FastAPI, PostgreSQL, Agent Registry, runtime adapters, workflow API),
Phase 2 (multi-agent workflows: dependencies, parallel steps, reviewer and aggregator
steps, live progress over Server-Sent Events, run/task/execution status) and, from Phase 3,
the web dashboard with a visual workflow builder and MCP tool servers.

Commands below are for Windows PowerShell. On macOS/Linux use `source .venv/bin/activate`,
`cp` instead of `Copy-Item`, and `export NAME=value` instead of `$env:NAME = "value"`.

## Run with Docker

```powershell
Copy-Item .env.example .env   # optional; add OPENAI_API_KEY when needed
docker compose up --build
```

- Dashboard: http://localhost:8000 (the image builds it; see [Dashboard](#dashboard))
- API: http://localhost:8000/api/v1 (OpenAPI docs at `/docs`)
- Liveness: `GET /health`
- Readiness (checks PostgreSQL and Redis): `GET /health/ready`

The `api` container runs `alembic upgrade head` before starting.

## Run locally

```powershell
docker compose up -d postgres redis
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

### Try a multi-agent workflow without an API key

Set `ENABLE_FAKE_RUNTIME=true` in `.env` (agents with runtime `fake` then answer without
calling a model, after a short delay), restart `uvicorn`, then in a second terminal:

```powershell
.venv\Scripts\Activate.ps1
python scripts\seed_samples.py --runtime fake

$run = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/workflows/research-and-report/run `
  -ContentType "application/json" `
  -Body '{"input": {"topic": "AI Agent Hub"}, "wait": false}'

curl.exe -N "http://localhost:8000$($run.links.events)"     # live progress (SSE)
Invoke-RestMethod "http://localhost:8000$($run.links.self)"  # status and outputs
```

Use `curl.exe`, not `curl`: in PowerShell `curl` is an alias that buffers the stream.
For real agents, seed without `--runtime fake` and set `OPENAI_API_KEY`.

`python scripts\run_sample_workflow.py --workflow research-and-report` runs the same
workflow in-process, with no server or database, and prints its events.

## Dashboard

A React + TypeScript app in `frontend/`. It needs [Node.js 22 LTS](https://nodejs.org)
(`node -v` should print v22 or newer) when you run it outside Docker.

| Page | What you can do |
| --- | --- |
| Overview | Counts, success rate, runs in progress and the latest runs. |
| Agents | List, search and filter agents; create and edit them (each change is a new version); see every version; enable, disable or archive; try an agent with a one-off task. |
| Workflows | See each workflow as a graph; run it with JSON input; switch between versions. |
| Workflow builder | Add agent, review and aggregate steps, connect them with "runs after", and watch the graph update. Mistakes (cycles, missing agents, unknown steps) are flagged before saving. A JSON tab edits the raw definition. Editing a workflow saves a new version. |
| Tools | Add MCP servers, discover their tools, limit which tools agents may get. |
| Runs | Filter by workflow and status. A run's page follows it live over SSE: each step's status on the graph, outputs, attempts, token usage, execution logs, the event log, and a Cancel button. |

**One URL (after a build).** Build once, and `uvicorn` serves the dashboard at
http://localhost:8000 next to the API:

```powershell
cd frontend
npm install
npm run build        # writes frontend\dist, which the API serves
cd ..
uvicorn app.main:app --reload
```

**While changing the dashboard.** Run the dev server with hot reload next to `uvicorn`; it
forwards `/api` calls to port 8000:

```powershell
cd frontend
npm run dev          # http://localhost:5173
```

Set `$env:HUB_API_URL = "http://localhost:9000"` first if the API is on another port.
`npm test` runs the dashboard's unit tests and `npm run typecheck` the TypeScript checks.

The dashboard sends `X-Actor: dashboard` with its requests, so agent versions and runs it
creates show "dashboard" as their author. There is no login yet (Phase 4: auth and RBAC), so
do not expose port 8000 beyond your machine.

## Tools and MCP servers

Agents only get the tools named in their `tools` list; anything else is denied.

| Name in `tools` | Grants |
| --- | --- |
| `web_search` | The built-in OpenAI web search tool. |
| `mcp:<server>` | Every tool the MCP server allows. |
| `mcp:<server>/<tool>` | One tool of that server. |

Register an MCP server on the dashboard's **Tools** page (or `POST /api/v1/mcp-servers`), then
press **Discover tools** (`POST /api/v1/mcp-servers/{name}/discover`) to load its tool list
into the registry (`GET /api/v1/tools`). A server's `allowed_tools` caps what any agent can be
granted. Each agent run connects to the servers it needs and disconnects when it ends.

Servers that run as a local program (`stdio`) are off by default, because the API has no
login yet and anyone who can reach it could make it start a program. For local use, set
`MCP_ALLOW_STDIO=true` in `.env` and restart `uvicorn`. Example, the reference filesystem
server sharing one folder (needs Node.js):

```powershell
$body = @{
  name = "files"; transport = "stdio"; command = "npx.cmd"
  args = @("-y", "@modelcontextprotocol/server-filesystem", "D:\shared-docs")
  allowed_tools = @("read_text_file", "list_directory", "search_files")
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/mcp-servers `
  -ContentType "application/json" -Body $body
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/mcp-servers/files/discover
```

Then add `mcp:files` to an agent's tools. On Windows use `npx.cmd`, not `npx`. HTTP servers
use `transport: "streamable_http"` (or `"sse"`) and a `url`.

Secrets never go into a server's stored config. `env_from_host` lists names of environment
variables the Hub passes to a stdio server (for example `["GITHUB_TOKEN"]`), and
`headers_from_env` maps an HTTP header to the variable holding its value
(`{"Authorization": "MCP_AUTH_HEADER"}`). Set those variables in `.env`.

## Tests

```powershell
# DB-backed tests need a throwaway database; they are skipped when this is unset.
docker compose exec postgres createdb -U hub agent_hub_test
$env:TEST_DATABASE_URL = "postgresql+asyncpg://hub:hub@localhost:5432/agent_hub_test"
pytest
```

## Workflows

A workflow is a graph of steps. A step starts once every step in its `depends_on` has
finished; steps that do not depend on each other run in parallel (at most
`max_parallel` at once, default `WORKFLOW_MAX_PARALLEL=4`).

```json
{
  "name": "research-and-report",
  "max_parallel": 3,
  "timeout_seconds": 900,
  "steps": [
    {"id": "plan", "agent_id": "planner-agent", "task": "Plan a report on the topic."},
    {"id": "research", "agent_id": "research-agent", "task": "...", "depends_on": ["plan"]},
    {"id": "competitors", "agent_id": "competitor-agent", "task": "...", "depends_on": ["plan"]},
    {"id": "architecture", "agent_id": "architecture-agent", "task": "...", "depends_on": ["plan"]},
    {"id": "review", "kind": "review", "agent_id": "reviewer-agent", "task": "Check for gaps.",
     "depends_on": ["research", "competitors", "architecture"], "max_revisions": 1},
    {"id": "report", "kind": "aggregate", "depends_on": ["review"],
     "uses": ["plan", "research", "competitors", "architecture", "review"]}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `depends_on` | Steps that must finish first. `[]` makes a root step. Unset: `uses` if given, else every earlier step, so Phase 1 pipelines still run in order. |
| `uses` | Steps whose outputs the agent receives as `previous_outputs`. Unset: its `depends_on`. Each must be an ancestor of the step. |
| `kind: "agent"` | Default. The agent runs `task`. |
| `kind: "review"` | The agent checks the outputs in `uses` and replies `{"approved": bool, "feedback": "..."}`. On a rejection, up to `max_revisions` times, the reviewed steps run again with the feedback (as `revision` in their input) and the review repeats. With `require_approval`, a final rejection fails the run. A reply without that JSON passes through as a plain review. |
| `kind: "aggregate"` | No agent: the output is `{step_id: output}` of the steps in `uses`. |
| `output_step` | Step whose output is the run output. Default: the last step. |
| `timeout_seconds` | Limit for the whole run, on top of each agent's own timeout. |

Definitions are checked when saved: unknown steps, cycles, and reads of a step that a
review may still send back are rejected with 422.

The first failed step fails the run: steps still running are cancelled and steps not
started are skipped.

## Running and following a run

| Method | Endpoint | What it does |
| --- | --- | --- |
| POST | `/api/v1/workflows/{id or name}/run` | Body `{"input": {...}, "wait": true}`. `wait: true` returns the finished run (200). `wait: false` returns 202 at once with `links.events` and `links.self`. |
| GET | `/api/v1/runs` | Runs, newest first. Filters: `workflow`, `status`, `limit`, `offset`. |
| GET | `/api/v1/runs/{run_id}` | Live status of the run and every task, outputs, token usage. |
| GET | `/api/v1/runs/{run_id}/events` | Server-Sent Events: past events, then live ones until the run ends. Reconnect with `Last-Event-ID` to resume. |
| POST | `/api/v1/runs/{run_id}/cancel` | Cancel a background run. |
| GET | `/api/v1/tasks/{task_id}` | One step of one run, with its executions. |
| GET | `/api/v1/executions/{execution_id}` | One agent call: status, timing, token usage, error, logs. A step sent back by a reviewer has one execution per attempt. |
| GET | `/api/v1/workflows`, `/api/v1/workflows/{id or name}` | Stored workflow definitions. |

Events: `workflow.started`, `task.queued`, `task.started`, `task.completed`, `task.failed`,
`task.skipped`, `task.cancelled`, `review.completed`, `task.revision_requested`,
`workflow.completed`, `workflow.failed`, `workflow.cancelled`. Each carries `run_id`,
`step_id`, `task_id` and `agent_id` where they apply, and a `data` object.

Background runs execute inside the API process: they are cancelled (and recorded as
cancelled) when the server stops, and their event history is kept in memory for 10 minutes
after they finish. Run a single API worker; a shared queue and worker pool come in Phase 4.

## Layout

```
app/
  main.py               FastAPI app factory, routers, lifespan
  api/health.py         /health and /health/ready
  api/v1/               agents, workflows, runs, tasks, executions, runtimes, mcp-servers, tools
  core/                 config (env settings), database (Base, engine, session), redis, security, permissions
  models/               SQLAlchemy models (plan section 6)
  repositories/         data access
  registry/             agent and tool registry
  orchestrator/         workflow graph, engine (parallel DAG), review, run recorder, SSE broker, runner
  runtimes/             AgentRuntime interface and adapters (plan section 9)
  tools/mcp.py          MCP server specs, tool grants, connecting and discovering tools
  workflows/ memory/ observability/ schemas/   placeholders for later phases
migrations/             Alembic (async); env.py imports app.models for autogenerate
scripts/                seed_samples.py (load samples via the API), run_sample_workflow.py (in-process demo)
tests/
frontend/               React + TypeScript dashboard (Vite); `app/frontend.py` serves its build
```

Secrets (API keys) come from environment variables only; never store them in agent config rows.
