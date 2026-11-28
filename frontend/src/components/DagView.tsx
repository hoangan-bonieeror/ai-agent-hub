import { useMemo } from "react";
import type { RunStatus, WorkflowStep } from "../api/types";
import { layoutGraph, NODE_H, NODE_W } from "../lib/graph";

interface Props {
  steps: WorkflowStep[];
  /** Live status per step id (run view). */
  statuses?: Record<string, RunStatus | string>;
  selected?: string | null;
  onSelect?: (stepId: string) => void;
  /** Step ids to outline as having a problem (builder). */
  flagged?: Set<string>;
  /** Shrink the graph to the container's width instead of scrolling. */
  fit?: boolean;
}

const KIND_LABEL: Record<string, string> = { agent: "agent", review: "review", aggregate: "aggregate" };

export function DagView({ steps, statuses, selected, onSelect, flagged, fit }: Props) {
  const layout = useMemo(() => layoutGraph(steps), [steps]);
  if (!steps.length) return <div className="dag-empty muted">No steps yet</div>;
  return (
    <div className="dag-scroll">
      <svg
        className="dag"
        width={fit ? "100%" : layout.width}
        height={fit ? undefined : layout.height}
        style={fit ? { maxWidth: layout.width } : undefined}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="img"
        aria-label="Workflow graph"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="dag-arrow" />
          </marker>
        </defs>
        {layout.edges.map((e) => {
          const active = statuses?.[e.from] === "succeeded" && statuses?.[e.to] === "running";
          return <path key={`${e.from}->${e.to}`} d={e.path} className={`dag-edge${active ? " active" : ""}`} markerEnd="url(#arrow)" />;
        })}
        {layout.nodes.map((n) => {
          const status = statuses?.[n.id] ?? "idle";
          const kind = n.step.kind ?? "agent";
          const cls = ["dag-node", `st-${status}`, `kind-${kind}`, selected === n.id ? "selected" : "", flagged?.has(n.id) ? "flagged" : "", onSelect ? "clickable" : ""]
            .filter(Boolean)
            .join(" ");
          const sub = kind === "aggregate" ? "combine outputs" : (n.step.agent_id ?? "no agent");
          return (
            <g
              key={n.id}
              className={cls}
              transform={`translate(${n.x},${n.y})`}
              onClick={onSelect ? () => onSelect(n.id) : undefined}
              tabIndex={onSelect ? 0 : undefined}
              onKeyDown={onSelect ? (ev) => (ev.key === "Enter" || ev.key === " ") && onSelect(n.id) : undefined}
            >
              <title>{`${n.id} (${kind})${statuses ? ` — ${status}` : ""}`}</title>
              <rect width={NODE_W} height={NODE_H} rx={10} />
              <rect className="dag-stripe" width={5} height={NODE_H} rx={2} />
              <text x={16} y={23} className="dag-title">
                {clip(n.id, 20)}
              </text>
              <text x={16} y={42} className="dag-sub">
                {clip(sub, 22)}
              </text>
              <text x={NODE_W - 10} y={23} className="dag-kind" textAnchor="end">
                {KIND_LABEL[kind]}
              </text>
              {status === "running" && <circle className="dag-pulse" cx={NODE_W - 14} cy={41} r={4} />}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
