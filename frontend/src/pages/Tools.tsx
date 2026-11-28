import { Fragment, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { McpServerInput, McpServerRow, McpTransport, RegistryTool } from "../api/types";
import { Card, Empty, ErrorBox, JsonBlock, Loading, PageHeader, StatusBadge, Tags } from "../components/ui";
import { useConfirm } from "../components/motion";
import { useApi } from "../hooks/useApi";
import { splitList, timeAgo } from "../lib/format";

export function ToolsPage() {
  const servers = useApi(() => api.listMcpServers(), []);
  const tools = useApi(() => api.listTools(), []);
  const [adding, setAdding] = useState(false);
  const reload = () => {
    servers.reload();
    tools.reload();
  };
  const builtins = (tools.data?.items ?? []).filter((t) => t.source === "builtin");

  return (
    <>
      <PageHeader
        title="Tools"
        subtitle="Tools agents can be granted. Connect MCP servers to add theirs; agents only get the tools listed in their definition."
        actions={
          !adding && (
            <button className="btn btn-primary" onClick={() => setAdding(true)}>
              Add MCP server
            </button>
          )
        }
      />
      {adding && (
        <ServerForm
          onDone={(saved) => {
            setAdding(false);
            if (saved) reload();
          }}
        />
      )}
      <ErrorBox error={servers.error ?? tools.error} onRetry={reload} />
      {servers.loading && !servers.data ? (
        <Loading />
      ) : servers.data?.items.length ? (
        servers.data.items.map((s) => <ServerCard key={s.id} server={s} onChange={reload} />)
      ) : (
        <Card>
          <Empty title="No MCP servers yet">
            Add one to give agents tools such as file access, GitHub or a database. Any server that speaks the Model Context
            Protocol works.
          </Empty>
        </Card>
      )}
      <Card title="Built-in tools">
        <ToolTable tools={builtins} />
      </Card>
    </>
  );
}

function ServerCard({ server, onChange }: { server: McpServerRow; onChange: () => void }) {
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const detail = useApi(() => (open ? api.getMcpServer(server.name) : Promise.resolve(undefined)), [open, server.updated_at]);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(undefined);
    try {
      await fn();
      onChange();
      detail.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(undefined);
    }
  };

  const where =
    server.transport === "stdio"
      ? [server.config.command, ...(server.config.args ?? [])].join(" ")
      : server.config.url;

  if (editing) return <ServerForm existing={server} onDone={(saved) => { setEditing(false); if (saved) onChange(); }} />;

  return (
    <Card
      title={
        <>
          {server.name} <span className="tag">{server.transport.replace("_", " ")}</span>{" "}
          {!server.enabled && <StatusBadge status="disabled" />}
        </>
      }
      actions={
        <div className="actions">
          <button className="btn btn-small" disabled={!!busy} onClick={() => act("discover", () => api.discoverMcpServer(server.name).then(() => setOpen(true)))}>
            {busy === "discover" ? "Connecting…" : "Discover tools"}
          </button>
          <button className="btn btn-small" disabled={!!busy} onClick={() => act("toggle", () => api.updateMcpServer(server.name, { enabled: !server.enabled }))}>
            {server.enabled ? "Disable" : "Enable"}
          </button>
          <button className="btn btn-small" onClick={() => setEditing(true)}>
            Edit
          </button>
          <button
            className="btn btn-small btn-danger"
            disabled={!!busy}
            onClick={() => {
              void confirm(`Remove MCP server ${server.name} and its ${server.tool_count} tools? Agents that use it will fail until you change them.`, {
                confirmLabel: "Remove server",
              }).then((ok) => {
                if (ok) void act("delete", () => api.deleteMcpServer(server.name));
              });
            }}
          >
            Remove
          </button>
        </div>
      }
    >
      {server.description && <p className="muted">{server.description}</p>}
      <dl className="props">
        <dt>{server.transport === "stdio" ? "Command" : "URL"}</dt>
        <dd className="mono clip-wide">{where}</dd>
        {!!server.config.env_from_host?.length && (
          <>
            <dt>Env passed</dt>
            <dd>
              <Tags items={server.config.env_from_host} />
            </dd>
          </>
        )}
        {server.config.headers_from_env && Object.keys(server.config.headers_from_env).length > 0 && (
          <>
            <dt>Headers from env</dt>
            <dd className="mono">
              {Object.entries(server.config.headers_from_env)
                .map(([h, v]) => `${h} ← $${v}`)
                .join(", ")}
            </dd>
          </>
        )}
        <dt>Allowed tools</dt>
        <dd>{server.allowed_tools ? <Tags items={server.allowed_tools} empty="none" /> : "every tool it lists"}</dd>
        <dt>Grant to agents</dt>
        <dd>
          <code>{server.grant}</code> for all, or <code>{server.grant}/&lt;tool&gt;</code> for one
        </dd>
        <dt>Tools found</dt>
        <dd>
          {server.tool_count}{" "}
          <button className="link-btn" onClick={() => setOpen(!open)}>
            {open ? "hide" : "show"}
          </button>
          <span className="muted small"> · updated {timeAgo(server.updated_at)}</span>
        </dd>
      </dl>
      <ErrorBox error={error} />
      {open && (detail.data ? <ToolTable tools={detail.data.tools ?? []} empty="Press Discover tools to load them." /> : <Loading />)}
    </Card>
  );
}

function ToolTable({ tools, empty = "None" }: { tools: RegistryTool[]; empty?: string }) {
  const [shown, setShown] = useState<string | null>(null);
  if (!tools.length) return <p className="muted small">{empty}</p>;
  return (
    <div className="table-wrap">
      <table className="table compact">
        <thead>
          <tr>
            <th>Name (use in an agent's tools)</th>
            <th>Description</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {tools.map((t) => (
            <Fragment key={t.name}>
              <tr>
                <td className="mono">{t.name}</td>
                <td>{t.description ?? <span className="muted">—</span>}</td>
                <td className="right">
                  {t.input_schema && (
                    <button className="btn btn-small btn-ghost" onClick={() => setShown(shown === t.name ? null : t.name)}>
                      {shown === t.name ? "Hide input" : "Input"}
                    </button>
                  )}
                </td>
              </tr>
              {shown === t.name && (
                <tr>
                  <td colSpan={3}>
                    <JsonBlock value={t.input_schema} maxHeight={260} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface FormState {
  name: string;
  transport: McpTransport;
  description: string;
  command: string;
  args: string;
  cwd: string;
  env: string;
  url: string;
  headers: string;
  restrict: boolean;
  allowed: string;
  timeout: number;
}

function initial(s?: McpServerRow): FormState {
  return {
    name: s?.name ?? "",
    transport: s?.transport ?? "stdio",
    description: s?.description ?? "",
    command: s?.config.command ?? "",
    args: (s?.config.args ?? []).join("\n"),
    cwd: s?.config.cwd ?? "",
    env: (s?.config.env_from_host ?? []).join(", "),
    url: s?.config.url ?? "",
    headers: Object.entries(s?.config.headers_from_env ?? {})
      .map(([h, v]) => `${h}=${v}`)
      .join("\n"),
    restrict: s ? s.allowed_tools !== null : false,
    allowed: (s?.allowed_tools ?? []).join(", "),
    timeout: s?.config.timeout_seconds ?? 10,
  };
}

function toBody(f: FormState): McpServerInput {
  const body: McpServerInput = {
    description: f.description.trim() || null,
    allowed_tools: f.restrict ? splitList(f.allowed) : null,
    timeout_seconds: Number(f.timeout) || 10,
  };
  if (f.transport === "stdio") {
    body.command = f.command.trim();
    body.args = f.args.split("\n").map((a) => a.trim()).filter(Boolean);
    body.cwd = f.cwd.trim() || null;
    body.env_from_host = splitList(f.env);
  } else {
    body.url = f.url.trim();
    body.headers_from_env = Object.fromEntries(
      f.headers
        .split("\n")
        .map((l) => l.split("=").map((p) => p.trim()))
        .filter((p) => p.length === 2 && p[0] && p[1]),
    );
  }
  return body;
}

function ServerForm({ existing, onDone }: { existing?: McpServerRow; onDone: (saved: boolean) => void }) {
  const [f, setF] = useState<FormState>(() => initial(existing));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setF((s) => ({ ...s, [k]: v }));
  const stdio = f.transport === "stdio";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      if (existing) {
        await api.updateMcpServer(existing.name, toBody(f));
      } else {
        const created = await api.createMcpServer({ ...toBody(f), name: f.name.trim(), transport: f.transport });
        // Load its tools right away; a failure here still leaves the server saved.
        await api.discoverMcpServer(created.name).catch(() => undefined);
      }
      onDone(true);
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  };

  return (
    <Card title={existing ? `Edit ${existing.name}` : "Add MCP server"}>
      <form className="form-tight" onSubmit={submit}>
        <div className="grid-2">
          <label className="field">
            <span>Name</span>
            <input
              className="input mono"
              required
              disabled={!!existing}
              pattern="[a-z0-9][a-z0-9\-]{0,62}"
              title="Lowercase letters, digits and dashes"
              placeholder="filesystem"
              value={f.name}
              onChange={(e) => set("name", e.target.value)}
            />
          </label>
          <label className="field">
            <span>Transport</span>
            <select className="input" disabled={!!existing} value={f.transport} onChange={(e) => set("transport", e.target.value as McpTransport)}>
              <option value="stdio">stdio (a local program)</option>
              <option value="streamable_http">Streamable HTTP (a URL)</option>
              <option value="sse">SSE (older HTTP servers)</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>Description</span>
          <input className="input" value={f.description} onChange={(e) => set("description", e.target.value)} />
        </label>
        {stdio ? (
          <>
            <div className="grid-2">
              <label className="field">
                <span>Command</span>
                <input className="input mono" required placeholder="npx" value={f.command} onChange={(e) => set("command", e.target.value)} />
              </label>
              <label className="field">
                <span>Working folder (optional)</span>
                <input className="input mono" value={f.cwd} onChange={(e) => set("cwd", e.target.value)} />
              </label>
            </div>
            <label className="field">
              <span>Arguments, one per line</span>
              <textarea
                className="input mono"
                rows={3}
                placeholder={"-y\n@modelcontextprotocol/server-filesystem\nD:\\shared-docs"}
                value={f.args}
                onChange={(e) => set("args", e.target.value)}
              />
            </label>
            <label className="field">
              <span>Environment variables to pass (names only)</span>
              <input className="input mono" placeholder="GITHUB_TOKEN" value={f.env} onChange={(e) => set("env", e.target.value)} />
              <small className="muted">Values are read from the Hub's own environment when the server starts. Never paste secrets here.</small>
            </label>
          </>
        ) : (
          <>
            <label className="field">
              <span>URL</span>
              <input className="input mono" required type="url" placeholder="https://example.com/mcp" value={f.url} onChange={(e) => set("url", e.target.value)} />
            </label>
            <label className="field">
              <span>Headers from environment variables, one per line</span>
              <textarea
                className="input mono"
                rows={2}
                placeholder="Authorization=MCP_AUTH_HEADER"
                value={f.headers}
                onChange={(e) => set("headers", e.target.value)}
              />
              <small className="muted">Header name = name of an environment variable on the Hub holding its value.</small>
            </label>
          </>
        )}
        <div className="grid-2">
          <div className="field">
            <label className="check">
              <input type="checkbox" checked={f.restrict} onChange={(e) => set("restrict", e.target.checked)} />
              <span>Only allow some of its tools</span>
            </label>
            {f.restrict && <input className="input mono" placeholder="read_file, list_directory" value={f.allowed} onChange={(e) => set("allowed", e.target.value)} />}
          </div>
          <label className="field">
            <span>Connect timeout (seconds)</span>
            <input className="input" type="number" min={1} max={300} value={f.timeout} onChange={(e) => set("timeout", Number(e.target.value))} />
          </label>
        </div>
        <ErrorBox error={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => onDone(false)}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : existing ? "Save" : "Add and discover tools"}
          </button>
        </div>
      </form>
    </Card>
  );
}
