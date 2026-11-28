import type { NetworkLink, NetworkNode } from "../api/types";

export const HUB_ID = "__hub__";

export interface PlacedNode {
  id: string;
  x: number;
  y: number;
  r: number;
  node: NetworkNode | null; // null for the hub
}

export interface PlacedLink {
  source: string;
  target: string;
  /** 0..1, relative to the strongest link. */
  strength: number;
  link: NetworkLink | null; // null for hub spokes
}

export interface NetworkLayout {
  width: number;
  height: number;
  nodes: PlacedNode[];
  links: PlacedLink[];
}

/** Node radius grows with the number of runs, gently. */
export function nodeRadius(runs: number): number {
  return 9 + Math.min(14, Math.sqrt(runs) * 2.4);
}

/** Small deterministic PRNG so the same data always gives the same picture. */
function seeded(seed: number) {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

/**
 * Force-directed layout: the hub is pinned at the centre, every agent hangs
 * off it on a spring, agents that ran together pull towards each other (more
 * shared runs, shorter spring), and all nodes push each other apart.
 * Runs a fixed number of steps up front so the result is stable.
 */
export function layoutNetwork(nodes: NetworkNode[], links: NetworkLink[], width: number, height: number, iterations = 320): NetworkLayout {
  const cx = width / 2;
  const cy = height / 2;
  const ring = Math.max(90, Math.min(width, height) * 0.38);
  const rand = seeded(hash(nodes.map((n) => n.id).join("|")));
  const maxRuns = Math.max(1, ...links.map((l) => l.runs));

  const placed: PlacedNode[] = [{ id: HUB_ID, x: cx, y: cy, r: 22, node: null }];
  nodes.forEach((n, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2 + rand() * 0.4;
    placed.push({ id: n.id, x: cx + Math.cos(angle) * ring, y: cy + Math.sin(angle) * ring, r: nodeRadius(n.runs), node: n });
  });
  const index = new Map(placed.map((p, i) => [p.id, i]));
  const springs: { a: number; b: number; length: number; k: number }[] = [];
  nodes.forEach((n) => {
    // Busy agents sit a little closer to the hub.
    const pull = Math.min(1, n.runs / Math.max(1, maxRuns));
    springs.push({ a: 0, b: index.get(n.id)!, length: ring * (1 - 0.25 * pull), k: 0.02 });
  });
  for (const l of links) {
    const a = index.get(l.source);
    const b = index.get(l.target);
    if (a === undefined || b === undefined) continue;
    const s = l.runs / maxRuns;
    springs.push({ a, b, length: ring * (0.95 - 0.45 * s), k: 0.01 + 0.04 * s });
  }

  const vx = new Float64Array(placed.length);
  const vy = new Float64Array(placed.length);
  for (let it = 0; it < iterations; it++) {
    const cooling = 1 - it / iterations;
    // repulsion
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        let dx = placed[j].x - placed[i].x;
        let dy = placed[j].y - placed[i].y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) {
          dx = rand() - 0.5;
          dy = rand() - 0.5;
          d2 = dx * dx + dy * dy;
        }
        const d = Math.sqrt(d2);
        const minGap = placed[i].r + placed[j].r + 46;
        const f = (2600 / d2) * (d < minGap ? 3 : 1);
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        vx[i] -= fx;
        vy[i] -= fy;
        vx[j] += fx;
        vy[j] += fy;
      }
    }
    // springs
    for (const s of springs) {
      const A = placed[s.a];
      const B = placed[s.b];
      const dx = B.x - A.x;
      const dy = B.y - A.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - s.length) * s.k;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      vx[s.a] += fx;
      vy[s.a] += fy;
      vx[s.b] -= fx;
      vy[s.b] -= fy;
    }
    for (let i = 1; i < placed.length; i++) {
      vx[i] *= 0.6;
      vy[i] *= 0.6;
      const step = 12 * cooling + 0.5;
      placed[i].x += Math.max(-step, Math.min(step, vx[i]));
      placed[i].y += Math.max(-step, Math.min(step, vy[i]));
      // keep inside the canvas, leaving room for labels
      const m = placed[i].r + 26;
      placed[i].x = Math.max(m, Math.min(width - m, placed[i].x));
      placed[i].y = Math.max(m, Math.min(height - m - 14, placed[i].y));
    }
  }

  // Springs pull into a circle; stretch it to fill a wide canvas.
  const stretch = Math.max(1, Math.min(2.3, width / height));
  for (let i = 1; i < placed.length; i++) {
    const m = placed[i].r + 26;
    placed[i].x = Math.max(m, Math.min(width - m, cx + (placed[i].x - cx) * stretch));
  }

  const out: PlacedLink[] = nodes.map((n) => ({ source: HUB_ID, target: n.id, strength: 0, link: null }));
  for (const l of links) {
    if (index.has(l.source) && index.has(l.target)) out.push({ source: l.source, target: l.target, strength: l.runs / maxRuns, link: l });
  }
  return { width, height, nodes: placed, links: out };
}

/** Gently curved path between two points (bends to one side, consistently). */
export function curve(x1: number, y1: number, x2: number, y2: number, bend = 0.12): { d: string; cx: number; cy: number } {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const cx = mx - (y2 - y1) * bend;
  const cy = my + (x2 - x1) * bend;
  return { d: `M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`, cx, cy };
}

/** Point at t (0..1) on the quadratic curve from (x1,y1) via (cx,cy) to (x2,y2). */
export function pointOnCurve(x1: number, y1: number, cx: number, cy: number, x2: number, y2: number, t: number): [number, number] {
  const u = 1 - t;
  return [u * u * x1 + 2 * u * t * cx + t * t * x2, u * u * y1 + 2 * u * t * cy + t * t * y2];
}
