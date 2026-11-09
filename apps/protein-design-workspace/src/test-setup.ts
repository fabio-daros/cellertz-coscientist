import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { CAMPAIGN_ABOUT_STORAGE_KEY, resetAutomaticCampaignAbout } from "./CampaignAbout";
import { clearJourneyLoadCache } from "./DesignJourney";

beforeEach(() => {
  clearJourneyLoadCache();
  window.localStorage.removeItem("cellertz.saved-design-journey");
  resetAutomaticCampaignAbout();
  window.sessionStorage.setItem(CAMPAIGN_ABOUT_STORAGE_KEY, "1");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      const method = String(init?.method ?? "GET").toUpperCase();
      if (method === "GET" && path === "/api/campaigns") {
        return Response.json({ campaigns: [] });
      }
      if (method === "GET" && path === "/api/design-journeys") {
        return Response.json({ requests: [] });
      }
      if (method === "GET" && path === "/api/attempt-coordinator") {
        return Response.json({ traces: [], limitations: [] });
      }
      throw new Error(`unexpected fetch ${method} ${path}`);
    }),
  );
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
