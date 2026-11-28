import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentNetwork, NetworkNode } from "../api/types";
import { curve, HUB_ID, layoutNetwork, pointOnCurve, type PlacedLink, type PlacedNode } from "../lib/network";
import { timeAgo } from "../lib/format";
import { prefersReducedMotion } from "./motion";

interface Props {
  data: AgentNetwork;
  height?: number;
  /** Smaller labels; names only on hover (Overview). */
  compact?: boolean;
  onSelect?: (agentId: string) => void;
}

const PULSE_POOL = 48;

interface Pulse {
  link: number;
  start: number;
  duration: number;
  reverse: boolean;
}

/** Agents as neurons around the hub. Links are the runs agents shared; signals
 * travel along them, constantly for agents that are working right now. */
export function NetworkGraph({ data, height = 520, compact, onSelect }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 0) setWidth((prev) => (Math.abs(prev - w) > 8 ? w : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Re-layout only when the shape of the network changes, not on every poll.
  const shapeKey = `${width}|${height}|${data.nodes.map((n) => `${n.id}:${n.runs}`).join(",")}|${data.links.map((l) => `${l.source}-${l.target}:${l.runs}`).join(",")}`;
  const layout = useMemo(
    () => layoutNetwork(data.nodes, data.links, width, height),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shapeKey],
  );
  useEffect(() => {
    layoutSize.current = { w: layout.width, h: layout.height };
  }, [layout]);

  // Live fields (running, last status) come from the latest data.
  const live = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data]);

  const nodeRefs = useRef<(SVGGElement | null)[]>([]);
  const haloRefs = useRef<(SVGCircleElement | null)[]>([]);
  const linkRefs = useRef<(SVGPathElement | null)[]>([]);
  const pulseRefs = useRef<(SVGCircleElement | null)[]>([]);
  const liveRef = useRef(live);

  // ---- zoom and pan: drag to move, Ctrl/⌘ + scroll or the buttons to zoom ----
  const svgRef = useRef<SVGSVGElement>(null);
  const viewport = useRef<SVGGElement>(null);
  const view = useRef({ k: 1, x: 0, y: 0 });
  const target = useRef({ k: 1, x: 0, y: 0 });
  const viewFrame = useRef(0);
  const drag = useRef<{ id: number; sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [zoomed, setZoomed] = useState(false);

  const applyView = () => {
    const v = view.current;
    viewport.current?.setAttribute("transform", `translate(${v.x.toFixed(2)},${v.y.toFixed(2)}) scale(${v.k.toFixed(4)})`);
  };
  const animateView = (next: { k: number; x: number; y: number }, instant = false) => {
    target.current = next;
    setZoomed(Math.abs(next.k - 1) > 0.01 || Math.abs(next.x) > 1 || Math.abs(next.y) > 1);
    cancelAnimationFrame(viewFrame.current);
    if (instant || prefersReducedMotion()) {
      view.current = { ...next };
      applyView();
      return;
    }
    let last = performance.now();
    const step = (now: number) => {
      const a = 1 - Math.exp(-(now - last) / 70); // critically damped feel
      last = now;
      const v = view.current;
      const t = target.current;
      v.k += (t.k - v.k) * a;
      v.x += (t.x - v.x) * a;
      v.y += (t.y - v.y) * a;
      applyView();
      if (Math.abs(t.k - v.k) > 0.0005 || Math.abs(t.x - v.x) > 0.1 || Math.abs(t.y - v.y) > 0.1) viewFrame.current = requestAnimationFrame(step);
      else {
        view.current = { ...t };
        applyView();
      }
    };
    viewFrame.current = requestAnimationFrame(step);
  };
  /** Zoom by `factor` keeping the point (px, py) in viewBox units still. */
  const zoomAt = (factor: number, px: number, py: number) => {
    const t = target.current;
    const k = Math.max(0.5, Math.min(3, t.k * factor));
    const f = k / t.k;
    animateView({ k, x: px - (px - t.x) * f, y: py - (py - t.y) * f });
  };
  const toViewBox = (clientX: number, clientY: number) => {
    const rect = svgRef.current!.getBoundingClientRect();
    return [((clientX - rect.left) / rect.width) * layoutSize.current.w, ((clientY - rect.top) / rect.height) * layoutSize.current.h] as const;
  };
  const layoutSize = useRef({ w: width, h: height });
  useEffect(() => () => cancelAnimationFrame(viewFrame.current), []);

  useEffect(() => {
    const el = svgRef.current;
    if (!el || compact) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return; // plain scrolling keeps scrolling the page
      e.preventDefault();
      const [px, py] = toViewBox(e.clientX, e.clientY);
      zoomAt(Math.exp(-e.deltaY * 0.0025), px, py);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compact]);
  liveRef.current = live;

  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const nodes = layout.nodes;
    const links = layout.links;
    const index = new Map(nodes.map((n, i) => [n.id, i]));
    const ends = links.map((l) => [index.get(l.source)!, index.get(l.target)!] as const);
    const phase = nodes.map((_, i) => [(Math.sin(i * 12.9898) * 43758.5453) % 6.28, (Math.cos(i * 78.233) * 12345.678) % 6.28]);
    const pos = nodes.map((n) => ({ x: n.x, y: n.y }));
    const ctrl = links.map(() => ({ cx: 0, cy: 0 }));
    const fired = new Float64Array(nodes.length);
    const pulses: Pulse[] = [];
    const agentLinks = links.map((l, i) => (l.link ? i : -1)).filter((i) => i >= 0);
    const spokeOf = new Map(links.map((l, i) => [l.link ? "" : l.target, i]));
    let last = performance.now() / 1000;
    let frame = 0;

    const spawn = (link: number, now: number, reverse = false) => {
      if (pulses.length >= PULSE_POOL) return;
      pulses.push({ link, start: now, duration: 0.9 + Math.random() * 0.7, reverse });
    };
    const direction = (l: PlacedLink) => {
      if (!l.link) return false;
      const fwd = l.link.handoffs[l.source] ?? 0;
      const back = l.link.handoffs[l.target] ?? 0;
      if (fwd + back === 0) return Math.random() < 0.5;
      return Math.random() < back / (fwd + back);
    };

    const tick = () => {
      const now = performance.now() / 1000;
      const dt = Math.min(0.1, now - last);
      last = now;

      // gentle drift
      for (let i = 1; i < nodes.length; i++) {
        const [a, b] = phase[i];
        pos[i].x = nodes[i].x + Math.sin(now * 0.45 + a) * 4;
        pos[i].y = nodes[i].y + Math.cos(now * 0.38 + b) * 4;
        nodeRefs.current[i]?.setAttribute("transform", `translate(${pos[i].x.toFixed(1)},${pos[i].y.toFixed(1)})`);
      }
      links.forEach((l, i) => {
        const [a, b] = ends[i];
        const c = curve(pos[a].x, pos[a].y, pos[b].x, pos[b].y, l.link ? 0.12 : 0.04);
        ctrl[i].cx = c.cx;
        ctrl[i].cy = c.cy;
        linkRefs.current[i]?.setAttribute("d", c.d);
      });

      // spawn signals: steady streams to working agents, sparse firing elsewhere
      for (const n of nodes) {
        const info = n.node && liveRef.current.get(n.id);
        if (!info?.running) continue;
        const spoke = spokeOf.get(n.id);
        if (spoke !== undefined && Math.random() < dt * 2.2) spawn(spoke, now);
        for (const li of agentLinks) {
          const l = links[li];
          if ((l.source === n.id || l.target === n.id) && Math.random() < dt * 0.9) spawn(li, now, l.target === n.id);
        }
      }
      if (agentLinks.length && Math.random() < dt * 1.4) {
        // pick a link weighted by strength
        let total = 0;
        for (const li of agentLinks) total += 0.2 + links[li].strength;
        let r = Math.random() * total;
        for (const li of agentLinks) {
          r -= 0.2 + links[li].strength;
          if (r <= 0) {
            spawn(li, now, direction(links[li]));
            break;
          }
        }
      }
      if (nodes.length > 1 && Math.random() < dt * 0.7) {
        const target = nodes[1 + Math.floor(Math.random() * (nodes.length - 1))].id;
        const spoke = spokeOf.get(target);
        if (spoke !== undefined) spawn(spoke, now, Math.random() < 0.3);
      }

      // move signals
      for (let p = pulses.length - 1; p >= 0; p--) {
        const s = pulses[p];
        const t = (now - s.start) / s.duration;
        if (t >= 1) {
          const [a, b] = ends[s.link];
          fired[s.reverse ? a : b] = now;
          pulses.splice(p, 1);
        }
      }
      for (let k = 0; k < PULSE_POOL; k++) {
        const el = pulseRefs.current[k];
        if (!el) continue;
        const s = pulses[k];
        if (!s) {
          el.setAttribute("opacity", "0");
          continue;
        }
        const raw = (now - s.start) / s.duration;
        const t = s.reverse ? 1 - raw : raw;
        const [a, b] = ends[s.link];
        const [x, y] = pointOnCurve(pos[a].x, pos[a].y, ctrl[s.link].cx, ctrl[s.link].cy, pos[b].x, pos[b].y, t);
        el.setAttribute("cx", x.toFixed(1));
        el.setAttribute("cy", y.toFixed(1));
        el.setAttribute("opacity", (Math.sin(Math.PI * raw) * 0.9 + 0.1).toFixed(2));
        el.setAttribute("class", links[s.link].link ? "net-pulse" : "net-pulse spoke");
      }

      // a neuron flares when a signal reaches it
      for (let i = 0; i < nodes.length; i++) {
        const el = haloRefs.current[i];
        if (!el) continue;
        const flare = Math.max(0, 1 - (now - fired[i]) * 1.6);
        el.style.opacity = (0.55 + flare * 0.45).toFixed(2);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [layout]);

  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  const neighbours = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of layout.links) {
      if (!l.link) continue;
      (m.get(l.source) ?? m.set(l.source, new Set()).get(l.source)!).add(l.target);
      (m.get(l.target) ?? m.set(l.target, new Set()).get(l.target)!).add(l.source);
    }
    return m;
  }, [layout]);
  const hovered = hover ? byId.get(hover) : undefined;
  const hoveredInfo = hover ? live.get(hover) : undefined;

  return (
    <div className={`net-wrap${compact ? " compact" : ""}`} ref={wrap} style={{ height }}>
      <svg
        ref={svgRef}
        className={`net${hover ? " has-hover" : ""}${compact ? "" : " pannable"}`}
        onPointerDown={
          compact
            ? undefined
            : (e) => {
                if (e.button !== 0) return;
                drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, vx: target.current.x, vy: target.current.y, moved: false };
              }
        }
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || d.id !== e.pointerId) return;
          const rect = svgRef.current!.getBoundingClientRect();
          const scale = layout.width / rect.width;
          const dx = (e.clientX - d.sx) * scale;
          const dy = (e.clientY - d.sy) * scale;
          if (!d.moved && Math.hypot(dx, dy) < 4) return;
          if (!d.moved) {
            d.moved = true;
            svgRef.current!.setPointerCapture(e.pointerId);
            svgRef.current!.classList.add("dragging");
            setHover(null);
          }
          animateView({ k: target.current.k, x: d.vx + dx, y: d.vy + dy }, true);
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          svgRef.current?.classList.remove("dragging");
          if (d?.moved) {
            suppressClick.current = true; // the release is not a click on a neuron
            window.setTimeout(() => (suppressClick.current = false), 0);
            svgRef.current?.releasePointerCapture(e.pointerId);
          }
        }}
        onPointerCancel={() => {
          drag.current = null;
          svgRef.current?.classList.remove("dragging");
        }}
        onDoubleClick={
          compact
            ? undefined
            : (e) => {
                if ((e.target as Element).closest(".neuron")) return;
                const [px, py] = toViewBox(e.clientX, e.clientY);
                zoomAt(1.6, px, py);
              }
        }
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        height={height}
        role="img"
        aria-label={`Agent network: ${data.nodes.length} agents, ${data.links.length} connections`}
      >
        <defs>
          <radialGradient id="net-halo">
            <stop offset="0%" stopColor="var(--neuron)" stopOpacity="0.55" />
            <stop offset="45%" stopColor="var(--neuron)" stopOpacity="0.16" />
            <stop offset="100%" stopColor="var(--neuron)" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="net-halo-hub">
            <stop offset="0%" stopColor="var(--synapse)" stopOpacity="0.6" />
            <stop offset="50%" stopColor="var(--synapse)" stopOpacity="0.14" />
            <stop offset="100%" stopColor="var(--synapse)" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="net-halo-bad">
            <stop offset="0%" stopColor="var(--bad)" stopOpacity="0.55" />
            <stop offset="100%" stopColor="var(--bad)" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="net-core" cx="35%" cy="35%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="35%" stopColor="var(--neuron)" />
            <stop offset="100%" stopColor="color-mix(in srgb, var(--neuron) 40%, #050816)" />
          </radialGradient>
          <radialGradient id="net-core-hub" cx="35%" cy="35%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="30%" stopColor="var(--synapse)" />
            <stop offset="100%" stopColor="color-mix(in srgb, var(--synapse) 35%, #050816)" />
          </radialGradient>
        </defs>

        <g ref={viewport} className="net-viewport">
          <g>
            {layout.links.map((l, i) => {
              const a = byId.get(l.source)!;
              const b = byId.get(l.target)!;
              const lit = hover != null && (l.source === hover || l.target === hover);
              const cls = l.link ? `net-link${lit ? " lit" : ""}` : `net-spoke${lit ? " lit" : ""}`;
              return (
                <path
                  key={`${l.source}->${l.target}`}
                  ref={(el) => {
                    linkRefs.current[i] = el;
                  }}
                  className={cls}
                  d={curve(a.x, a.y, b.x, b.y, l.link ? 0.12 : 0.04).d}
                  strokeWidth={l.link ? 1 + l.strength * 4.5 : 1}
                  style={l.link ? { opacity: 0.25 + l.strength * 0.6 } : undefined}
                >
                  {l.link && <title>{`${l.source} ↔ ${l.target}: ${l.link.runs} shared runs`}</title>}
                </path>
              );
            })}
          </g>
          <g>
            {Array.from({ length: PULSE_POOL }, (_, k) => (
              <circle
                key={k}
                ref={(el) => {
                  pulseRefs.current[k] = el;
                }}
                className="net-pulse"
                r={3}
                opacity={0}
              />
            ))}
          </g>
          <g>
            {layout.nodes.map((n, i) => (
              <Neuron
                key={n.id}
                placed={n}
                info={n.node ? live.get(n.id) : undefined}
                compact={compact}
                dim={hover != null && hover !== n.id && !neighbours.get(hover)?.has(n.id) && n.id !== HUB_ID}
                gRef={(el) => {
                  nodeRefs.current[i] = el;
                }}
                haloRef={(el) => {
                  haloRefs.current[i] = el;
                }}
                onHover={(id) => !drag.current?.moved && setHover(id)}
                onSelect={onSelect ? (id) => !suppressClick.current && onSelect(id) : undefined}
              />
            ))}
          </g>
        </g>
      </svg>
      {hovered && hoveredInfo && (
        <NodeTip
          placed={{ ...hovered, x: hovered.x * view.current.k + view.current.x, y: hovered.y * view.current.k + view.current.y, r: hovered.r * view.current.k }}
          info={hoveredInfo}
          width={layout.width}
          links={neighbours.get(hovered.id)?.size ?? 0}
        />
      )}
      {!compact && data.nodes.length > 0 && (
        <div className="net-controls" role="group" aria-label="Zoom">
          <button
            className="btn btn-small"
            onClick={() => zoomAt(1.35, layout.width / 2, layout.height / 2)}
            aria-label="Zoom in"
            title="Zoom in (Ctrl + scroll)"
          >
            +
          </button>
          <button className="btn btn-small" onClick={() => zoomAt(1 / 1.35, layout.width / 2, layout.height / 2)} aria-label="Zoom out" title="Zoom out">
            −
          </button>
          <button className={`btn btn-small${zoomed ? "" : " invisible"}`} onClick={() => animateView({ k: 1, x: 0, y: 0 })} title="Back to the whole network">
            Reset
          </button>
        </div>
      )}
      {!compact && data.nodes.length > 0 && <div className={`net-hint small muted${zoomed ? " hidden" : ""}`}>Drag to move · Ctrl + scroll or double-click to zoom</div>}
      {data.nodes.length === 0 && <div className="net-empty">No agents yet. Register one and it will appear here.</div>}
    </div>
  );
}

function Neuron({
  placed,
  info,
  compact,
  dim,
  gRef,
  haloRef,
  onHover,
  onSelect,
}: {
  placed: PlacedNode;
  info: NetworkNode | undefined;
  compact?: boolean;
  dim: boolean;
  gRef: (el: SVGGElement | null) => void;
  haloRef: (el: SVGCircleElement | null) => void;
  onHover: (id: string | null) => void;
  onSelect?: (id: string) => void;
}) {
  const hub = placed.id === HUB_ID;
  const r = placed.r;
  const failed = info?.last_status === "failed";
  const state = hub ? "hub" : info?.status !== "active" ? "off" : info.running ? "firing" : failed ? "failed" : "idle";
  const sparks = Math.min(6, info?.memories ?? 0);
  const label = hub ? "Hub" : (info?.name ?? placed.id);
  return (
    <g
      ref={gRef}
      transform={`translate(${placed.x},${placed.y})`}
      className={`neuron n-${state}${dim ? " dim" : ""}${!hub && onSelect ? " clickable" : ""}`}
      onMouseEnter={hub ? undefined : () => onHover(placed.id)}
      onMouseLeave={hub ? undefined : () => onHover(null)}
      onFocus={hub ? undefined : () => onHover(placed.id)}
      onBlur={hub ? undefined : () => onHover(null)}
      onClick={!hub && onSelect ? () => onSelect(placed.id) : undefined}
      onKeyDown={!hub && onSelect ? (e) => (e.key === "Enter" || e.key === " ") && onSelect(placed.id) : undefined}
      tabIndex={!hub && onSelect ? 0 : undefined}
      aria-label={hub ? undefined : `${label}, ${info?.runs ?? 0} runs`}
    >
      <circle
        ref={haloRef}
        className="neuron-halo"
        r={r * (hub ? 3.4 : 2.8)}
        fill={hub ? "url(#net-halo-hub)" : failed && state === "failed" ? "url(#net-halo-bad)" : "url(#net-halo)"}
      />
      {hub && <circle className="hub-ring" r={r + 9} />}
      {state === "firing" && <circle className="neuron-wave" r={r + 4} />}
      <circle className="neuron-core" r={r} fill={hub ? "url(#net-core-hub)" : "url(#net-core)"} />
      {sparks > 0 && (
        <g className="neuron-sparks" style={{ animationDuration: `${9 + (placed.id.length % 5) * 2}s` }}>
          {Array.from({ length: sparks }, (_, k) => {
            const a = (k / sparks) * Math.PI * 2;
            return <circle key={k} cx={Math.cos(a) * (r + 7)} cy={Math.sin(a) * (r + 7)} r={1.6} />;
          })}
        </g>
      )}
      {(!compact || hub) && (
        <text className={`neuron-label${hub ? " hub" : ""}`} y={hub ? 4 : r + 16} textAnchor="middle">
          {hub ? "HUB" : clip(label, 22)}
        </text>
      )}
    </g>
  );
}

function NodeTip({ placed, info, width, links }: { placed: PlacedNode; info: NetworkNode; width: number; links: number }) {
  const left = placed.x > width - 240 ? placed.x - 230 : placed.x + placed.r + 16;
  return (
    <div className="net-tip" style={{ left, top: Math.max(8, placed.y - 40) }}>
      <strong>{info.name}</strong>
      <span className="mono small muted">{info.id}</span>
      <dl>
        <dt>Runs</dt>
        <dd>{info.runs}</dd>
        <dt>Connections</dt>
        <dd>{links}</dd>
        <dt>Steps</dt>
        <dd>
          {info.tasks}
          {info.failed > 0 && <span className="error-text"> · {info.failed} failed</span>}
        </dd>
        <dt>Memories</dt>
        <dd>{info.memories}</dd>
        <dt>Last active</dt>
        <dd>{info.last_active_at ? timeAgo(info.last_active_at) : "never"}</dd>
      </dl>
      {info.running > 0 && <span className="net-tip-live">● working now</span>}
      {info.status !== "active" && <span className="muted small">{info.status}</span>}
    </div>
  );
}

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
