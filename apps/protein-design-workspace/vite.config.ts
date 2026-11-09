import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

function devApiOrigin(): string {
  const raw = process.env.CELLERTZ_DEV_API_ORIGIN ?? "";
  if (raw === "") {
    return "http://127.0.0.1:8765";
  }
  if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(raw)) {
    throw new Error("CELLERTZ_DEV_API_ORIGIN must be http://127.0.0.1:<port>");
  }
  const port = Number(raw.slice(raw.lastIndexOf(":") + 1));
  if (port < 1 || port > 65535) {
    throw new Error("CELLERTZ_DEV_API_ORIGIN must be http://127.0.0.1:<port>");
  }
  return raw;
}

const devApi = devApiOrigin();

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/conversation": devApi,
      "/api/attempt-coordinator": devApi,
      "/api/attempts": devApi,
      "/api/generation-plan/revision": devApi,
      "/api/generation-plan": devApi,
      "/api/generation-plan/decision": devApi,
      "/api/generation-plan/authorization": devApi,
      "/api/generation-plan/dispatch": devApi,
      "/api/generation-plan/apptainer-demonstration": devApi,
      "/api/design-draft": devApi,
      "/api/design-supervisor": devApi,
      "/api/design-journeys": devApi,
      "/api/campaigns": devApi,
      "/api/design-approval": devApi,
      "/api/approved-execution": devApi,
      "/api/approved-runs": devApi,
      "/api/scientific-runner-readiness": devApi,
      "/api/scientific-run-authorizations": devApi,
      "/api/scientific-runs": devApi,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
  },
});
