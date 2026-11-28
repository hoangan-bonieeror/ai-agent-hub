import type { WorkflowDefinition, WorkflowStep } from "../api/types";

/** The definition as the API expects it: no empty optional fields. */
export function cleanDefinition(def: WorkflowDefinition): WorkflowDefinition {
  const out: WorkflowDefinition = {
    name: def.name.trim(),
    steps: def.steps.map(cleanStep),
  };
  if (def.description?.trim()) out.description = def.description.trim();
  if (def.output_step) out.output_step = def.output_step;
  if (def.max_parallel) out.max_parallel = def.max_parallel;
  if (def.timeout_seconds) out.timeout_seconds = def.timeout_seconds;
  return out;
}

function cleanStep(step: WorkflowStep): WorkflowStep {
  const kind = step.kind ?? "agent";
  const out: WorkflowStep = { id: step.id.trim(), kind };
  if (kind !== "aggregate") {
    out.agent_id = step.agent_id ?? "";
    out.task = step.task ?? "";
  }
  out.depends_on = [...(step.depends_on ?? [])];
  if (step.uses != null) out.uses = [...step.uses];
  if (kind === "review") {
    out.max_revisions = step.max_revisions ?? 0;
    if (step.require_approval) out.require_approval = true;
  }
  return out;
}

const swap = (list: string[] | null | undefined, from: string, to: string) =>
  list == null ? list : list.map((x) => (x === from ? to : x));

/** Rename the step at `index` and every reference to it. While two steps
 * share an id (mid-typing), references stay put: they are ambiguous. */
export function renameStep(def: WorkflowDefinition, index: number, to: string): WorkflowDefinition {
  const from = def.steps[index]?.id;
  if (from === undefined || from === to) return def;
  const unique = def.steps.filter((s) => s.id === from).length === 1;
  const ref = (list: string[] | null | undefined) => (unique ? swap(list, from, to) : list);
  return {
    ...def,
    output_step: unique && def.output_step === from ? to : def.output_step,
    steps: def.steps.map((s, i) => ({
      ...s,
      id: i === index ? to : s.id,
      depends_on: ref(s.depends_on),
      uses: ref(s.uses),
    })),
  };
}

/** Remove the step at `index` and drop references to it (unless another
 * step still has the same id). */
export function removeStep(def: WorkflowDefinition, index: number): WorkflowDefinition {
  const id = def.steps[index]?.id;
  if (id === undefined) return def;
  const unique = def.steps.filter((s) => s.id === id).length === 1;
  const drop = (list: string[] | null | undefined) => (list == null || !unique ? list : list.filter((x) => x !== id));
  return {
    ...def,
    output_step: unique && def.output_step === id ? null : def.output_step,
    steps: def.steps
      .filter((_, i) => i !== index)
      .map((s) => ({ ...s, depends_on: drop(s.depends_on), uses: drop(s.uses) })),
  };
}
