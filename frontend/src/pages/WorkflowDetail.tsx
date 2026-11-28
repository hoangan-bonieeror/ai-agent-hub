import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { DagView } from "../components/DagView";
import { RunsTable } from "../components/RunsTable";
import { Card, Empty, ErrorBox, JsonBlock, Loading, PageHeader, StatusBadge } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { dependenciesOf, inputsOf } from "../lib/graph";

export function WorkflowDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const workflow = useApi(() => api.getWorkflow(id), [id]);
  const name = workflow.data?.name;
  const siblings = useApi(() => (name ? api.listWorkflows() : Promise.resolve(undefined)), [name]);
  const runs = useApi(() => (name ? api.listRuns({ workflow: name, limit: 10 }) : Promise.resolve(undefined)), [name], 5000);
  const [selected, setSelected] = useState<string | null>(null);

  if (workflow.loading && !workflow.data) return <Loading />;
  if (workflow.error || !workflow.data) return <ErrorBox error={workflow.error ?? "Workflow not found"} onRetry={workflow.reload} />;
  const w = workflow.data;
  const steps = w.definition.steps;
  const versions = (siblings.data?.items ?? []).filter((v) => v.name === w.name).sort((a, b) => b.version - a.version);
  const latest = versions[0];
  const index = selected ? steps.findIndex((s) => s.id === selected) : -1;
  const step = index >= 0 ? steps[index] : undefined;

  return (
    <>
      <PageHeader
        title={
          <>
            {w.name} <span className="muted">v{w.version}</span> {!w.enabled && <StatusBadge status="disabled" />}
          </>
        }
        subtitle={w.description || `${steps.length} steps`}
        actions={
          <>
            {versions.length > 1 && (
              <select className="input" value={w.id} onChange={(e) => navigate(`/workflows/${e.target.value}`)} aria-label="Version">
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version}
                    {v.id === latest?.id ? " (latest)" : ""}
                  </option>
                ))}
              </select>
            )}
            <Link className="btn" to={`/workflows/${w.id}/edit`}>
              Edit as new version
            </Link>
          </>
        }
      />
      {latest && latest.id !== w.id && (
        <div className="notice">
          You are viewing v{w.version}. Running by name uses the newest enabled version; this page runs exactly v{w.version}.{" "}
          <Link to={`/workflows/${latest.id}`}>Go to v{latest.version}</Link>
        </div>
      )}
      <Card title="Graph" actions={<span className="muted small">Click a step for details</span>}>
        <DagView steps={steps} selected={selected} onSelect={(s) => setSelected(s === selected ? null : s)} />
        {step && (
          <div className="step-detail">
            <h3>
              {step.id} <span className="tag">{step.kind ?? "agent"}</span>
            </h3>
            <dl className="props">
              {step.agent_id && (
                <>
                  <dt>Agent</dt>
                  <dd>
                    <Link to={`/agents/${step.agent_id}`}>{step.agent_id}</Link>
                  </dd>
                </>
              )}
              <dt>Depends on</dt>
              <dd>{dependenciesOf(steps, index).join(", ") || "nothing (root step)"}</dd>
              <dt>Reads outputs of</dt>
              <dd>{inputsOf(steps, index).join(", ") || "—"}</dd>
              {step.kind === "review" && (
                <>
                  <dt>Revisions</dt>
                  <dd>
                    up to {step.max_revisions ?? 0}
                    {step.require_approval ? ", approval required" : ""}
                  </dd>
                </>
              )}
            </dl>
            {step.task && <pre className="prose-block">{step.task}</pre>}
          </div>
        )}
      </Card>
      <div className="grid-main">
        <RunForm workflowId={w.id} disabled={!w.enabled} />
        <Card title="Definition">
          <JsonBlock value={w.definition} maxHeight={360} />
        </Card>
      </div>
      <Card title="Recent runs" actions={<Link to={`/runs?workflow=${encodeURIComponent(w.name)}`}>All →</Link>}>
        {runs.data?.items.length ? <RunsTable runs={runs.data.items} showWorkflow={false} /> : <Empty title="No runs yet" />}
      </Card>
    </>
  );
}

function RunForm({ workflowId, disabled }: { workflowId: string; disabled: boolean }) {
  const navigate = useNavigate();
  const [input, setInput] = useState('{\n  "topic": "AI Agent Hub"\n}');
  const [error, setError] = useState<string>();
  const [starting, setStarting] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let parsed: Record<string, unknown>;
    try {
      parsed = input.trim() ? JSON.parse(input) : {};
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error();
    } catch {
      setError("Input must be a JSON object.");
      return;
    }
    setStarting(true);
    setError(undefined);
    try {
      const run = await api.startRun(workflowId, parsed);
      navigate(`/runs/${run.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setStarting(false);
    }
  };

  return (
    <Card title="Run">
      <form onSubmit={submit} className="form-tight">
        <label className="field">
          <span>Input (JSON)</span>
          <textarea className="input mono" rows={6} value={input} onChange={(e) => setInput(e.target.value)} />
          <small className="muted">Every step receives this as its workflow input. The run starts in the background and you can follow it live.</small>
        </label>
        <ErrorBox error={error} />
        <div className="form-actions">
          <button className="btn btn-primary" disabled={starting || disabled}>
            {starting ? "Starting…" : "Run workflow"}
          </button>
        </div>
      </form>
    </Card>
  );
}
