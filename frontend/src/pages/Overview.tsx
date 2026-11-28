import { Link } from "react-router-dom";
import { api } from "../api/client";
import { RunsTable } from "../components/RunsTable";
import { Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { useApi } from "../hooks/useApi";

export function OverviewPage() {
  const agents = useApi(() => api.listAgents(), []);
  const workflows = useApi(() => api.listWorkflows(), []);
  const runs = useApi(() => api.listRuns({ limit: 50 }), [], 5000);

  const runItems = runs.data?.items ?? [];
  const active = runItems.filter((r) => r.status === "running" || r.status === "pending").length;
  const finished = runItems.filter((r) => r.status === "succeeded" || r.status === "failed");
  const successRate = finished.length
    ? `${Math.round((finished.filter((r) => r.status === "succeeded").length / finished.length) * 100)}%`
    : "—";
  const workflowNames = new Set((workflows.data?.items ?? []).map((w) => w.name));
  const activeAgents = (agents.data?.items ?? []).filter((a) => a.status === "active").length;
  const firstUse = agents.data && agents.data.items.length === 0;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Agents, workflows and what is running right now."
        actions={
          <>
            <Link className="btn" to="/agents/new">
              New agent
            </Link>
            <Link className="btn btn-primary" to="/workflows/new">
              New workflow
            </Link>
          </>
        }
      />
      <ErrorBox error={agents.error ?? workflows.error ?? runs.error} onRetry={() => { agents.reload(); workflows.reload(); runs.reload(); }} />
      <div className="stats">
        <Stat label="Active agents" value={agents.data ? activeAgents : "…"} hint={agents.data ? `${agents.data.total ?? agents.data.items.length} registered` : undefined} />
        <Stat label="Workflows" value={workflows.data ? workflowNames.size : "…"} hint={workflows.data ? `${workflows.data.items.length} versions` : undefined} />
        <Stat label="Running now" value={runs.data ? active : "…"} hint="background runs" />
        <Stat label="Success rate" value={successRate} hint={`last ${finished.length} finished runs`} />
      </div>

      {firstUse && (
        <Card title="Get started">
          <p>No agents yet. Load the sample agents and workflows from a second PowerShell window:</p>
          <pre className="json">{`.venv\\Scripts\\Activate.ps1
python scripts\\seed_samples.py --runtime fake   # no API key needed (ENABLE_FAKE_RUNTIME=true)`}</pre>
          <p className="muted small">
            Or <Link to="/agents/new">create an agent</Link> here, then build a workflow from it.
          </p>
        </Card>
      )}

      <Card title="Recent runs" actions={<Link to="/runs">All runs →</Link>}>
        {runs.loading && !runs.data ? (
          <Loading />
        ) : runItems.length ? (
          <RunsTable runs={runItems.slice(0, 10)} />
        ) : (
          <Empty title="No runs yet">Open a workflow and press Run.</Empty>
        )}
      </Card>
    </>
  );
}
