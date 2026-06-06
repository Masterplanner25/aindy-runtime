import { resolve } from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In production the SPA is served by the same FastAPI server as the API, so
// all relative paths resolve correctly with no base URL configured.
//
// In local dev the Vite dev server (typically port 5173) is separate from
// the API (port 8000), so we proxy the API path prefixes here instead of
// baking http://localhost:8000 into the bundle via VITE_API_BASE_URL.
// VITE_API_BASE_URL still works if you need to target a non-default host;
// setting it overrides the empty default in _core.js.
const API_DEV_TARGET = "http://localhost:8000";
const p = { target: API_DEV_TARGET, changeOrigin: true };

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  define: {
    __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? "0.0.0"),
  },
  build: {
    outDir: "../AINDY/platform/dist",
    emptyOutDir: true,
  },
  base: "/platform/",
  server: {
    proxy: {
      // Root-level API prefixes
      "/api":      p,
      "/auth":     p,
      "/health":   p,
      "/ready":    p,
      "/apps":     p,
      "/client":   p,
      "/identity": p,
      "/watcher":  p,
      // /platform/* API sub-paths — distinct from the /platform/ SPA entry
      // and /platform/assets/ which Vite serves directly.
      "/platform/flows":         p,
      "/platform/observability": p,
      "/platform/keys":          p,
      "/platform/syscalls":      p,
      "/platform/db":            p,
    },
  },
});
