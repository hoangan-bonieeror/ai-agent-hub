// Shapes returned by the Hub API (/api/v1). Kept by hand next to the FastAPI
// handlers in app/api/v1; see /docs for the authoritative schema.

export type AgentStatus = "active" | "disabled" | "archived";

export interface AgentLimits {
  max_steps: number;
  timeout_seconds: number;
}

export interface Agent {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  runtime: string;
  model: string;
  instructions: string;
  tools: string[];
  capabilities: string[];
  limits: AgentLimits;
  status: AgentStatus;
  enabled: boolean;
  current_version: number;
  created_at: string;
  updated_at: string;
}

export interface AgentInput {
  slug?: string;
  name: string;
  description: string | null;
  runtime: string;
  model: string;
  instructions: string;
  tools: string[];
  capabilities: string[];
  limits: AgentLimits;
  status: "active" | "disabled";
}

export interface AgentVersion {
  agent_id: string;
  version: number;
  config_snapshot: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total?: number;
  limit: number;
  offset: number;
}

export interface TokenUsage {
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface AgentResult {
  agent_id: string;
  runtime: string;
  status: "completed" | "failed" | "timed_out" | "step_limit_exceeded";
  output: unknown;
  error: string | null;
  usage: TokenUsage;
  model: string | null;
  duration_ms: number;
  metadata: Record<string, unknown>;
  // Direct runs (POST /agents/{id}/run) are recorded with their cost.
  execution_id?: string;
  cost_usd?: number | null;
}

export type StepKind = "agent" | "review" | "aggregate";

export interface WorkflowStep {
  id: string;
  kind?: StepKind;
  agent_id?: string | null;
  task?: string;
  depends_on?: string[] | null;
  uses?: string[] | null;
  max_revisions?: number;
  require_approval?: boolean;
}

export interface WorkflowDefinition {
  name: string;
  description?: string;
  steps: WorkflowStep[];
  output_step?: string | null;
  max_parallel?: number | null;
  timeout_seconds?: number | null;
}

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  version: number;
  enabled: boolean;
  definition: WorkflowDefinition;
}

export type RunStatus =
  | "pending"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled";

export interface WorkflowRef {
  id: string;
  name: string;
  version: number;
}

export interface RunLinks {
  self: string;
  events: string;
}

export interface RunSummary {
  run_id: string;
  workflow: WorkflowRef;
  status: RunStatus;
  error: string | null;
  requested_by: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  cost_usd?: number;
  links: RunLinks;
}

export interface ExecutionSummary {
  execution_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  token_usage: Partial<TokenUsage> | null;
  model: string | null;
  cost_usd: number | null;
  error: string | null;
}

export interface RunTask {
  task_id: string;
  run_id: string;
  step_id: string;
  kind: StepKind;
  agent_id: string | null;
  depends_on: string[];
  status: RunStatus;
  output: unknown;
  error: string | null;
  retry_count: number;
  created_at: string;
  updated_at: string;
  executions: ExecutionSummary[];
}

export interface RunDetail extends RunSummary {
  input: Record<string, unknown>;
  output: unknown;
  usage: TokenUsage;
  cost_usd: number;
  unpriced_executions: number;
  tasks: RunTask[];
}

export interface Execution extends ExecutionSummary {
  task_id: string | null;
  run_id: string | null;
  step_id: string | null;
  agent_id: string | null;
  agent_version: number | null;
  logs: unknown[];
}

export interface HubEvent {
  type: string;
  run_id: string;
  step_id?: string | null;
  task_id?: string | null;
  agent_id?: string | null;
  data?: Record<string, unknown>;
  at?: string;
  // run.status (sent when the server no longer holds the run's events)
  status?: RunStatus;
  error?: string | null;
}

export interface Readiness {
  status: "ok" | "degraded";
  postgres: string;
  redis: string;
}

export type McpTransport = "stdio" | "streamable_http" | "sse";

export interface RegistryTool {
  name: string;
  description: string | null;
  source: "builtin" | "mcp_server" | "mcp" | "registry";
  server: string | null;
  input_schema: Record<string, unknown> | null;
  enabled: boolean;
}

export interface McpServerInput {
  name?: string;
  transport?: McpTransport;
  description?: string | null;
  command?: string | null;
  args?: string[];
  cwd?: string | null;
  env_from_host?: string[];
  url?: string | null;
  headers_from_env?: Record<string, string>;
  allowed_tools?: string[] | null;
  timeout_seconds?: number;
  enabled?: boolean;
}

export interface McpServerRow {
  id: string;
  name: string;
  description: string | null;
  transport: McpTransport;
  config: {
    command?: string;
    args?: string[];
    cwd?: string;
    env_from_host?: string[];
    url?: string;
    headers_from_env?: Record<string, string>;
    timeout_seconds?: number;
  };
  allowed_tools: string[] | null;
  enabled: boolean;
  grant: string;
  tool_count: number;
  tools?: RegistryTool[];
  created_at: string;
  updated_at: string;
}

export interface MemoryItem {
  id: string;
  scope: "agent" | "shared";
  owner: string | null;
  content: string;
  tags: string[];
  created_by: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryInput {
  content?: string;
  scope?: "agent" | "shared";
  owner?: string | null;
  tags?: string[];
  expires_at?: string | null;
}

export interface CostFigures {
  executions: number;
  cost_usd: number;
  unpriced_executions: number;
  requests: number;
  input_tokens: number;
  output_tokens: number;
}

export interface CostGroup extends CostFigures {
  key: string | null;
}

export interface CostSummary {
  since: string;
  until: string;
  days: number;
  total: CostFigures;
  by_day: (CostFigures & { day: string })[];
  by_agent: CostGroup[];
  by_model: CostGroup[];
  by_workflow: CostGroup[];
  unpriced_models: { key: string | null; unpriced_executions: number; input_tokens: number; output_tokens: number }[];
}

export interface ModelPrice {
  id: string;
  model: string;
  input_per_mtok: number;
  output_per_mtok: number;
  created_at: string;
  updated_at: string;
}

export type PolicyKind = "budget" | "tools" | "models";
export type PolicyScope = "global" | "agent" | "workflow";

export interface BudgetRules {
  limit_usd: number;
  period: "day" | "month" | "total";
  action: "block" | "warn";
}

export interface BudgetStatus {
  spent_usd: number;
  limit_usd: number;
  since: string | null;
  used: number;
  reached: boolean;
}

export interface Policy {
  id: string;
  name: string;
  description: string | null;
  kind: PolicyKind;
  scope: PolicyScope;
  target: string | null;
  rules: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  status?: BudgetStatus;
}

export interface PolicyInput {
  name?: string;
  description?: string | null;
  kind?: PolicyKind;
  scope?: PolicyScope;
  target?: string | null;
  rules?: Record<string, unknown>;
  enabled?: boolean;
}

export type PolicyCheckResult =
  | { allowed: false; policy: string; reason: string }
  | { allowed: true; applied: string[]; tools: string[]; removed_tools: string[]; blocked_tools: string[]; warnings: string[] };

export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export interface NetworkNode {
  id: string;
  name: string;
  status: string;
  runtime: string;
  model: string;
  tools: number;
  memories: number;
  /** Runs in the window this agent took part in. */
  runs: number;
  tasks: number;
  failed: number;
  /** Steps running or queued right now. */
  running: number;
  last_status: string | null;
  last_active_at: string | null;
}

export interface NetworkLink {
  source: string;
  target: string;
  /** Runs both agents took part in. */
  runs: number;
  /** Times each agent handed work to the other (keyed by the sender). */
  handoffs: Record<string, number>;
}

export interface AgentNetwork {
  days: number;
  runs: number;
  nodes: NetworkNode[];
  links: NetworkLink[];
}
