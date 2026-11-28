import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AuditEntry, BudgetRules, Policy, PolicyCheckResult, PolicyKind, PolicyScope } from "../api/types";
import { Card, Empty, ErrorBox, Loading, PageHeader, Tags } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { dateTime, money, splitList, timeAgo } from "../lib/format";

const KINDS: { kind: PolicyKind; title: string; help: string }[] = [
  { kind: "budget", title: "Budgets", help: "Stop agent calls (or only warn) once spend in a period reaches a limit." },
  { kind: "tools", title: "Blocked tools", help: "Tools agents may not use, even when their definition lists them." },
  { kind: "models", title: "Allowed models", help: "Agents may only call these models; calls to any other model are refused." },
];
const PERIOD_LABEL = { day: "per day", month: "per month", total: "in total" } as const;

function scopeLabel(p: { scope: PolicyScope; target: string | null }) {
  if (p.scope === "global") return "All agents";
  if (p.scope === "agent") return <>Agent <Link to={`/agents/${p.target}`}>{p.target}</Link></>;
  return <>Workflow {p.target}</>;
}

export function PoliciesPage() {
  const policies = useApi(() => api.listPolicies(), [], 15000);
  const audit = useApi(() => api.auditLogs({ resource_type: "policy", limit: 15 }), []);
  const [creating, setCreating] = useState<PolicyKind | null>(null);
  const reload = () => {
    policies.reload();
    audit.reload();
  };
  const items = policies.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Policies"
        subtitle="Checked before every agent call, in workflows and direct runs. Without a policy, nothing is limited."
        actions={
          !creating && (
            <button className="btn btn-primary" onClick={() => setCreating("budget")}>
              New policy
            </button>
          )
        }
      />
      {creating && (
        <PolicyForm
          initialKind={creating}
          onDone={(saved) => {
            setCreating(null);
            if (saved) reload();
          }}
        />
      )}
      <ErrorBox error={policies.error} onRetry={policies.reload} />
      {policies.loading && !policies.data ? (
        <Loading />
      ) : (
        KINDS.map(({ kind, title, help }) => {
          const group = items.filter((p) => p.kind === kind);
          return (
            <Card
              key={kind}
              title={title}
              actions={
                <button className="btn btn-small" onClick={() => setCreating(kind)}>
                  Add
                </button>
              }
            >
              <p className="muted small">{help}</p>
              {group.length === 0 ? (
                <p className="muted">None.</p>
              ) : (
                <ul className="policies">
                  {group.map((p) => (
                    <PolicyRow key={p.id} policy={p} onChange={reload} />
                  ))}
                </ul>
              )}
            </Card>
          );
        })
      )}
      <div className="grid-2">
        <CheckAgent />
        <Card title="Recent changes">
          {audit.error ? <ErrorBox error={audit.error} /> : !audit.data ? <Loading /> : <AuditList items={audit.data.items} />}
        </Card>
      </div>
    </>
  );
}

function PolicyRow({ policy: p, onChange }: { policy: Policy; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const rules = p.rules as Record<string, unknown>;
  const budget = p.kind === "budget" ? (rules as unknown as BudgetRules) : null;

  return (
    <li className={`policy ${p.enabled ? "" : "policy-off"}`}>
      <div className="policy-head">
        <strong>{p.name}</strong>
        <span className="muted small">{scopeLabel(p)}</span>
        {!p.enabled && <span className="badge badge-disabled">off</span>}
        {budget && budget.action === "warn" && <span className="tag">warn only</span>}
        <span className="memory-actions">
          <button className="btn btn-ghost btn-small" disabled={busy} onClick={() => act(() => api.updatePolicy(p.id, { enabled: !p.enabled }))}>
            {p.enabled ? "Turn off" : "Turn on"}
          </button>
          <button
            className="btn btn-ghost btn-small danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Delete policy ${p.name}?`)) void act(() => api.deletePolicy(p.id));
            }}
          >
            Delete
          </button>
        </span>
      </div>
      {p.description && <p className="small muted">{p.description}</p>}
      {budget && p.status && <BudgetBar budget={budget} status={p.status} />}
      {p.kind === "tools" && <Tags items={(rules.deny as string[]) ?? []} />}
      {p.kind === "models" && <Tags items={(rules.allow as string[]) ?? []} />}
      <ErrorBox error={error} />
    </li>
  );
}

function BudgetBar({ budget, status }: { budget: BudgetRules; status: NonNullable<Policy["status"]> }) {
  const pct = Math.min(100, status.used * 100);
  const level = status.reached ? "bad" : status.used >= 0.8 ? "warn" : "good";
  return (
    <div className="budget">
      <div className="budget-line">
        <span>
          <strong>{money(status.spent_usd)}</strong> of {money(budget.limit_usd)} {PERIOD_LABEL[budget.period]}
        </span>
        <span className={`small ${level === "bad" ? "error-text" : level === "warn" ? "warn-text" : "muted"}`}>
          {status.reached
            ? budget.action === "block"
              ? "Reached: agent calls are refused"
              : "Reached: calls run with a warning"
            : `${Math.round(status.used * 100)}% used`}
        </span>
      </div>
      <div className={`meter meter-${level}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <span style={{ width: `${pct}%` }} />
      </div>
      {status.since && <div className="small muted">since {dateTime(status.since)}</div>}
    </div>
  );
}

function PolicyForm({ initialKind, onDone }: { initialKind: PolicyKind; onDone: (saved: boolean) => void }) {
  const agents = useApi(() => api.listAgents(), []);
  const workflows = useApi(() => api.listWorkflows(), []);
  const tools = useApi(() => api.listTools(), []);
  const [kind, setKind] = useState<PolicyKind>(initialKind);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState<PolicyScope>("global");
  const [target, setTarget] = useState("");
  const [limit, setLimit] = useState("10");
  const [period, setPeriod] = useState<BudgetRules["period"]>("month");
  const [action, setAction] = useState<BudgetRules["action"]>("block");
  const [patterns, setPatterns] = useState("");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  const workflowNames = [...new Set((workflows.data?.items ?? []).map((w) => w.name))].sort();
  const toolNames = (tools.data?.items ?? []).map((t) => t.name);
  const models = [...new Set((agents.data?.items ?? []).map((a) => a.model).filter(Boolean))].sort();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(undefined);
    const rules =
      kind === "budget"
        ? { limit_usd: Number(limit), period, action }
        : kind === "tools"
          ? { deny: splitList(patterns) }
          : { allow: splitList(patterns) };
    try {
      await api.createPolicy({
        name: name.trim(),
        description: description.trim() || null,
        kind,
        scope,
        target: scope === "global" ? null : target,
        rules,
      });
      onDone(true);
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  };

  const suggestions = kind === "tools" ? toolNames : kind === "models" ? models : [];
  const addPattern = (p: string) => {
    const current = splitList(patterns);
    if (!current.includes(p)) setPatterns([...current, p].join(", "));
  };

  return (
    <Card title="New policy">
      <form className="form-tight" onSubmit={submit}>
        <div className="tabs" role="tablist">
          {KINDS.map((k) => (
            <button type="button" key={k.kind} role="tab" aria-selected={kind === k.kind} className={kind === k.kind ? "active" : ""} onClick={() => setKind(k.kind)}>
              {k.title.replace(/s$/, "")}
            </button>
          ))}
        </div>
        <p className="muted small">{KINDS.find((k) => k.kind === kind)?.help}</p>
        <div className="grid-3">
          <label className="field">
            <span>Name</span>
            <input className="input" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === "budget" ? "monthly-cap" : kind === "tools" ? "no-web" : "cheap-models"} />
          </label>
          <label className="field">
            <span>Applies to</span>
            <select
              className="input"
              value={scope}
              onChange={(e) => {
                setScope(e.target.value as PolicyScope);
                setTarget("");
              }}
            >
              <option value="global">All agents</option>
              <option value="agent">One agent</option>
              <option value="workflow">Agents in one workflow</option>
            </select>
          </label>
          {scope !== "global" && (
            <label className="field">
              <span>{scope === "agent" ? "Agent" : "Workflow"}</span>
              <select className="input" required value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Choose…</option>
                {(scope === "agent" ? (agents.data?.items ?? []).map((a) => a.slug) : workflowNames).map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {kind === "budget" ? (
          <div className="grid-3">
            <label className="field">
              <span>Limit (US $)</span>
              <input className="input" required type="number" min="0.01" step="0.01" value={limit} onChange={(e) => setLimit(e.target.value)} />
            </label>
            <label className="field">
              <span>Period</span>
              <select className="input" value={period} onChange={(e) => setPeriod(e.target.value as BudgetRules["period"])}>
                <option value="day">Per day (from 00:00 UTC)</option>
                <option value="month">Per month (from the 1st, UTC)</option>
                <option value="total">In total</option>
              </select>
            </label>
            <label className="field">
              <span>When reached</span>
              <select className="input" value={action} onChange={(e) => setAction(e.target.value as BudgetRules["action"])}>
                <option value="block">Refuse agent calls</option>
                <option value="warn">Only warn</option>
              </select>
            </label>
          </div>
        ) : (
          <label className="field">
            <span>{kind === "tools" ? "Tools to block" : "Models to allow"} (comma separated, * matches anything)</span>
            <input
              className="input mono"
              required
              value={patterns}
              onChange={(e) => setPatterns(e.target.value)}
              placeholder={kind === "tools" ? "web_search, mcp:files/write_*" : "gpt-4.1-mini*, gpt-4.1-nano*"}
            />
            {suggestions.length > 0 && (
              <span className="chips">
                {suggestions.slice(0, 12).map((s) => (
                  <button type="button" key={s} className="chip" onClick={() => addPattern(s)}>
                    + {s}
                  </button>
                ))}
              </span>
            )}
          </label>
        )}
        <label className="field">
          <span>Note (optional)</span>
          <input className="input" maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Why this policy exists" />
        </label>
        <ErrorBox error={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => onDone(false)}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save policy"}
          </button>
        </div>
      </form>
    </Card>
  );
}

function CheckAgent() {
  const agents = useApi(() => api.listAgents(), []);
  const workflows = useApi(() => api.listWorkflows(), []);
  const [agent, setAgent] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [result, setResult] = useState<PolicyCheckResult>();
  const [error, setError] = useState<string>();
  const workflowNames = [...new Set((workflows.data?.items ?? []).map((w) => w.name))].sort();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(undefined);
    setResult(undefined);
    try {
      setResult(await api.checkPolicies(agent, workflow || undefined));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <Card title="Check an agent">
      <p className="muted small">What the policies would do to the agent's next call. Nothing runs.</p>
      <form className="toolbar" onSubmit={submit}>
        <select className="input" required value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
          <option value="">Agent…</option>
          {(agents.data?.items ?? []).map((a) => (
            <option key={a.slug} value={a.slug}>
              {a.name}
            </option>
          ))}
        </select>
        <select className="input" value={workflow} onChange={(e) => setWorkflow(e.target.value)} aria-label="Workflow">
          <option value="">Direct run</option>
          {workflowNames.map((w) => (
            <option key={w} value={w}>
              in {w}
            </option>
          ))}
        </select>
        <button className="btn">Check</button>
      </form>
      <ErrorBox error={error} />
      {result &&
        (result.allowed ? (
          <dl className="props">
            <dt>Result</dt>
            <dd className="ok-text">Allowed{result.warnings.length > 0 && <span className="warn-text"> with warnings</span>}</dd>
            <dt>Policies</dt>
            <dd>
              <Tags items={result.applied} empty="None apply" />
            </dd>
            <dt>Tools it keeps</dt>
            <dd>
              <Tags items={result.tools} empty="None" />
            </dd>
            {result.removed_tools.length > 0 && (
              <>
                <dt>Tools removed</dt>
                <dd>
                  <Tags items={result.removed_tools} />
                </dd>
              </>
            )}
            {result.warnings.map((w) => (
              <dd key={w} className="warn-text">
                {w}
              </dd>
            ))}
          </dl>
        ) : (
          <div className="error-box">{result.reason}</div>
        ))}
    </Card>
  );
}

function AuditList({ items }: { items: AuditEntry[] }) {
  if (!items.length) return <Empty title="No changes yet" />;
  return (
    <ul className="audit">
      {items.map((e) => (
        <li key={e.id}>
          <span className="mono small">{e.action.replace("policy.", "")}</span> <strong>{String(e.metadata.name ?? "")}</strong>{" "}
          <span className="muted small" title={dateTime(e.timestamp)}>
            by {e.actor} · {timeAgo(e.timestamp)}
          </span>
        </li>
      ))}
    </ul>
  );
}
