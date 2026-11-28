import { useState } from "react";
import type { CostFigures } from "../api/types";
import { money, number } from "../lib/format";

type Day = CostFigures & { day: string };

const H = 160;
const PAD_TOP = 10;
const PAD_BOTTOM = 22;
const PAD_LEFT = 52;

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * exp >= value) ?? 10;
  return step * exp;
}

const label = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

/** Daily spend as thin bars; hover a day for its numbers. */
export function SpendChart({ days }: { days: Day[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 720;
  const plotW = width - PAD_LEFT;
  const plotH = H - PAD_TOP - PAD_BOTTOM;
  const max = niceMax(Math.max(...days.map((d) => d.cost_usd), 0));
  const slot = plotW / Math.max(days.length, 1);
  const barW = Math.max(2, Math.min(18, slot - 2));
  const y = (v: number) => PAD_TOP + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(days.length / 6);
  const active = hover == null ? null : days[hover];

  return (
    <div className="spend-chart">
      <svg viewBox={`0 0 ${width} ${H}`} role="img" aria-label="Spend per day" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={PAD_LEFT} x2={width} y1={y(t)} y2={y(t)} />
            <text className="axis" x={PAD_LEFT - 6} y={y(t) + 4} textAnchor="end">
              {money(t)}
            </text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = PAD_LEFT + i * slot + (slot - barW) / 2;
          const h = d.cost_usd > 0 ? Math.max(2, (d.cost_usd / max) * plotH) : 0;
          const r = Math.min(4, barW / 2, h);
          const top = PAD_TOP + plotH - h;
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)}>
              <rect className="hit" x={PAD_LEFT + i * slot} y={PAD_TOP} width={slot} height={plotH} />
              {h > 0 && (
                <path
                  className={`bar ${hover === i ? "on" : ""}`}
                  d={`M${x},${PAD_TOP + plotH} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${PAD_TOP + plotH} Z`}
                />
              )}
              {i % labelEvery === 0 && (
                <text className="axis" x={x + barW / 2} y={H - 6} textAnchor="middle">
                  {label(d.day)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {active && (
        <div
          className="chart-tip"
          style={{
            left: `${Math.min(88, Math.max(12, ((PAD_LEFT + (hover! + 0.5) * slot) / width) * 100))}%`,
          }}
        >
          <strong>{label(active.day)}</strong>
          <span>{money(active.cost_usd)}</span>
          <span className="muted">
            {number(active.executions)} calls · {number(active.input_tokens + active.output_tokens)} tokens
          </span>
          {active.unpriced_executions > 0 && <span className="warn-text">{active.unpriced_executions} without a price</span>}
        </div>
      )}
    </div>
  );
}
