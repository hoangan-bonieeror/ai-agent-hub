import { describe, expect, it } from "vitest";
import type { WorkflowDefinition } from "../api/types";
import { removeStep, renameStep } from "./builder";
import { dependenciesOf, layoutGraph, validateDefinition, withExplicitDependencies } from "./graph";

const fanOut: WorkflowDefinition = {
  name: "fan-out",
  steps: [
    { id: "plan", agent_id: "planner", task: "plan", depends_on: [] },
    { id: "a", agent_id: "x", task: "a", depends_on: ["plan"] },
    { id: "b", agent_id: "x", task: "b", depends_on: ["plan"] },
    { id: "report", kind: "aggregate", depends_on: ["a", "b"] },
  ],
};

describe("dependenciesOf", () => {
  it("follows the server's defaults", () => {
    const steps = [
      { id: "one", agent_id: "x", task: "t" },
      { id: "two", agent_id: "x", task: "t", uses: ["one"] },
      { id: "three", agent_id: "x", task: "t" },
    ];
    expect(dependenciesOf(steps, 0)).toEqual([]);
    expect(dependenciesOf(steps, 1)).toEqual(["one"]);
    expect(dependenciesOf(steps, 2)).toEqual(["one", "two"]);
    expect(withExplicitDependencies({ name: "w", steps }).steps[2].depends_on).toEqual(["one", "two"]);
  });
});

describe("validateDefinition", () => {
  it("accepts a valid graph", () => {
    expect(validateDefinition(fanOut)).toEqual([]);
  });

  it("finds cycles", () => {
    const def: WorkflowDefinition = {
      name: "loop",
      steps: [
        { id: "a", agent_id: "x", task: "t", depends_on: ["b"] },
        { id: "b", agent_id: "x", task: "t", depends_on: ["a"] },
      ],
    };
    expect(validateDefinition(def)[0].message).toMatch(/cycle/);
  });

  it("flags unknown refs, missing agents and uses without dependency", () => {
    const def: WorkflowDefinition = {
      name: "bad",
      steps: [
        { id: "a", agent_id: "", task: "t", depends_on: ["ghost"] },
        { id: "b", agent_id: "x", task: "t", depends_on: [] },
      ],
    };
    const messages = validateDefinition(def).map((p) => p.message);
    expect(messages.some((m) => m.includes("needs an agent"))).toBe(true);
    expect(messages.some((m) => m.includes("'ghost'"))).toBe(true);

    const notAncestor: WorkflowDefinition = {
      name: "uses",
      steps: [
        { id: "a", agent_id: "x", task: "t", depends_on: [] },
        { id: "b", agent_id: "x", task: "t", depends_on: [], uses: ["a"] },
      ],
    };
    expect(validateDefinition(notAncestor)[0].message).toMatch(/without depending/);
  });
});

describe("layoutGraph", () => {
  it("puts parallel steps in one column", () => {
    const layout = layoutGraph(fanOut.steps);
    const layer = Object.fromEntries(layout.nodes.map((n) => [n.id, n.layer]));
    expect(layer).toEqual({ plan: 0, a: 1, b: 1, report: 2 });
    expect(layout.edges).toHaveLength(4);
    const a = layout.nodes.find((n) => n.id === "a")!;
    const b = layout.nodes.find((n) => n.id === "b")!;
    expect(a.x).toBe(b.x);
    expect(a.y).not.toBe(b.y);
  });

  it("survives a cycle in a draft", () => {
    const layout = layoutGraph([
      { id: "a", depends_on: ["b"] },
      { id: "b", depends_on: ["a"] },
    ]);
    expect(layout.nodes).toHaveLength(2);
  });
});

describe("builder edits", () => {
  it("renames a step and its references", () => {
    const next = renameStep({ ...fanOut, output_step: "a" }, 1, "alpha");
    expect(next.steps[1].id).toBe("alpha");
    expect(next.steps[3].depends_on).toEqual(["alpha", "b"]);
    expect(next.output_step).toBe("alpha");
  });

  it("leaves references alone while ids are duplicated", () => {
    const dup = renameStep(fanOut, 2, "a"); // b -> a: two steps named a
    expect(dup.steps[3].depends_on).toEqual(["a", "a"]);
    const back = renameStep(dup, 2, "b");
    expect(back.steps.map((s) => s.id)).toEqual(["plan", "a", "b", "report"]);
  });

  it("removes a step and references to it", () => {
    const next = removeStep(fanOut, 2);
    expect(next.steps.map((s) => s.id)).toEqual(["plan", "a", "report"]);
    expect(next.steps[2].depends_on).toEqual(["a"]);
  });
});
