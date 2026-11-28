import { describe, expect, it } from "vitest";
import type { NetworkLink, NetworkNode } from "../api/types";
import { HUB_ID, layoutNetwork, pointOnCurve } from "./network";

const node = (id: string, runs = 0): NetworkNode => ({
  id,
  name: id,
  status: "active",
  runtime: "fake",
  model: "m",
  tools: 0,
  memories: 0,
  runs,
  tasks: runs,
  failed: 0,
  running: 0,
  last_status: null,
  last_active_at: null,
});

const nodes = ["a", "b", "c", "d", "e"].map((id, i) => node(id, i * 3));
const links: NetworkLink[] = [
  { source: "a", target: "b", runs: 10, handoffs: { a: 10, b: 0 } },
  { source: "c", target: "d", runs: 1, handoffs: {} },
  { source: "a", target: "ghost", runs: 4, handoffs: {} },
];

describe("layoutNetwork", () => {
  const layout = layoutNetwork(nodes, links, 800, 500);

  it("pins the hub at the centre and keeps agents inside the canvas", () => {
    const hub = layout.nodes.find((n) => n.id === HUB_ID)!;
    expect([hub.x, hub.y]).toEqual([400, 250]);
    for (const n of layout.nodes) {
      expect(n.x).toBeGreaterThan(0);
      expect(n.x).toBeLessThan(800);
      expect(n.y).toBeGreaterThan(0);
      expect(n.y).toBeLessThan(500);
    }
  });

  it("adds a spoke per agent and drops links to unknown agents", () => {
    expect(layout.links.filter((l) => !l.link)).toHaveLength(5);
    expect(layout.links.filter((l) => l.link).map((l) => l.target)).toEqual(["b", "d"]);
    expect(layout.links.find((l) => l.link && l.target === "b")!.strength).toBe(1);
  });

  it("is deterministic and pulls strongly linked agents closer", () => {
    expect(layoutNetwork(nodes, links, 800, 500)).toEqual(layout);
    const at = (id: string) => layout.nodes.find((n) => n.id === id)!;
    const dist = (p: string, q: string) => Math.hypot(at(p).x - at(q).x, at(p).y - at(q).y);
    expect(dist("a", "b")).toBeLessThan(dist("c", "d"));
  });

  it("does not overlap nodes", () => {
    for (let i = 0; i < layout.nodes.length; i++)
      for (let j = i + 1; j < layout.nodes.length; j++) {
        const p = layout.nodes[i];
        const q = layout.nodes[j];
        expect(Math.hypot(p.x - q.x, p.y - q.y)).toBeGreaterThan(p.r + q.r);
      }
  });
});

describe("pointOnCurve", () => {
  it("starts and ends at the endpoints", () => {
    expect(pointOnCurve(0, 0, 5, 5, 10, 0, 0)).toEqual([0, 0]);
    expect(pointOnCurve(0, 0, 5, 5, 10, 0, 1)).toEqual([10, 0]);
  });
});
