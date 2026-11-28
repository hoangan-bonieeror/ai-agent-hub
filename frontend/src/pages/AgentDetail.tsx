import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { AgentResult } from "../api/types";
import { Card, ErrorBox, JsonBlock, Loading, PageHeader, StatusBadge, Tags } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { dateTime, duration, number, timeAgo } from "../lib/format";

export function AgentDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const agent = useApi(() => api.getAgent(id), [id]);
  const versions = useApi(() => api.agentVersions(id), [id]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();

  if (agent.loading && !agent.data) return <Loading />;
  if (agent.error || !agent.data) return <ErrorBox error={agent.error ?? "Agent not found"} onRetry={agent.reload} />;
  const a = agent.data;
  const archived = a.status === "archived";

  const act = async (fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setActionError(undefined);
    try {
      await fn();
      after ? after() : (agent.reload(), versions.reload());
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={
          <>
            {a.name} <StatusBadge status={a.status} />
          </>
        }
        subtitle={a.description ?? <span className="mono">{a.slug}</span>}
        actions={
          !archived && (
            <>
              <button
                className="btn"
                disabled={busy}
                onClick={() => act(() => api.updateAgent(a.slug, { status: a.status === "active" ? "disabled" : "active" }))}
              >
                {a.status === "active" ? "Disable" : "Enable"}
              </button>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(`Archive ${a.name}? It disappears from lists; its history is kept and the slug stays reserved.`)) {
                    void act(() => api.archiveAgent(a.slug), () => navigate("/agents"));
                  }
                }}
              >
                Archive
              </button>
              <Link className="btn btn-primary" to={`/agents/${a.slug}/edit`}>
                Edit
              </Link>
            </>
          )
        }
      />
      <ErrorBox error={actionError} />
      <div className="grid-main">
        <div className="stack">
          <Card title="Definition">
            <dl className="props">
              <dt>Slug</dt>
              <dd className="mono">{a.slug}</dd>
              <dt>Runtime</dt>
              <dd>{a.runtime}</dd>
              <dt>Model</dt>
              <dd>{a.model}</dd>
              <dt>Capabilities</dt>
              <dd>
                <Tags items={a.capabilities} />
              </dd>
              <dt>Tools</dt>
              <dd>
                <Tags items={a.tools} empty="None (no tool calls allowed)" />
              </dd>
              {a.tools.includes("memory") && (
                <>
                  <dt>Memory</dt>
                  <dd>
                    <Link to={`/memory?agent=${a.slug}`}>See what it can recall →</Link>
                  </dd>
                </>
              )}
              <dt>Limits</dt>
              <dd>
                {a.limits.max_steps} steps, {a.limits.timeout_seconds} s timeout
              </dd>
              <dt>Version</dt>
              <dd>
                v{a.current_version} · updated {timeAgo(a.updated_at)}
              </dd>
            </dl>
            <h3>Instructions</h3>
            {a.instructions ? <pre className="prose-block">{a.instructions}</pre> : <p className="muted">No instructions.</p>}
          </Card>
          {!archived && <TryAgent slug={a.slug} disabled={a.status !== "active"} />}
        </div>
        <Card title="Versions">
          {versions.error ? (
            <ErrorBox error={versions.error} />
          ) : !versions.data ? (
            <Loading />
          ) : (
            <ul className="versions">
              {versions.data.map((v) => (
                <li key={v.version}>
                  <details>
                    <summary>
                      <strong>v{v.version}</strong> <span className="muted small">{dateTime(v.created_at)}</span>
                      {v.created_by && <span className="muted small"> · {v.created_by}</span>}
                    </summary>
                    <JsonBlock value={v.config_snapshot} maxHeight={300} />
                  </details>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function TryAgent({ slug, disabled }: { slug: string; disabled: boolean }) {
  const [task, setTask] = useState("");
  const [input, setInput] = useState("{}");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AgentResult>();
  const [error, setError] = useState<string>();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let parsed: Record<string, unknown>;
    try {
      parsed = input.trim() ? JSON.parse(input) : {};
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error();
    } catch {
      setError("Extra input must be a JSON object, for example {\"topic\": \"AI\"}.");
      return;
    }
    setRunning(true);
    setError(undefined);
    setResult(undefined);
    try {
      setResult(await api.runAgent(slug, task, parsed));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Card title="Try it">
      {disabled && <p className="muted small">Enable the agent to run it.</p>}
      <form onSubmit={submit} className="form-tight">
        <label className="field">
          <span>Task</span>
          <textarea className="input" rows={3} required value={task} onChange={(e) => setTask(e.target.value)} placeholder="Summarise the latest trends in AI agents." />
        </label>
        <label className="field">
          <span>Extra input (JSON)</span>
          <textarea className="input mono" rows={2} value={input} onChange={(e) => setInput(e.target.value)} />
        </label>
        <div className="form-actions">
          <button className="btn btn-primary" disabled={running || disabled}>
            {running ? "Running…" : "Run agent"}
          </button>
        </div>
      </form>
      <ErrorBox error={error} />
      {result && (
        <div className="result">
          <div className="result-meta">
            <StatusBadge status={result.status === "completed" ? "succeeded" : result.status} />
            <span>{duration(result.duration_ms)}</span>
            <span>{number(result.usage.total_tokens)} tokens</span>
          </div>
          {result.error && <div className="error-text">{result.error}</div>}
          <JsonBlock value={result.output} />
        </div>
      )}
    </Card>
  );
}
