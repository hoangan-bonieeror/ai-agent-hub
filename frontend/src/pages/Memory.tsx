import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { MemoryItem } from "../api/types";
import { Card, Empty, ErrorBox, Loading, PageHeader, Tags } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { dateTime, splitList, timeAgo } from "../lib/format";

const PAGE = 50;

export function MemoryPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const agent = params.get("agent") ?? "";
  const scope = params.get("scope") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const [draft, setDraft] = useState(q);
  const [adding, setAdding] = useState(false);
  useEffect(() => setDraft(q), [q]);

  const memories = useApi(() => api.listMemories({ q, agent, scope, limit: PAGE, offset }), [q, agent, scope, offset]);
  const agents = useApi(() => api.listAgents(), []);

  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "offset") next.delete("offset");
    setParams(next);
  };
  const items = memories.data?.items ?? [];
  const total = memories.data?.total ?? 0;

  return (
    <>
      <PageHeader
        title="Memory"
        subtitle={
          <>
            Notes kept between runs. Agents with <code>memory</code> in their tools can search and save them; private notes stay
            with their agent, shared notes are read by all.
          </>
        }
        actions={
          !adding && (
            <button className="btn btn-primary" onClick={() => setAdding(true)}>
              Add memory
            </button>
          )
        }
      />
      {adding && (
        <MemoryForm
          agents={(agents.data?.items ?? []).map((a) => a.slug)}
          onDone={(saved) => {
            setAdding(false);
            if (saved) memories.reload();
          }}
        />
      )}
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          update("q", draft.trim());
        }}
      >
        <input className="input" placeholder="Search memories…" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <select className="input" value={agent} onChange={(e) => update("agent", e.target.value)}>
          <option value="">Every agent</option>
          {(agents.data?.items ?? []).map((a) => (
            <option key={a.slug} value={a.slug}>
              What {a.slug} can read
            </option>
          ))}
        </select>
        <select className="input" value={scope} onChange={(e) => update("scope", e.target.value)}>
          <option value="">Private and shared</option>
          <option value="agent">Private to an agent</option>
          <option value="shared">Shared</option>
        </select>
        <button className="btn">Search</button>
      </form>
      <ErrorBox error={memories.error} onRetry={memories.reload} />
      <Card>
        {memories.loading && !memories.data ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty title={q ? "Nothing matches" : "No memories yet"}>
            {!q && "Agents save notes here as they work, or add one yourself."}
          </Empty>
        ) : (
          <>
            <p className="muted small">
              {total} {total === 1 ? "memory" : "memories"}
              {q && <> matching “{q}”, best match first</>}
            </p>
            <ul className="memories">
              {items.map((m) => (
                <MemoryRow key={m.id} memory={m} onChange={memories.reload} />
              ))}
            </ul>
            {total > PAGE && (
              <div className="pager">
                <button className="btn btn-small" disabled={offset === 0} onClick={() => update("offset", String(Math.max(0, offset - PAGE)))}>
                  ← Previous
                </button>
                <span className="muted small">
                  {offset + 1}–{offset + items.length} of {total}
                </span>
                <button className="btn btn-small" disabled={offset + PAGE >= total} onClick={() => update("offset", String(offset + PAGE))}>
                  Next →
                </button>
              </div>
            )}
          </>
        )}
      </Card>
    </>
  );
}

function MemoryRow({ memory, onChange }: { memory: MemoryItem; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(memory.content);
  const [tags, setTags] = useState(memory.tags.join(", "));
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      setEditing(false);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="memory">
      <div className="memory-meta">
        <span className={`tag ${memory.scope === "shared" ? "tag-shared" : ""}`}>
          {memory.scope === "shared" ? "shared" : <>private · {memory.owner && <Link to={`/agents/${memory.owner}`}>{memory.owner}</Link>}</>}
        </span>
        <Tags items={memory.tags} empty="" />
        <span className="muted small" title={dateTime(memory.updated_at)}>
          {timeAgo(memory.updated_at)} · {memory.created_by ?? "unknown"}
          {memory.expires_at && <> · expires {dateTime(memory.expires_at)}</>}
        </span>
        <span className="memory-actions">
          {!editing && (
            <button className="btn btn-ghost btn-small" onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
          <button
            className="btn btn-ghost btn-small danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Delete this memory? Agents will no longer find it.")) void run(() => api.deleteMemory(memory.id));
            }}
          >
            Delete
          </button>
        </span>
      </div>
      {editing ? (
        <div className="form-tight">
          <textarea className="input" rows={3} maxLength={4000} value={content} onChange={(e) => setContent(e.target.value)} />
          <input className="input" placeholder="tags, comma separated" value={tags} onChange={(e) => setTags(e.target.value)} />
          <div className="form-actions">
            <button className="btn btn-small" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button className="btn btn-primary btn-small" disabled={busy} onClick={() => run(() => api.updateMemory(memory.id, { content, tags: splitList(tags) }))}>
              Save
            </button>
          </div>
        </div>
      ) : (
        <p className="memory-content">{memory.content}</p>
      )}
      <ErrorBox error={error} />
    </li>
  );
}

function MemoryForm({ agents, onDone }: { agents: string[]; onDone: (saved: boolean) => void }) {
  const [content, setContent] = useState("");
  const [owner, setOwner] = useState("");
  const [tags, setTags] = useState("");
  const [expires, setExpires] = useState("");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      await api.createMemory({
        content,
        scope: owner ? "agent" : "shared",
        owner: owner || null,
        tags: splitList(tags),
        expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
      });
      onDone(true);
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  };

  return (
    <Card title="Add memory">
      <form className="form-tight" onSubmit={submit}>
        <label className="field">
          <span>Note</span>
          <textarea className="input" rows={3} required maxLength={4000} value={content} onChange={(e) => setContent(e.target.value)} placeholder="Reports for Acme go out as PDF." />
        </label>
        <div className="grid-3">
          <label className="field">
            <span>Who can read it</span>
            <select className="input" value={owner} onChange={(e) => setOwner(e.target.value)}>
              <option value="">Every agent (shared)</option>
              {agents.map((a) => (
                <option key={a} value={a}>
                  Only {a}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Tags</span>
            <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="customer, style" />
          </label>
          <label className="field">
            <span>Forget after (optional)</span>
            <input className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </label>
        </div>
        <ErrorBox error={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => onDone(false)}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save memory"}
          </button>
        </div>
      </form>
    </Card>
  );
}
