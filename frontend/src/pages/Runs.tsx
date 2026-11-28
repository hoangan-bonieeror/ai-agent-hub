import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { RunsTable } from "../components/RunsTable";
import { Card, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";

const PAGE = 25;
const STATUSES = ["running", "pending", "succeeded", "failed", "cancelled"];

export function RunsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const workflow = params.get("workflow") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const runs = useApi(() => api.listRuns({ status, workflow, limit: PAGE, offset }), [status, workflow, offset], 5000);
  const workflows = useApi(() => api.listWorkflows(), []);
  const names = [...new Set((workflows.data?.items ?? []).map((w) => w.name))];

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "offset") next.delete("offset");
    setParams(next);
  };
  const items = runs.data?.items ?? [];

  return (
    <>
      <PageHeader title="Runs" subtitle="Every workflow run, newest first. Refreshes every few seconds." />
      <div className="toolbar">
        <select className="input" value={workflow} onChange={(e) => update("workflow", e.target.value)}>
          <option value="">All workflows</option>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <select className="input" value={status} onChange={(e) => update("status", e.target.value)}>
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      <ErrorBox error={runs.error} onRetry={runs.reload} />
      <Card>
        {runs.loading && !runs.data ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty title="No runs match">Start one from a workflow's page.</Empty>
        ) : (
          <>
            <RunsTable runs={items} />
            <div className="pager">
              <button className="btn btn-small" disabled={offset === 0} onClick={() => update("offset", String(Math.max(0, offset - PAGE)))}>
                ← Newer
              </button>
              <span className="muted small">
                {offset + 1}–{offset + items.length}
              </span>
              <button className="btn btn-small" disabled={items.length < PAGE} onClick={() => update("offset", String(offset + PAGE))}>
                Older →
              </button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
