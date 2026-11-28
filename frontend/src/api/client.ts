import type {
  Agent,
  AgentInput,
  AgentResult,
  AgentVersion,
  Execution,
  McpServerInput,
  MemoryInput,
  MemoryItem,
  McpServerRow,
  Page,
  Readiness,
  RegistryTool,
  RunDetail,
  RunSummary,
  Workflow,
  WorkflowDefinition,
} from "./types";

const BASE = "/api/v1";
const ACTOR = "dashboard";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

/** Turn a FastAPI error body into one readable line. */
export function describeError(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return (detail as ValidationItem[])
      .map((d) => {
        const where = (d.loc ?? []).filter((p) => p !== "body").join(".");
        const msg = (d.msg ?? "invalid").replace(/^Value error, /, "");
        return where ? `${where}: ${msg}` : msg;
      })
      .join("; ");
  }
  return `Request failed (${status})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Actor": ACTOR };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path.startsWith("/") ? path : `${BASE}/${path}`, {
      ...init,
      headers: { ...headers, ...(init.headers as Record<string, string>) },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Hub API. Is the server running on port 8000?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) throw new ApiError(res.status, describeError(res.status, body), body);
  return body as T;
}

const json = (body: unknown) => JSON.stringify(body);
const qs = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : "";
};

export const api = {
  ready: () => request<Readiness>("/health/ready").catch((e: ApiError) => {
    // 503 still carries the per-service breakdown.
    if (e.status === 503 && e.detail) return e.detail as Readiness;
    throw e;
  }),
  runtimes: () => request<{ items: string[] }>("runtimes"),

  listAgents: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    request<Page<Agent>>(`agents${qs({ limit: 200, ...params })}`),
  getAgent: (id: string) => request<Agent>(`agents/${encodeURIComponent(id)}`),
  createAgent: (body: AgentInput) => request<Agent>("agents", { method: "POST", body: json(body) }),
  updateAgent: (id: string, body: Partial<AgentInput> & { expected_version?: number }) =>
    request<Agent>(`agents/${encodeURIComponent(id)}`, { method: "PATCH", body: json(body) }),
  archiveAgent: (id: string) => request<void>(`agents/${encodeURIComponent(id)}`, { method: "DELETE" }),
  agentVersions: (id: string) => request<AgentVersion[]>(`agents/${encodeURIComponent(id)}/versions`),
  runAgent: (id: string, task: string, input: Record<string, unknown>) =>
    request<AgentResult>(`agents/${encodeURIComponent(id)}/run`, {
      method: "POST",
      body: json({ task, input }),
    }),

  listWorkflows: () => request<Page<Workflow>>("workflows?limit=100"),
  getWorkflow: (id: string) => request<Workflow>(`workflows/${encodeURIComponent(id)}`),
  createWorkflow: (def: WorkflowDefinition) =>
    request<Workflow>("workflows", { method: "POST", body: json(def) }),
  startRun: (workflowId: string, input: Record<string, unknown>) =>
    request<{ run_id: string; links: { self: string; events: string } }>(
      `workflows/${encodeURIComponent(workflowId)}/run`,
      { method: "POST", body: json({ input, wait: false }) },
    ),

  listRuns: (params: { workflow?: string; status?: string; limit?: number; offset?: number } = {}) =>
    request<Page<RunSummary>>(`runs${qs(params)}`),
  getRun: (id: string) => request<RunDetail>(`runs/${encodeURIComponent(id)}`),
  cancelRun: (id: string) => request<unknown>(`runs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  getExecution: (id: string) => request<Execution>(`executions/${encodeURIComponent(id)}`),

  listTools: () => request<{ items: RegistryTool[] }>("tools"),
  listMcpServers: () => request<{ items: McpServerRow[] }>("mcp-servers"),
  getMcpServer: (name: string) => request<McpServerRow>(`mcp-servers/${encodeURIComponent(name)}`),
  createMcpServer: (body: McpServerInput) =>
    request<McpServerRow>("mcp-servers", { method: "POST", body: json(body) }),
  updateMcpServer: (name: string, body: McpServerInput) =>
    request<McpServerRow>(`mcp-servers/${encodeURIComponent(name)}`, { method: "PATCH", body: json(body) }),
  deleteMcpServer: (name: string) =>
    request<void>(`mcp-servers/${encodeURIComponent(name)}`, { method: "DELETE" }),
  listMemories: (params: { q?: string; agent?: string; scope?: string; limit?: number; offset?: number } = {}) =>
    request<Page<MemoryItem>>(`memories${qs(params)}`),
  createMemory: (body: MemoryInput) => request<MemoryItem>("memories", { method: "POST", body: json(body) }),
  updateMemory: (id: string, body: MemoryInput) =>
    request<MemoryItem>(`memories/${encodeURIComponent(id)}`, { method: "PATCH", body: json(body) }),
  deleteMemory: (id: string) => request<void>(`memories/${encodeURIComponent(id)}`, { method: "DELETE" }),
  discoverMcpServer: (name: string) =>
    request<McpServerRow>(`mcp-servers/${encodeURIComponent(name)}/discover`, { method: "POST" }),
};

export const runEventsUrl = (runId: string) => `${BASE}/runs/${encodeURIComponent(runId)}/events`;
