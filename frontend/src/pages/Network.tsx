import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { NetworkGraph } from "../components/NetworkGraph";
import { Card, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { useApi } from "../hooks/useApi";

const WINDOWS = [7, 30, 90];

export function NetworkPage() {
  const [days, setDays] = useState(30);
  const net = useApi(() => api.network(days), [days], 5000);
  const navigate = useNavigate();

  const d = net.data;
  const working = d?.nodes.filter((n) => n.running > 0) ?? [];
  const strongest = [...(d?.links ?? [])].sort((a, b) => b.runs - a.runs).slice(0, 6);
  const names = new Map(d?.nodes.map((n) => [n.id, n.name]));

  return (
    <>
      <PageHeader
        title="Network"
        subtitle="Every agent is a neuron. Agents that work in the same runs are wired together, and busier links glow brighter."
        actions={
          <div className="tabs" role="group" aria-label="Time window">
            {WINDOWS.map((w) => (
              <button key={w} className={w === days ? "active" : ""} onClick={() => setDays(w)}>
                {w} days
              </button>
            ))}
          </div>
        }
      />
      <ErrorBox error={net.error} onRetry={net.reload} />
      <div className="stats">
        <Stat label="Agents" value={d ? d.nodes.length : "…"} hint={d ? `${d.nodes.filter((n) => n.status === "active").length} active` : undefined} />
        <Stat label="Connections" value={d ? d.links.length : "…"} hint="pairs that shared a run" />
        <Stat label="Runs" value={d ? d.runs : "…"} hint={`last ${days} days`} />
        <Stat label="Working now" value={d ? working.length : "…"} hint={working.length ? working.map((n) => n.name).join(", ") : "all quiet"} />
      </div>

      <Card className="net-card">
        {net.loading && !d ? <Loading /> : d && <NetworkGraph data={d} height={560} onSelect={(id) => navigate(`/agents/${id}`)} />}
        <div className="legend net-legend">
          <span className="legend-item">
            <i className="lg-neuron" /> agent (size = runs)
          </span>
          <span className="legend-item">
            <i className="lg-firing" /> working now
          </span>
          <span className="legend-item">
            <i className="lg-failed" /> last step failed
          </span>
          <span className="legend-item">
            <i className="lg-off" /> disabled
          </span>
          <span className="legend-item">
            <i className="lg-spark" /> memories
          </span>
          <span className="legend-item">
            <i className="lg-link" /> shared runs
          </span>
        </div>
      </Card>

      <Card title="Strongest connections">
        {strongest.length ? (
          <ol className="synapses">
            {strongest.map((l) => {
              const ab = l.handoffs[l.source] ?? 0;
              const ba = l.handoffs[l.target] ?? 0;
              return (
                <li key={`${l.source}-${l.target}`}>
                  <span className="synapse-pair">
                    <Link to={`/agents/${l.source}`}>{names.get(l.source) ?? l.source}</Link>
                    <span className="synapse-arrow" aria-hidden>
                      {ab && !ba ? "→" : ba && !ab ? "←" : "↔"}
                    </span>
                    <Link to={`/agents/${l.target}`}>{names.get(l.target) ?? l.target}</Link>
                  </span>
                  <span className="synapse-bar">
                    <span style={{ width: `${(l.runs / strongest[0].runs) * 100}%` }} />
                  </span>
                  <span className="small muted">
                    {l.runs} shared {l.runs === 1 ? "run" : "runs"}
                    {ab + ba > 0 && ` · ${ab + ba} handoffs`}
                  </span>
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="muted">No connections yet. Run a workflow with two or more agents and they will wire together here.</p>
        )}
      </Card>
    </>
  );
}
