import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ProteinDesignModule } from "./ProteinDesignModule";

const owned = {
  request_id: "request_owned",
  request_revision: 2,
  plan_id: "plan_owned",
  plan_revision: 3,
  results: [
    {
      attempt_id: "attempt.linked01",
      status: "verified",
      diagnosis: "verified",
      launched: true,
      terminal: true,
      plan_id: "plan_owned",
      revision: 3,
      digest: "d".repeat(64),
      recorded_at: "2026-11-08T13:25:27Z",
      duration: "Script execution for 2 trajectories took: 0 hours, 7 minutes, 25 seconds",
      executor_log: "Finished all designs.",
      review: {
        candidate: "PDL1_l65_s597806_mpnn1",
        filter_policy: "no_filters",
        scientific_evaluation: "pending",
        platform_ipsae_ranking: false,
        ranking_score_computed: false,
        representative: { model: "model1" },
        chains: { target: "A", binder: "B" },
        omitted: ["ipSAE", "ranking_score"],
        bindcraft_rank: "1",
        stats_csv_sha256: "abc",
        pdb_verified: true,
        metrics: [{
          metric: "i_pTM",
          model1: { kind: "recorded", text: "0.79" },
          model2: { kind: "recorded", text: "0.76" },
        }],
        models: {
          model1: {
            id: "model1",
            representative: true,
            sequence: "A".repeat(65),
            sha256: "hash-model1",
            hash_matches: true,
            pdb_url: "/api/attempts/attempt.linked01/structures/model1.pdb",
            ca_counts: { A: 1, B: 1 },
          },
          model2: {
            id: "model2",
            representative: false,
            sequence: "A".repeat(65),
            sha256: "hash-model2",
            hash_matches: true,
            pdb_url: "/api/attempts/attempt.linked01/structures/model2.pdb",
            ca_counts: { A: 1, B: 1 },
          },
        },
      },
    },
    {
      attempt_id: "attempt.failed01",
      status: "launch_failed",
      diagnosis: "launch_failed",
      launched: true,
      terminal: true,
      plan_id: "plan_owned",
      revision: 3,
      digest: "d".repeat(64),
      recorded_at: "2026-11-08T12:54:23Z",
      duration: null,
      executor_log: null,
      review: null,
    },
  ],
};

function drafts() {
  return {
    status: "drafts_recorded",
    execution_authorized: false,
    executable: false,
    drafts: [
      { message: "#5 design an new binder to PDL1", execution_authorized: false, executable: false, draft: { request_id: "request_owned", revision: 2 } },
      { message: "another request", execution_authorized: false, executable: false, draft: { request_id: "request_empty", revision: 1 } },
    ],
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = String(init?.method ?? "GET").toUpperCase();
    if (method !== "GET") {
      throw new Error(`unexpected ${method} ${url}`);
    }
    if (url === "/api/design-draft") {
      return Response.json(drafts());
    }
    if (url.startsWith("/api/design-supervisor")) {
      return new Response(null, { status: 404 });
    }
    if (url.startsWith("/api/generation-plan")) {
      return new Response(null, { status: 404 });
    }
    if (url === "/api/design-journeys/by-request/request_owned/results") {
      return Response.json(owned);
    }
    if (url === "/api/design-journeys/by-request/request_empty/results") {
      return Response.json({ request_id: "request_empty", request_revision: 1, plan_id: null, plan_revision: null, results: [] });
    }
    if (url.includes("/structures/")) {
      return new Response("ATOM", { status: 200, headers: { "content-type": "chemical/x-pdb" } });
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  }));
});

it("shows only the selected request results and keeps the earlier failure", async () => {
  const user = userEvent.setup();
  render(<ProteinDesignModule />);
  await user.click(await screen.findByRole("button", { name: "#5 design an new binder to PDL1" }));
  await user.click(screen.getByRole("tab", { name: "Results" }));
  expect(await screen.findByText("Status: Generation completed.")).toBeInTheDocument();
  expect(screen.getByText("Scientific evaluation pending.")).toBeInTheDocument();
  expect(screen.getByText("Candidate PDL1_l65_s597806_mpnn1.")).toBeInTheDocument();
  expect(screen.getByText("Length: 65 amino acids.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Structure prediction 1 — representative" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Structure prediction 2" })).toBeInTheDocument();
  expect(screen.getByText("Two structure predictions of the same designed sequence.")).toBeInTheDocument();
  expect(screen.getByText("Structure prediction 1 0.79.")).toBeInTheDocument();
  expect(screen.getByText("Structure prediction 2 0.76.")).toBeInTheDocument();
  expect(screen.getAllByText("Not calculated").length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText("Technical demonstration — no scientific filtering.")).toBeInTheDocument();
  expect(screen.getByText("BindCraft rank")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Earlier attempts" })).toHaveTextContent("attempt.failed01");
  expect(screen.queryByText("historical")).not.toBeInTheDocument();
  const calls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
  expect(calls.filter((url) => url.includes("/results"))).toEqual([
    "/api/design-journeys/by-request/request_owned/results",
  ]);
  expect(calls.some((url) => url.includes("/run"))).toBe(false);

  await user.click(screen.getByRole("tab", { name: "Design" }));
  await user.click(screen.getByRole("button", { name: "another request" }));
  await user.click(screen.getByRole("tab", { name: "Results" }));
  expect(await screen.findByText("No results for this request.")).toBeInTheDocument();
  expect(screen.queryByText("PDL1_l65_s597806_mpnn1.")).not.toBeInTheDocument();
});

it("reloads the selected request results without starting a run", async () => {
  window.history.replaceState({}, "", "/?panel=results&request=request_owned");
  render(<ProteinDesignModule />);
  expect(await screen.findByText("Status: Generation completed.")).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some((call) => String(call[1]?.method ?? "GET").toUpperCase() !== "GET")).toBe(false);
});
