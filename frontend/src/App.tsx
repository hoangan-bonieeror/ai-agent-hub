import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { useApi } from "./hooks/useApi";
import { AgentDetailPage } from "./pages/AgentDetail";
import { AgentFormPage } from "./pages/AgentForm";
import { AgentsPage } from "./pages/Agents";
import { NotFoundPage } from "./pages/NotFound";
import { OverviewPage } from "./pages/Overview";
import { RunDetailPage } from "./pages/RunDetail";
import { RunsPage } from "./pages/Runs";
import { ToolsPage } from "./pages/Tools";
import { WorkflowBuilderPage } from "./pages/WorkflowBuilder";
import { WorkflowDetailPage } from "./pages/WorkflowDetail";
import { WorkflowsPage } from "./pages/Workflows";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/agents", label: "Agents" },
  { to: "/workflows", label: "Workflows" },
  { to: "/runs", label: "Runs" },
  { to: "/tools", label: "Tools" },
];

export function App() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={26} height={26} />
          <span>AI Agent Hub</span>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <ServerStatus />
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs ↗
          </a>
        </div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/new" element={<AgentFormPage />} />
          <Route path="/agents/:id" element={<AgentDetailPage />} />
          <Route path="/agents/:id/edit" element={<AgentFormPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/workflows/new" element={<WorkflowBuilderPage />} />
          <Route path="/workflows/:id" element={<WorkflowDetailPage />} />
          <Route path="/workflows/:id/edit" element={<WorkflowBuilderPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}

function ServerStatus() {
  const { data, error } = useApi(() => api.ready(), [], 15000);
  const ok = data?.status === "ok";
  const label = error ? "API unreachable" : !data ? "Checking…" : ok ? "API ready" : `Degraded: postgres ${data.postgres}, redis ${data.redis}`;
  return (
    <div className="server-status" title={label}>
      <span className={`dot ${error ? "bad" : ok ? "good" : data ? "warn" : ""}`} />
      <span>{label}</span>
    </div>
  );
}
