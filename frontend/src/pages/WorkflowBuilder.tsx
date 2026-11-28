import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Agent, StepKind, WorkflowDefinition, WorkflowStep } from "../api/types";
import { DagView } from "../components/DagView";
import { Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { validateDefinition, withExplicitDependencies } from "../lib/graph";
import { cleanDefinition, removeStep, renameStep } from "../lib/builder";

const BLANK: WorkflowDefinition = {
  name: "",
  description: "",
  steps: [{ id: "step-1", kind: "agent", agent_id: "", task: "", depends_on: [] }],
};

export function WorkflowBuilderPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const source = useApi(() => (id ? api.getWorkflow(id) : Promise.resolve(undefined)), [id]);
  const agents = useApi(() => api.listAgents({ status: "active" }), []);
  const [def, setDef] = useState<WorkflowDefinition>(BLANK);
  const [tab, setTab] = useState<"graph" | "json">("graph");
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState<string>();
  const [selected, setSelected] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string>();

  useEffect(() => {
    if (source.data) setDef(withExplicitDependencies(source.data.definition));
  }, [source.data]);

  const problems = useMemo(() => validateDefinition(def), [def]);
  const flagged = useMemo(() => new Set(problems.map((p) => p.stepId).filter(Boolean) as string[]), [problems]);

  if (id && source.loading && !source.data) return <Loading />;
  if (id && source.error) return <ErrorBox error={source.error} onRetry={source.reload} />;

  const setStep = (index: number, patch: Partial<WorkflowStep>) =>
    setDef((d) => ({ ...d, steps: d.steps.map((s, i) => (i === index ? { ...s, ...patch } : s)) }));

  const addStep = (kind: StepKind) =>
    setDef((d) => {
      const taken = new Set(d.steps.map((s) => s.id));
      let n = d.steps.length + 1;
      while (taken.has(`${kind === "agent" ? "step" : kind}-${n}`)) n += 1;
      const newId = `${kind === "agent" ? "step" : kind}-${n}`;
      const last = d.steps[d.steps.length - 1];
      const step: WorkflowStep = {
        id: newId,
        kind,
        depends_on: last ? [last.id] : [],
        ...(kind === "aggregate" ? {} : { agent_id: "", task: kind === "review" ? "Check the outputs for gaps and errors." : "" }),
        ...(kind === "review" ? { max_revisions: 1 } : {}),
      };
      setSelected(newId);
      return { ...d, steps: [...d.steps, step] };
    });

  const move = (index: number, delta: number) =>
    setDef((d) => {
      const steps = [...d.steps];
      const target = index + delta;
      if (target < 0 || target >= steps.length) return d;
      [steps[index], steps[target]] = [steps[target], steps[index]];
      return { ...d, steps };
    });

  const openJson = () => {
    setJsonText(JSON.stringify(cleanDefinition(def), null, 2));
    setJsonError(undefined);
    setTab("json");
  };

  const applyJson = () => {
    try {
      const parsed = JSON.parse(jsonText) as WorkflowDefinition;
      if (!parsed || !Array.isArray(parsed.steps)) throw new Error("The definition needs a steps array.");
      setDef(withExplicitDependencies({ ...parsed, name: parsed.name ?? "" }));
      setJsonError(undefined);
      setTab("graph");
    } catch (e) {
      setJsonError((e as Error).message);
    }
  };

  const save = async () => {
    setSaving(true);
    setSaveError(undefined);
    try {
      const saved = await api.createWorkflow(cleanDefinition(def));
      navigate(`/workflows/${saved.id}`);
    } catch (e) {
      setSaveError((e as Error).message);
      setSaving(false);
    }
  };

  const agentList = agents.data?.items ?? [];
  const editingName = source.data?.name;

  return (
    <>
      <PageHeader
        title={id ? `Edit ${editingName}` : "New workflow"}
        subtitle={
          id
            ? `Saving adds a new version of “${editingName}”. Earlier runs keep the version they ran.`
            : "Add steps, connect them with dependencies and watch the graph take shape."
        }
        actions={
          <>
            <Link className="btn" to={id ? `/workflows/${id}` : "/workflows"}>
              Cancel
            </Link>
            <button className="btn btn-primary" disabled={saving || problems.length > 0} onClick={save} title={problems.length ? "Fix the problems first" : undefined}>
              {saving ? "Saving…" : "Save workflow"}
            </button>
          </>
        }
      />
      <ErrorBox error={saveError} />
      {agents.data && agentList.length === 0 && (
        <div className="notice">
          There are no active agents yet. <Link to="/agents/new">Create one</Link> first, or load the samples with <code>python scripts\seed_samples.py --runtime fake</code>.
        </div>
      )}

      <div className="builder">
        <div className="stack">
          <Card title="Workflow">
            <div className="grid-2">
              <label className="field">
                <span>Name</span>
                <input className="input" value={def.name} onChange={(e) => setDef({ ...def, name: e.target.value })} placeholder="research-and-report" />
                {editingName && def.name !== editingName && <small className="warn-text">A new name saves a separate workflow.</small>}
              </label>
              <label className="field">
                <span>Output step</span>
                <select className="input" value={def.output_step ?? ""} onChange={(e) => setDef({ ...def, output_step: e.target.value || null })}>
                  <option value="">Last step</option>
                  {def.steps.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="field">
              <span>Description</span>
              <input className="input" value={def.description ?? ""} onChange={(e) => setDef({ ...def, description: e.target.value })} />
            </label>
            <div className="grid-2">
              <label className="field">
                <span>Max parallel steps</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={32}
                  placeholder="server default (4)"
                  value={def.max_parallel ?? ""}
                  onChange={(e) => setDef({ ...def, max_parallel: e.target.value ? Number(e.target.value) : null })}
                />
              </label>
              <label className="field">
                <span>Run timeout (seconds)</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  placeholder="no limit"
                  value={def.timeout_seconds ?? ""}
                  onChange={(e) => setDef({ ...def, timeout_seconds: e.target.value ? Number(e.target.value) : null })}
                />
              </label>
            </div>
          </Card>

          {def.steps.map((step, i) => (
            <StepEditor
              key={i}
              step={step}
              index={i}
              count={def.steps.length}
              others={def.steps.filter((_, j) => j !== i).map((s) => s.id)}
              agents={agentList}
              selected={selected === step.id}
              flagged={flagged.has(step.id)}
              onFocus={() => setSelected(step.id)}
              onChange={(patch) => setStep(i, patch)}
              onRename={(next) => {
                setDef((d) => renameStep(d, i, next));
                setSelected(next);
              }}
              onMove={(delta) => move(i, delta)}
              onRemove={() => setDef((d) => removeStep(d, i))}
            />
          ))}
          <div className="add-step">
            <span className="muted">Add step:</span>
            <button className="btn" onClick={() => addStep("agent")}>
              + Agent
            </button>
            <button className="btn" onClick={() => addStep("review")}>
              + Review
            </button>
            <button className="btn" onClick={() => addStep("aggregate")}>
              + Aggregate
            </button>
          </div>
        </div>

        <div className="builder-side">
          <Card
            title="Preview"
            actions={
              <div className="tabs">
                <button className={tab === "graph" ? "active" : ""} onClick={() => setTab("graph")}>
                  Graph
                </button>
                <button className={tab === "json" ? "active" : ""} onClick={openJson}>
                  JSON
                </button>
              </div>
            }
          >
            {tab === "graph" ? (
              <DagView fit steps={def.steps} selected={selected} flagged={flagged} onSelect={(s) => {
                setSelected(s);
                document.getElementById(`step-${s}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
              }} />
            ) : (
              <>
                <textarea className="input mono json-edit" rows={18} value={jsonText} onChange={(e) => setJsonText(e.target.value)} spellCheck={false} />
                <ErrorBox error={jsonError} />
                <div className="form-actions">
                  <button className="btn btn-primary" onClick={applyJson}>
                    Apply JSON
                  </button>
                </div>
              </>
            )}
          </Card>
          <Card title={problems.length ? `Problems (${problems.length})` : "Checks"}>
            {problems.length === 0 ? (
              <p className="ok-text">Looks good. The server checks it again when you save.</p>
            ) : (
              <ul className="problems">
                {problems.map((p, i) => (
                  <li key={i}>{p.message}</li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

interface StepEditorProps {
  step: WorkflowStep;
  index: number;
  count: number;
  others: string[];
  agents: Agent[];
  selected: boolean;
  flagged: boolean;
  onFocus: () => void;
  onChange: (patch: Partial<WorkflowStep>) => void;
  onRename: (next: string) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
}

function StepEditor({ step, index, count, others, agents, selected, flagged, onFocus, onChange, onRename, onMove, onRemove }: StepEditorProps) {
  const kind = step.kind ?? "agent";
  const deps = step.depends_on ?? [];
  const customUses = step.uses != null;
  const toggle = (list: string[], item: string) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  const knownAgent = !step.agent_id || agents.some((a) => a.slug === step.agent_id);

  const setKind = (next: StepKind) => {
    const patch: Partial<WorkflowStep> = { kind: next };
    if (next === "aggregate") Object.assign(patch, { agent_id: undefined, task: undefined });
    else if (kind === "aggregate") Object.assign(patch, { agent_id: "", task: "" });
    if (next !== "review") Object.assign(patch, { max_revisions: undefined, require_approval: undefined });
    else Object.assign(patch, { max_revisions: step.max_revisions ?? 1 });
    onChange(patch);
  };

  return (
    <section id={`step-${step.id}`} className={`card step-card kind-${kind}${selected ? " selected" : ""}${flagged ? " flagged" : ""}`} onFocusCapture={onFocus}>
      <div className="step-head">
        <span className="step-num">{index + 1}</span>
        <input className="input mono step-id" value={step.id} onChange={(e) => onRename(e.target.value.replace(/\s+/g, "-"))} aria-label="Step id" />
        <select className="input" value={kind} onChange={(e) => setKind(e.target.value as StepKind)} aria-label="Kind">
          <option value="agent">Agent step</option>
          <option value="review">Review step</option>
          <option value="aggregate">Aggregate (no agent)</option>
        </select>
        <div className="step-tools">
          <button className="btn btn-ghost btn-small" disabled={index === 0} onClick={() => onMove(-1)} title="Move up">
            ↑
          </button>
          <button className="btn btn-ghost btn-small" disabled={index === count - 1} onClick={() => onMove(1)} title="Move down">
            ↓
          </button>
          <button className="btn btn-ghost btn-small danger" disabled={count === 1} onClick={onRemove} title="Remove step">
            ✕
          </button>
        </div>
      </div>

      {kind !== "aggregate" && (
        <>
          <label className="field">
            <span>Agent</span>
            <select className="input" value={step.agent_id ?? ""} onChange={(e) => onChange({ agent_id: e.target.value })}>
              <option value="">Choose an agent…</option>
              {!knownAgent && <option value={step.agent_id!}>{step.agent_id} (not an active agent)</option>}
              {agents.map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.name} ({a.slug})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{kind === "review" ? "What to check" : "Task"}</span>
            <textarea className="input" rows={2} value={step.task ?? ""} onChange={(e) => onChange({ task: e.target.value })} />
          </label>
        </>
      )}
      {kind === "aggregate" && <p className="muted small">Combines the outputs of the steps it reads into one object, without calling an agent.</p>}

      <div className="field">
        <span>Runs after</span>
        <div className="chips">
          {others.length === 0 && <span className="muted small">Nothing: this is the first step.</span>}
          {others.map((o) => (
            <button key={o} type="button" className={`chip${deps.includes(o) ? " on" : ""}`} onClick={() => onChange({ depends_on: toggle(deps, o) })}>
              {o}
            </button>
          ))}
        </div>
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={customUses}
          onChange={(e) => onChange({ uses: e.target.checked ? [...deps] : undefined })}
        />
        <span>Read outputs from a different set of steps than it waits for</span>
      </label>
      {customUses && (
        <div className="field">
          <span>Reads outputs of</span>
          <div className="chips">
            {others.map((o) => (
              <button key={o} type="button" className={`chip${step.uses!.includes(o) ? " on" : ""}`} onClick={() => onChange({ uses: toggle(step.uses!, o) })}>
                {o}
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === "review" && (
        <div className="grid-2">
          <label className="field">
            <span>Max revisions</span>
            <input
              className="input"
              type="number"
              min={0}
              max={5}
              value={step.max_revisions ?? 0}
              onChange={(e) => onChange({ max_revisions: Number(e.target.value) })}
            />
            <small className="muted">How often a rejection sends the reviewed steps back.</small>
          </label>
          <label className="check">
            <input type="checkbox" checked={Boolean(step.require_approval)} onChange={(e) => onChange({ require_approval: e.target.checked })} />
            <span>Fail the run if the final verdict is a rejection</span>
          </label>
        </div>
      )}
    </section>
  );
}
