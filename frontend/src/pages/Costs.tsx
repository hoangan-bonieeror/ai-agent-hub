import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { CostGroup, ModelPrice } from "../api/types";
import { SpendChart } from "../components/SpendChart";
import { Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { useConfirm } from "../components/motion";
import { useApi } from "../hooks/useApi";
import { money, number } from "../lib/format";

const PERIODS = [7, 30, 90];

export function CostsPage() {
  const [params, setParams] = useSearchParams();
  const days = Number(params.get("days")) || 30;
  const agent = params.get("agent") ?? "";
  const costs = useApi(() => api.costs({ days, agent: agent || undefined }), [days, agent], 15000);
  const prices = useApi(() => api.listPrices(), []);
  const agents = useApi(() => api.listAgents(), []);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };
  const c = costs.data;
  const t = c?.total;

  return (
    <>
      <PageHeader
        title="Costs"
        subtitle="What agent calls cost, from their token use and the prices below. Days are counted in UTC."
        actions={
          <div className="toolbar toolbar-inline">
            <select className="input" value={agent} onChange={(e) => set("agent", e.target.value)} aria-label="Agent">
              <option value="">All agents</option>
              {(agents.data?.items ?? []).map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.name}
                </option>
              ))}
            </select>
            <div className="tabs" role="tablist">
              {PERIODS.map((p) => (
                <button key={p} role="tab" aria-selected={p === days} className={p === days ? "active" : ""} onClick={() => set("days", String(p))}>
                  {p} days
                </button>
              ))}
            </div>
          </div>
        }
      />
      <ErrorBox error={costs.error} onRetry={costs.reload} />
      {c && c.unpriced_models.length > 0 && (
        <div className="notice notice-warn">
          {number(c.total.unpriced_executions)} {c.total.unpriced_executions === 1 ? "call" : "calls"} used models with no price, so they
          count as $0: {c.unpriced_models.map((m) => m.key ?? "unknown model").join(", ")}. Add a price under Model prices, then press
          Price past calls.
        </div>
      )}
      <div className="stats">
        <Stat label="Spend" value={t ? money(t.cost_usd) : "…"} hint={`last ${days} days`} />
        <Stat label="Agent calls" value={t ? number(t.executions) : "…"} hint={t ? `${number(t.requests)} model requests` : undefined} />
        <Stat
          label="Tokens"
          value={t ? number(t.input_tokens + t.output_tokens) : "…"}
          hint={t ? `${number(t.input_tokens)} in · ${number(t.output_tokens)} out` : undefined}
        />
        <Stat label="Average per call" value={t ? (t.executions ? money(t.cost_usd / t.executions) : "—") : "…"} />
      </div>

      <Card title="Spend per day">
        {!c ? <Loading /> : c.total.executions === 0 ? <Empty title="No agent calls in this period" /> : <SpendChart days={c.by_day} />}
      </Card>

      {c && c.total.executions > 0 && (
        <div className="grid-3">
          <Card title="By agent">
            <GroupTable rows={c.by_agent} total={c.total.cost_usd} link={(k) => (k ? `/agents/${k}` : undefined)} />
          </Card>
          <Card title="By model">
            <GroupTable rows={c.by_model} total={c.total.cost_usd} empty="unknown" />
          </Card>
          <Card title="By workflow">
            <GroupTable rows={c.by_workflow} total={c.total.cost_usd} empty="Direct runs" />
          </Card>
        </div>
      )}

      <Prices prices={prices.data?.items} error={prices.error} reload={() => (prices.reload(), costs.reload())} />
    </>
  );
}

function GroupTable({ rows, total, empty = "—", link }: { rows: CostGroup[]; total: number; empty?: string; link?: (key: string | null) => string | undefined }) {
  return (
    <div className="table-wrap">
      <table className="table table-compact">
        <thead>
          <tr>
            <th>Name</th>
            <th className="right">Calls</th>
            <th className="right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const href = link?.(r.key);
            const name = r.key ?? <span className="muted">{empty}</span>;
            const share = total > 0 ? r.cost_usd / total : 0;
            return (
              <tr key={r.key ?? "_none"}>
                <td>
                  {href ? <Link to={href}>{name}</Link> : name}
                  <div className="share" title={`${Math.round(share * 100)}% of spend`}>
                    <span style={{ width: `${share * 100}%` }} />
                  </div>
                </td>
                <td className="right">{number(r.executions)}</td>
                <td className="right">
                  {money(r.cost_usd)}
                  {r.unpriced_executions > 0 && (
                    <div className="small warn-text" title="Calls whose model has no price">
                      +{r.unpriced_executions} unpriced
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Prices({ prices, error, reload }: { prices?: ModelPrice[]; error?: Error; reload: () => void }) {
  const confirm = useConfirm();
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();
  const [actionError, setActionError] = useState<string>();

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setActionError(undefined);
    setMessage(undefined);
    try {
      await fn();
      if (done) setMessage(done);
      reload();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void act(async () => {
      await api.setPrice({ model: model.trim(), input_per_mtok: Number(input), output_per_mtok: Number(output) });
      setModel("");
      setInput("");
      setOutput("");
    });
  };

  const edit = (p: ModelPrice) => {
    setModel(p.model);
    setInput(String(p.input_per_mtok));
    setOutput(String(p.output_per_mtok));
  };

  return (
    <Card
      title="Model prices"
      actions={
        <button
          className="btn btn-small"
          disabled={busy}
          onClick={() => act(async () => setMessage(`Priced ${(await api.reprice()).updated} past calls.`))}
          title="Give a cost to stored calls whose model had no price when they ran"
        >
          Price past calls
        </button>
      }
    >
      <p className="muted small">
        US dollars per million tokens, from your provider's price list. Use <code>*</code> to cover dated versions, for example{" "}
        <code>gpt-4.1-mini*</code>. A call is priced when it runs; changing a price later does not change past calls.
      </p>
      <ErrorBox error={error ?? actionError} />
      {message && <p className="ok-text small">{message}</p>}
      {prices && prices.length > 0 && (
        <div className="table-wrap">
          <table className="table table-compact">
            <thead>
              <tr>
                <th>Model</th>
                <th className="right">Input / 1M</th>
                <th className="right">Output / 1M</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {prices.map((p) => (
                <tr key={p.id}>
                  <td className="mono">{p.model}</td>
                  <td className="right">{money(p.input_per_mtok)}</td>
                  <td className="right">{money(p.output_per_mtok)}</td>
                  <td className="right">
                    <button className="btn btn-ghost btn-small" onClick={() => edit(p)}>
                      Edit
                    </button>
                    <button
                      className="btn btn-ghost btn-small danger"
                      disabled={busy}
                      onClick={() => {
                        void confirm(`Remove the price of ${p.model}? New calls to it will count as $0.`, { confirmLabel: "Remove price" }).then((ok) => {
                          if (ok) void act(() => api.deletePrice(p.id));
                        });
                      }}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {prices && prices.length === 0 && <p className="muted">No prices yet. Until you add one, every call counts as $0.</p>}
      <form className="price-form" onSubmit={submit}>
        <label className="field">
          <span>Model</span>
          <input className="input mono" required value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4.1-mini*" />
        </label>
        <label className="field">
          <span>Input $ / 1M tokens</span>
          <input className="input" required type="number" min="0" step="0.0001" value={input} onChange={(e) => setInput(e.target.value)} />
        </label>
        <label className="field">
          <span>Output $ / 1M tokens</span>
          <input className="input" required type="number" min="0" step="0.0001" value={output} onChange={(e) => setOutput(e.target.value)} />
        </label>
        <button className="btn btn-primary" disabled={busy}>
          Save price
        </button>
      </form>
    </Card>
  );
}
