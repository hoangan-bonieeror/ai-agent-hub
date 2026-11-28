import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Agent, AgentInput } from "../api/types";
import { Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { splitList } from "../lib/format";

interface FormState {
  slug: string;
  name: string;
  description: string;
  runtime: string;
  model: string;
  instructions: string;
  tools: string;
  capabilities: string;
  max_steps: number;
  timeout_seconds: number;
  status: "active" | "disabled";
}

const EMPTY: FormState = {
  slug: "",
  name: "",
  description: "",
  runtime: "openai_agents",
  model: "gpt-4.1-mini",
  instructions: "",
  tools: "",
  capabilities: "",
  max_steps: 10,
  timeout_seconds: 120,
  status: "active",
};

function fromAgent(a: Agent): FormState {
  return {
    slug: a.slug,
    name: a.name,
    description: a.description ?? "",
    runtime: a.runtime,
    model: a.model,
    instructions: a.instructions,
    tools: a.tools.join(", "),
    capabilities: a.capabilities.join(", "),
    max_steps: a.limits.max_steps,
    timeout_seconds: a.limits.timeout_seconds,
    status: a.status === "disabled" ? "disabled" : "active",
  };
}

function toInput(f: FormState): AgentInput {
  return {
    name: f.name.trim(),
    description: f.description.trim() || null,
    runtime: f.runtime.trim(),
    model: f.model.trim(),
    instructions: f.instructions,
    tools: splitList(f.tools),
    capabilities: splitList(f.capabilities),
    limits: { max_steps: Number(f.max_steps), timeout_seconds: Number(f.timeout_seconds) },
    status: f.status,
  };
}

/** Only the fields that differ, so an unchanged definition keeps its version. */
function changes(before: AgentInput, after: AgentInput): Partial<AgentInput> {
  const out: Partial<AgentInput> = {};
  (Object.keys(after) as (keyof AgentInput)[]).forEach((k) => {
    if (JSON.stringify(before[k]) !== JSON.stringify(after[k])) (out as Record<string, unknown>)[k] = after[k];
  });
  return out;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);

export function AgentFormPage() {
  const { id } = useParams();
  const editing = Boolean(id);
  const navigate = useNavigate();
  const existing = useApi(() => (id ? api.getAgent(id) : Promise.resolve(undefined)), [id]);
  const runtimes = useApi(() => api.runtimes(), []);
  const registry = useApi(() => api.listTools(), []);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [slugTouched, setSlugTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (existing.data) setForm(fromAgent(existing.data));
  }, [existing.data]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      if (editing && existing.data) {
        const diff = changes(toInput(fromAgent(existing.data)), toInput(form));
        if (Object.keys(diff).length) {
          await api.updateAgent(existing.data.slug, { ...diff, expected_version: existing.data.current_version });
        }
        navigate(`/agents/${existing.data.slug}`);
      } else {
        const created = await api.createAgent({ ...toInput(form), slug: form.slug.trim() });
        navigate(`/agents/${created.slug}`);
      }
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  };

  if (editing && existing.loading && !existing.data) return <Loading page />;
  if (editing && existing.error) return <ErrorBox error={existing.error} onRetry={existing.reload} />;

  const knownRuntimes = runtimes.data?.items ?? [];
  const runtimeUnknown = knownRuntimes.length > 0 && !knownRuntimes.includes(form.runtime.trim());

  return (
    <>
      <PageHeader
        title={editing ? `Edit ${existing.data?.name ?? "agent"}` : "New agent"}
        subtitle={editing ? "Saving a changed definition creates a new version." : "Register a specialist agent the orchestrator can assign steps to."}
      />
      <form onSubmit={submit} className="form">
        <Card title="Identity">
          <div className="grid-2">
            <label className="field">
              <span>Name</span>
              <input
                className="input"
                required
                maxLength={200}
                value={form.name}
                onChange={(e) => {
                  set("name", e.target.value);
                  if (!editing && !slugTouched) set("slug", slugify(e.target.value));
                }}
              />
            </label>
            <label className="field">
              <span>Slug</span>
              <input
                className="input mono"
                required
                disabled={editing}
                pattern="[a-z0-9][a-z0-9\-]{0,62}"
                title="Lowercase letters, digits and dashes"
                value={form.slug}
                onChange={(e) => {
                  setSlugTouched(true);
                  set("slug", e.target.value);
                }}
              />
              <small className="muted">{editing ? "The slug cannot change." : "Workflows refer to the agent by this id."}</small>
            </label>
          </div>
          <label className="field">
            <span>Description</span>
            <input className="input" maxLength={2000} value={form.description} onChange={(e) => set("description", e.target.value)} />
          </label>
        </Card>

        <Card title="Model">
          <div className="grid-3">
            <label className="field">
              <span>Runtime</span>
              <input className="input" list="runtimes" required value={form.runtime} onChange={(e) => set("runtime", e.target.value)} />
              <datalist id="runtimes">
                {knownRuntimes.map((r) => (
                  <option key={r} value={r} />
                ))}
              </datalist>
              {runtimeUnknown && <small className="warn-text">This server does not serve “{form.runtime}”. Available: {knownRuntimes.join(", ")}.</small>}
            </label>
            <label className="field">
              <span>Model</span>
              <input className="input" required value={form.model} onChange={(e) => set("model", e.target.value)} />
            </label>
            <label className="field">
              <span>Status</span>
              <select className="input" value={form.status} onChange={(e) => set("status", e.target.value as FormState["status"])}>
                <option value="active">Active</option>
                <option value="disabled">Disabled</option>
              </select>
            </label>
          </div>
          <label className="field">
            <span>Instructions</span>
            <textarea className="input" rows={8} value={form.instructions} onChange={(e) => set("instructions", e.target.value)} placeholder="You are a research agent. …" />
          </label>
        </Card>

        <Card title="Tools and limits">
          <div className="grid-2">
            <label className="field">
              <span>Tools</span>
              <input className="input" value={form.tools} onChange={(e) => set("tools", e.target.value)} placeholder="web_search, calculator" />
              <small className="muted">Comma separated. Tools not granted here are denied. Click to add:</small>
              <ToolPicker
                tools={(registry.data?.items ?? []).filter((t) => t.enabled).map((t) => t.name)}
                selected={splitList(form.tools)}
                onToggle={(name) => {
                  const current = splitList(form.tools);
                  set("tools", (current.includes(name) ? current.filter((t) => t !== name) : [...current, name]).join(", "));
                }}
              />
            </label>
            <label className="field">
              <span>Capabilities</span>
              <input className="input" value={form.capabilities} onChange={(e) => set("capabilities", e.target.value)} placeholder="research, summarise" />
            </label>
          </div>
          <div className="grid-2">
            <label className="field">
              <span>Max steps</span>
              <input className="input" type="number" min={1} max={100} value={form.max_steps} onChange={(e) => set("max_steps", Number(e.target.value))} />
            </label>
            <label className="field">
              <span>Timeout (seconds)</span>
              <input className="input" type="number" min={1} max={3600} value={form.timeout_seconds} onChange={(e) => set("timeout_seconds", Number(e.target.value))} />
            </label>
          </div>
        </Card>

        <ErrorBox error={error} />
        <div className="form-actions">
          <Link className="btn" to={editing && existing.data ? `/agents/${existing.data.slug}` : "/agents"}>
            Cancel
          </Link>
          <button className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : editing ? "Save changes" : "Create agent"}
          </button>
        </div>
      </form>
    </>
  );
}

function ToolPicker({ tools, selected, onToggle }: { tools: string[]; selected: string[]; onToggle: (name: string) => void }) {
  if (!tools.length) return null;
  return (
    <div className="tool-picker">
      {tools.map((t) => (
        <button key={t} type="button" className={`chip${selected.includes(t) ? " on" : ""}`} onClick={() => onToggle(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}
