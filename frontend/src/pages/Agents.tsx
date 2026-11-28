import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Card, Empty, ErrorBox, Loading, PageHeader, StatusBadge, Tags } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { timeAgo } from "../lib/format";

export function AgentsPage() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const agents = useApi(() => api.listAgents(status ? { status } : {}), [status]);
  const q = query.trim().toLowerCase();
  const items = (agents.data?.items ?? []).filter(
    (a) =>
      !q ||
      [a.slug, a.name, a.description ?? "", a.model, ...a.capabilities, ...a.tools].some((v) => v.toLowerCase().includes(q)),
  );

  return (
    <>
      <PageHeader
        title="Agents"
        subtitle="Specialist agents in the registry. Every change to a definition is kept as a version."
        actions={
          <Link className="btn btn-primary" to="/agents/new">
            New agent
          </Link>
        }
      />
      <div className="toolbar">
        <input className="input" placeholder="Search name, capability, tool…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Active and disabled</option>
          <option value="active">Active</option>
          <option value="disabled">Disabled</option>
          <option value="archived">Archived</option>
        </select>
      </div>
      <ErrorBox error={agents.error} onRetry={agents.reload} />
      <Card>
        {agents.loading && !agents.data ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty title={q ? "No agents match" : "No agents yet"}>
            {!q && (
              <>
                <Link to="/agents/new">Create one</Link> or run <code>python scripts\seed_samples.py --runtime fake</code>.
              </>
            )}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Status</th>
                  <th>Runtime / model</th>
                  <th>Capabilities</th>
                  <th>Tools</th>
                  <th>Version</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <Link to={`/agents/${a.slug}`}>
                        <strong>{a.name}</strong>
                      </Link>
                      <div className="mono small muted">{a.slug}</div>
                    </td>
                    <td>
                      <StatusBadge status={a.status} />
                    </td>
                    <td>
                      <div>{a.runtime}</div>
                      <div className="small muted">{a.model}</div>
                    </td>
                    <td>
                      <Tags items={a.capabilities} />
                    </td>
                    <td>
                      <Tags items={a.tools} />
                    </td>
                    <td>v{a.current_version}</td>
                    <td className="muted">{timeAgo(a.updated_at)}</td>
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
