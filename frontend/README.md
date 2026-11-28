# AI Agent Hub dashboard

React 19 + TypeScript + Vite. See the main README's "Dashboard" section for how to run it.

```
src/
  api/          typed client for /api/v1 (client.ts) and response types (types.ts)
  hooks/        useApi (load + poll), useRunEvents (Server-Sent Events for one run)
  lib/          graph.ts (dependency rules, checks, layered layout), builder.ts, format.ts
  components/   DagView (SVG workflow graph), RunsTable, small UI pieces
  pages/        Overview, Agents, AgentForm, AgentDetail, Workflows, WorkflowDetail,
                WorkflowBuilder, Runs, RunDetail
```

`lib/graph.ts` mirrors the server's rules in `app/orchestrator/workflow.py` (how
`depends_on` and `uses` default, cycle and ancestry checks) so the builder can flag problems
early. The server stays the authority: it re-checks every definition on save.
