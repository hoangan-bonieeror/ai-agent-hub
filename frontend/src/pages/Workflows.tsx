import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Workflow } from "../api/types";
import { Card, Empty, ErrorBox, Loading, PageHeader, StatusBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";

/** The API lists every version, by name then newest first. Keep the newest. */
export function latestVersions(items: Workflow[]): { latest: Workflow; versions: number }[] {
  const byName = new Map<string, { latest: Workflow; versions: number }>();
  for (const w of items) {
    const seen = byName.get(w.name);
    if (!seen) byName.set(w.name, { latest: w, versions: 1 });
    else {
      seen.versions += 1;
      if (w.version > seen.latest.version) seen.latest = w;
    }
  }
  return [...byName.values()];
}

export function WorkflowsPage() {
  const workflows = useApi(() => api.listWorkflows(), []);
  const groups = latestVersions(workflows.data?.items ?? []);

  return (
    <>
      <PageHeader
        title="Workflows"
        subtitle="Graphs of steps. Independent steps run in parallel; saving a name again adds a version."
        actions={
          <Link className="btn btn-primary" to="/workflows/new">
            New workflow
          </Link>
        }
      />
      <ErrorBox error={workflows.error} onRetry={workflows.reload} />
      <Card>
        {workflows.loading && !workflows.data ? (
          <Loading />
        ) : groups.length === 0 ? (
          <Empty title="No workflows yet">
            <Link to="/workflows/new">Build one</Link> from your agents.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Steps</th>
                  <th>Latest version</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {groups.map(({ latest, versions }) => (
                  <tr key={latest.name}>
                    <td>
                      <Link to={`/workflows/${latest.id}`}>
                        <strong>{latest.name}</strong>
                      </Link>
                      {latest.description && <div className="small muted clip">{latest.description}</div>}
                    </td>
                    <td>{latest.definition.steps.length}</td>
                    <td>
                      v{latest.version} <span className="muted small">({versions} total)</span>
                    </td>
                    <td>
                      <StatusBadge status={latest.enabled ? "active" : "disabled"} />
                    </td>
                    <td className="right">
                      <Link className="btn btn-small" to={`/workflows/${latest.id}`}>
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
