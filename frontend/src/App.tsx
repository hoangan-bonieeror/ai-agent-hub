import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api/client";
import { useApi } from "./hooks/useApi";
import { AgentDetailPage } from "./pages/AgentDetail";
import { AgentFormPage } from "./pages/AgentForm";
import { AgentsPage } from "./pages/Agents";
import { CostsPage } from "./pages/Costs";
import { MemoryPage } from "./pages/Memory";
import { NetworkPage } from "./pages/Network";
import { NotFoundPage } from "./pages/NotFound";
import { OverviewPage } from "./pages/Overview";
import { PoliciesPage } from "./pages/Policies";
import { RunDetailPage } from "./pages/RunDetail";
import { RunsPage } from "./pages/Runs";
import { ToolsPage } from "./pages/Tools";
import { WorkflowBuilderPage } from "./pages/WorkflowBuilder";
import { WorkflowDetailPage } from "./pages/WorkflowDetail";
import { WorkflowsPage } from "./pages/Workflows";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/network", label: "Network" },
  { to: "/agents", label: "Agents" },
  { to: "/workflows", label: "Workflows" },
  { to: "/runs", label: "Runs" },
  { to: "/tools", label: "Tools" },
  { to: "/memory", label: "Memory" },
  { to: "/costs", label: "Costs" },
  { to: "/policies", label: "Policies" },
];

export function App() {
  const location = useLocation();
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
          <ThemeToggle />
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs ↗
          </a>
        </div>
      </aside>
      <main className="content">
        {/* keyed by path so each page plays its entrance */}
        <div className="page" key={location.pathname}>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/network" element={<NetworkPage />} />
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
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/costs" element={<CostsPage />} />
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </div>
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

type Theme = "neural" | "light";

function readTheme(): Theme {
  try {
    return localStorage.getItem("hub-theme") === "light" ? "light" : "neural";
  } catch {
    return "neural";
  }
}

/** The neural (dark navy) look is the default; a light theme stays available. */
function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (root.dataset.theme && root.dataset.theme !== theme) {
      // cross-fade colours instead of snapping
      root.classList.add("theme-fade");
      window.setTimeout(() => root.classList.remove("theme-fade"), 450);
    }
    root.dataset.theme = theme;
    try {
      localStorage.setItem("hub-theme", theme);
    } catch {
      // private mode: the choice just isn't remembered
    }
  }, [theme]);
  return (
    <button className="link-btn theme-toggle" onClick={() => setTheme(theme === "neural" ? "light" : "neural")}>
      {theme === "neural" ? "Light theme" : "Neural theme"}
    </button>
  );
}
