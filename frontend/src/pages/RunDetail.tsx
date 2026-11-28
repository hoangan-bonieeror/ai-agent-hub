import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Execution, HubEvent, RunDetail, RunTask, WorkflowStep } from "../api/types";
import { DagView } from "../components/DagView";
import { Card, ErrorBox, JsonBlock, Loading, PageHeader, Stat, StatusBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useRunEvents } from "../hooks/useRunEvents";
import { between, dateTime, duration, money, number, shortId } from "../lib/format";

const FINISHED = new Set(["succeeded", "failed", "cancelled"]);

export function RunDetailPage() {
  const { id = "" } = useParams();
  const run = useApi(() => api.getRun(id), [id]);
  const [selected, setSelected] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<string>();
  const [cancelling, setCancelling] = useState(false);
  const now = useNow(run.data && !FINISHED.has(run.data.status));

  // Refetch the run (outputs, executions) on every event, at most every 300 ms.
  const pending = useRef<number | undefined>(undefined);
  const refresh = () => {
    window.clearTimeout(pending.current);
    pending.current = window.setTimeout(run.reload, 300);
  };
  useEffect(() => () => window.clearTimeout(pending.current), []);

  const live = Boolean(run.data && !FINISHED.has(run.data.status));
  const stream = useRunEvents(id, live, () => refresh());

  // Fallback when the stream is unavailable (e.g. the run is on another server).
  useEffect(() => {
    if (!live || stream.state !== "error") return;
    const timer = window.setInterval(run.reload, 3000);
    return () => window.clearInterval(timer);
  }, [live, stream.state, run.reload]);

  const steps: WorkflowStep[] = useMemo(
    () =>
      (run.data?.tasks ?? []).map((t) => ({
        id: t.step_id,
        kind: t.kind,
        agent_id: t.agent_id,
        depends_on: t.depends_on,
      })),
    [run.data],
  );
  const statuses = useMemo(
    () => Object.fromEntries((run.data?.tasks ?? []).map((t) => [t.step_id, t.status])),
    [run.data],
  );

  if (run.loading && !run.data) return <Loading />;
  if (run.error && !run.data) return <ErrorBox error={run.error} onRetry={run.reload} />;
  const r = run.data as RunDetail;
  const tasks = r.tasks;
  const done = tasks.filter((t) => FINISHED.has(t.status) || t.status === "skipped").length;
  const task = tasks.find((t) => t.step_id === selected);

  const cancel = async () => {
    setCancelling(true);
    setCancelError(undefined);
    try {
      await api.cancelRun(r.run_id);
      run.reload();
    } catch (e) {
      setCancelError((e as Error).message);
    } finally {
      setCancelling(false);
    }
  };

  return (
    <>
      <PageHeader
        title={
          <>
            Run <span className="mono">{shortId(r.run_id)}</span> <StatusBadge status={r.status} />
          </>
        }
        subtitle={
          <>
            <Link to={`/workflows/${r.workflow.id}`}>{r.workflow.name}</Link> v{r.workflow.version} · started {dateTime(r.started_at ?? r.created_at)}
            {r.requested_by && <> · by {r.requested_by}</>}
          </>
        }
        actions={
          live && (
            <button className="btn btn-danger" disabled={cancelling} onClick={cancel}>
              {cancelling ? "Cancelling…" : "Cancel run"}
            </button>
          )
        }
      />
      <ErrorBox error={cancelError} />
      {r.error && <div className="error-box">{r.error}</div>}

      <div className="stats">
        <Stat label="Duration" value={duration(between(r.started_at, r.completed_at, now))} hint={live ? "running" : r.completed_at ? `ended ${dateTime(r.completed_at)}` : undefined} />
        <Stat label="Steps finished" value={`${done} / ${tasks.length}`} />
        <Stat label="Tokens" value={number(r.usage.total_tokens)} hint={`${number(r.usage.input_tokens)} in · ${number(r.usage.output_tokens)} out`} />
        <Stat label="Model calls" value={number(r.usage.requests)} />
        <Stat
          label="Cost"
          value={money(r.cost_usd)}
          hint={r.unpriced_executions ? <Link to="/costs">{r.unpriced_executions} calls without a price</Link> : "from model prices"}
        />
      </div>

      <Card
        title="Progress"
        actions={
          live ? (
            <span className={`live live-${stream.state}`}>{stream.state === "live" ? "● Live" : stream.state === "error" ? "Polling" : "Connecting…"}</span>
          ) : undefined
        }
      >
        <DagView steps={steps} statuses={statuses} selected={selected} onSelect={(s) => setSelected(s === selected ? null : s)} />
        <div className="legend">
          {["pending", "queued", "running", "succeeded", "failed", "skipped", "cancelled"].map((s) => (
            <span key={s} className={`legend-item st-${s}`}>
              <i /> {s}
            </span>
          ))}
        </div>
        {task ? <TaskPanel task={task} /> : <p className="muted small">Click a step to see its output, attempts and logs.</p>}
      </Card>

      <div className="grid-2">
        <Card title="Output">
          <JsonBlock value={r.output} empty={live ? "Not finished yet" : "No output"} maxHeight={420} />
        </Card>
        <Card title="Input">
          <JsonBlock value={r.input} empty="No input" maxHeight={420} />
        </Card>
      </div>

      {stream.events.length > 0 && <EventLog events={stream.events} />}
    </>
  );
}

function TaskPanel({ task }: { task: RunTask }) {
  const [execution, setExecution] = useState<Execution>();
  const [execError, setExecError] = useState<string>();
  useEffect(() => {
    setExecution(undefined);
    setExecError(undefined);
  }, [task.task_id]);

  return (
    <div className="step-detail">
      <h3>
        {task.step_id} <span className="tag">{task.kind}</span> <StatusBadge status={task.status} />
      </h3>
      <dl className="props">
        {task.agent_id && (
          <>
            <dt>Agent</dt>
            <dd>
              <Link to={`/agents/${task.agent_id}`}>{task.agent_id}</Link>
            </dd>
          </>
        )}
        <dt>Depends on</dt>
        <dd>{task.depends_on.join(", ") || "nothing"}</dd>
        {task.retry_count > 0 && (
          <>
            <dt>Revisions</dt>
            <dd>{task.retry_count}</dd>
          </>
        )}
      </dl>
      {task.error && <div className="error-box">{task.error}</div>}
      {task.executions.length > 0 && (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Attempt</th>
                <th>Status</th>
                <th>Duration</th>
                <th>Tokens</th>
                <th>Model</th>
                <th className="right">Cost</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {task.executions.map((e, i) => (
                <tr key={e.execution_id}>
                  <td>#{i + 1}</td>
                  <td>
                    <StatusBadge status={e.status} />
                  </td>
                  <td>{duration(e.duration_ms)}</td>
                  <td>{number(e.token_usage?.total_tokens ?? null)}</td>
                  <td className="mono small">{e.model ?? "—"}</td>
                  <td className="right">{e.cost_usd == null ? <span className="muted" title="No price for this model">—</span> : money(e.cost_usd)}</td>
                  <td className="right">
                    <button
                      className="btn btn-small"
                      onClick={() =>
                        api.getExecution(e.execution_id).then(setExecution, (err: Error) => setExecError(err.message))
                      }
                    >
                      Logs
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ErrorBox error={execError} />
      {execution && (
        <details open className="exec-logs">
          <summary>
            Logs of execution <span className="mono">{shortId(execution.execution_id)}</span>
            {execution.agent_version != null && <> (agent v{execution.agent_version})</>}
          </summary>
          <JsonBlock value={execution.logs.length ? execution.logs : null} empty="No log entries" maxHeight={300} />
        </details>
      )}
      <h4>Output</h4>
      <JsonBlock value={task.output} empty={task.status === "running" ? "Running…" : "No output"} maxHeight={360} />
    </div>
  );
}

function EventLog({ events }: { events: HubEvent[] }) {
  return (
    <Card title={`Events (${events.length})`}>
      <ol className="events">
        {[...events].reverse().map((e, i) => (
          <li key={events.length - i}>
            <span className="mono small muted">{e.at ? new Date(e.at).toLocaleTimeString() : ""}</span>
            <span className={`ev ev-${e.type.split(".")[1]}`}>{e.type}</span>
            {e.step_id && <span className="mono">{e.step_id}</span>}
            {e.data && Object.keys(e.data).length > 0 && <span className="small muted clip">{summarise(e.data)}</span>}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function summarise(data: Record<string, unknown>): string {
  const text = JSON.stringify(data);
  return text.length > 140 ? `${text.slice(0, 139)}…` : text;
}

function useNow(ticking: boolean | undefined): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!ticking) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [ticking]);
  return ticking ? now : Date.now();
}
