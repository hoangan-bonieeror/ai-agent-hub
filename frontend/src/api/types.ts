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
  duration_ms: number;
  metadata: Record<string, unknown>;
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
  links: RunLinks;
}

export interface ExecutionSummary {
  execution_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  token_usage: Partial<TokenUsage> | null;
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
  tasks: RunTask[];
}

export interface Execution extends ExecutionSummary {
  task_id: string;
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
