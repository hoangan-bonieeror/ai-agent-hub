import { Link } from "react-router-dom";
import type { RunSummary } from "../api/types";
import { between, duration, money, shortId, timeAgo } from "../lib/format";
import { StatusBadge } from "./ui";

export function RunsTable({ runs, showWorkflow = true }: { runs: RunSummary[]; showWorkflow?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Run</th>
            {showWorkflow && <th>Workflow</th>}
            <th>Status</th>
            <th>Started</th>
            <th>Duration</th>
            <th className="right">Cost</th>
            <th>By</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td>
                <Link to={`/runs/${r.run_id}`} className="mono">
                  {shortId(r.run_id)}
                </Link>
              </td>
              {showWorkflow && (
                <td>
                  <Link to={`/workflows/${r.workflow.id}`}>{r.workflow.name}</Link>{" "}
                  <span className="muted small">v{r.workflow.version}</span>
                </td>
              )}
              <td>
                <StatusBadge status={r.status} />
                {r.error && <div className="small error-text clip" title={r.error}>{r.error}</div>}
              </td>
              <td title={r.started_at ?? r.created_at}>{timeAgo(r.started_at ?? r.created_at)}</td>
              <td>{duration(between(r.started_at, r.completed_at))}</td>
              <td className="right">{money(r.cost_usd)}</td>
              <td className="muted">{r.requested_by ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
