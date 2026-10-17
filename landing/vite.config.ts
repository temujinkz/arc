import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The arc-engine API only allows its own frontend origin, so in development the
// browser talks to Vite and Vite forwards /api to the backend. Override with ARC_API_URL.
const api = process.env.ARC_API_URL ?? "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: api, changeOrigin: true } } },
});
