import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the dashboard on http://localhost:5173 and forwards API
// calls to the Hub. Point HUB_API_URL elsewhere if the API is not on port 8000.
const api = process.env.HUB_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: api, changeOrigin: true },
      "/health": { target: api, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "node" },
});
