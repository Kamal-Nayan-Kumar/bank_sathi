import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The deployed backend is on Render, so the API origin is a build-time input
// rather than a hardcoded localhost. Same-origin in preview: Vite proxies
// /api to the local backend.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Local dev talks to a backend on :8000. In a deployed build the frontend
      // calls VITE_API_URL directly, so this proxy is only used by `npm run dev`.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
