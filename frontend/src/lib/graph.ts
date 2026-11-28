import type { WorkflowDefinition, WorkflowStep } from "../api/types";

/** Same defaulting rules as WorkflowDefinition.dependencies_of on the server. */
export function dependenciesOf(steps: WorkflowStep[], index: number): string[] {
  const step = steps[index];
  if (step.depends_on != null) return [...step.depends_on];
  if (step.uses != null) return [...step.uses];
  return steps.slice(0, index).map((s) => s.id);
}

export function inputsOf(steps: WorkflowStep[], index: number): string[] {
  const step = steps[index];
  return step.uses != null ? [...step.uses] : dependenciesOf(steps, index);
}

/** Copy of the definition with every step's depends_on spelled out. */
export function withExplicitDependencies(def: WorkflowDefinition): WorkflowDefinition {
  return {
    ...def,
    steps: def.steps.map((s, i) => ({ ...s, depends_on: dependenciesOf(def.steps, i) })),
  };
}

export interface GraphProblem {
  stepId?: string;
  message: string;
}

/** Client-side checks that mirror the server's, so the builder can flag
 * mistakes before saving. The server still has the final word (422). */
export function validateDefinition(def: WorkflowDefinition): GraphProblem[] {
  const problems: GraphProblem[] = [];
  if (!def.name.trim()) problems.push({ message: "The workflow needs a name." });
  if (def.steps.length === 0) problems.push({ message: "Add at least one step." });
  const ids = new Set<string>();
  def.steps.forEach((s) => {
    if (!s.id.trim()) problems.push({ message: "Every step needs an id." });
    else if (ids.has(s.id)) problems.push({ stepId: s.id, message: `Duplicate step id '${s.id}'.` });
    ids.add(s.id);
  });
  def.steps.forEach((s, i) => {
    const kind = s.kind ?? "agent";
    if (kind !== "aggregate") {
      if (!s.agent_id) problems.push({ stepId: s.id, message: `Step '${s.id}' needs an agent.` });
      if (!s.task?.trim()) problems.push({ stepId: s.id, message: `Step '${s.id}' needs a task.` });
    }
    for (const ref of [...(s.depends_on ?? []), ...(s.uses ?? [])]) {
      if (ref === s.id) problems.push({ stepId: s.id, message: `Step '${s.id}' cannot depend on itself.` });
      else if (!ids.has(ref)) problems.push({ stepId: s.id, message: `Step '${s.id}' refers to '${ref}', which is not a step.` });
    }
    if (kind === "review" && inputsOf(def.steps, i).length === 0) {
      problems.push({ stepId: s.id, message: `Review step '${s.id}' has nothing to review.` });
    }
  });
  if (problems.length) return problems;

  const cycle = findCycle(def.steps);
  if (cycle) {
    problems.push({ message: `Steps form a dependency cycle: ${cycle.join(" → ")}.` });
    return problems;
  }
  const anc = ancestors(def.steps);
  def.steps.forEach((s, i) => {
    const missing = inputsOf(def.steps, i).filter((r) => !anc.get(s.id)!.has(r));
    if (missing.length) {
      problems.push({
        stepId: s.id,
        message: `Step '${s.id}' uses ${missing.join(", ")} without depending on it.`,
      });
    }
  });
  if (def.output_step && !ids.has(def.output_step)) {
    problems.push({ message: `Output step '${def.output_step}' is not a step.` });
  }
  return problems;
}

function findCycle(steps: WorkflowStep[]): string[] | null {
  const deps = new Map(steps.map((s, i) => [s.id, dependenciesOf(steps, i)]));
  const state = new Map<string, 1 | 2>();
  const path: string[] = [];
  const visit = (id: string): string[] | null => {
    if (state.get(id) === 2) return null;
    if (state.get(id) === 1) return [...path.slice(path.indexOf(id)), id];
    state.set(id, 1);
    path.push(id);
    for (const d of deps.get(id) ?? []) {
      const found = visit(d);
      if (found) return found;
    }
    path.pop();
    state.set(id, 2);
    return null;
  };
  for (const s of steps) {
    const found = visit(s.id);
    if (found) return found;
  }
  return null;
}

function ancestors(steps: WorkflowStep[]): Map<string, Set<string>> {
  const deps = new Map(steps.map((s, i) => [s.id, dependenciesOf(steps, i)]));
  const memo = new Map<string, Set<string>>();
  const get = (id: string): Set<string> => {
    const cached = memo.get(id);
    if (cached) return cached;
    const out = new Set<string>();
    memo.set(id, out);
    for (const d of deps.get(id) ?? []) {
      out.add(d);
      get(d).forEach((x) => out.add(x));
    }
    return out;
  };
  steps.forEach((s) => get(s.id));
  return memo;
}

export interface LayoutNode {
  id: string;
  step: WorkflowStep;
  layer: number;
  x: number;
  y: number;
}

export interface LayoutEdge {
  from: string;
  to: string;
  /** SVG path from the dependency's right edge to the step's left edge. */
  path: string;
}

export interface Layout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

export const NODE_W = 184;
export const NODE_H = 58;
const GAP_X = 64;
const GAP_Y = 22;
const PAD = 12;

/** Layered left-to-right layout: a step sits one column right of its deepest
 * dependency. Cycles (invalid drafts) are broken by list order. */
export function layoutGraph(steps: WorkflowStep[]): Layout {
  const index = new Map(steps.map((s, i) => [s.id, i]));
  const layer = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    const known = layer.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const i = index.get(id)!;
    const deps = dependenciesOf(steps, i).filter((d) => index.has(d));
    const value = deps.length ? Math.max(...deps.map(depth)) + 1 : 0;
    visiting.delete(id);
    layer.set(id, value);
    return value;
  };
  steps.forEach((s) => depth(s.id));

  const columns: string[][] = [];
  steps.forEach((s) => {
    const l = layer.get(s.id)!;
    (columns[l] ??= []).push(s.id);
  });
  const tallest = Math.max(1, ...columns.map((c) => c?.length ?? 0));
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * GAP_Y;
  const pos = new Map<string, { x: number; y: number }>();
  columns.forEach((col, l) => {
    if (!col) return;
    const colH = col.length * NODE_H + (col.length - 1) * GAP_Y;
    const top = (height - colH) / 2;
    col.forEach((id, j) => pos.set(id, { x: PAD + l * (NODE_W + GAP_X), y: top + j * (NODE_H + GAP_Y) }));
  });

  const nodes = steps.map((s) => ({ id: s.id, step: s, layer: layer.get(s.id)!, ...pos.get(s.id)! }));
  const edges: LayoutEdge[] = [];
  steps.forEach((s, i) => {
    for (const d of dependenciesOf(steps, i)) {
      const a = pos.get(d);
      const b = pos.get(s.id);
      if (!a || !b || d === s.id) continue;
      const x1 = a.x + NODE_W;
      const y1 = a.y + NODE_H / 2;
      const x2 = b.x;
      const y2 = b.y + NODE_H / 2;
      const mid = (x1 + x2) / 2;
      const path =
        x2 > x1
          ? `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`
          : `M${x1},${y1} C${x1 + 40},${y1 - 60} ${x2 - 40},${y2 - 60} ${x2},${y2}`;
      edges.push({ from: d, to: s.id, path });
    }
  });
  const width = PAD * 2 + Math.max(1, columns.length) * NODE_W + Math.max(0, columns.length - 1) * GAP_X;
  return { nodes, edges, width, height };
}
