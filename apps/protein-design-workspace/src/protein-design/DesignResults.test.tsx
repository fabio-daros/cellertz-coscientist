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
      developability_screen: {
        status: "calculated",
        thresholds_from: "engager_target_atlas_bcma_campaign",
        native_ligand: {
          status: "declared",
          ligand: "PD-1",
          source: "pdl1_pd1_interface_4zqk",
          offset_from_declared_frame: 17,
        },
        models: {
          model1: {
            length: 65,
            net_charge: -3,
            net_charge_limit: 2,
            cysteines: { unpaired: [], without_sg_atom: [] },
            sequons: [],
            // Measured, not filtered: this campaign declared no limit.
            occlusion: { status: "measured", footprint: 12, ligand_residues: 22, limit: null, is_a_gate: false },
            rejected_by: null,
            verdict: "passed_measured_filters",
            unscreened: [],
          },
          model2: {
            length: 65,
            net_charge: 4,
            net_charge_limit: 2,
            cysteines: { unpaired: [22], without_sg_atom: [] },
            sequons: ["N31-A-T33"],
            occlusion: { status: "measured", footprint: 3, ligand_residues: 18, limit: 8, is_a_gate: true },
            rejected_by: "unpaired_cysteine",
            verdict: "rejected",
            unscreened: [],
          },
        },
      },
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

it("screens the structure on screen, and separates a measurement from a filter", async () => {
  const user = userEvent.setup();
  window.history.replaceState({}, "", "/?panel=results&request=request_owned");
  render(<ProteinDesignModule />);
  const panel = await screen.findByRole("region", { name: "Developability screen" });
  expect(panel).toHaveTextContent("Passed every filter that could be measured.");
  expect(panel).toHaveTextContent("Net charge: -3 (limit +2, K+R minus D+E).");
  // Covering PD-1's site is what a PD-L1 blocker is for, so it is reported as
  // a number and explicitly not as a filter.
  expect(panel).toHaveTextContent("12 of 22 residues of PD-1's site are covered");
  expect(panel).toHaveTextContent("declared no limit, so it is measured, not filtered");
  expect(panel).toHaveTextContent("shifted to match it");
  expect(panel).not.toHaveTextContent("Not screened:");

  await user.click(screen.getByRole("button", { name: "Structure prediction 2" }));
  expect(panel).toHaveTextContent("Rejected by a free cysteine thiol.");
  expect(panel).toHaveTextContent("Free cysteine thiols: 22.");
  expect(panel).toHaveTextContent("Glycosylation sequons: N31-A-T33.");
  expect(panel).toHaveTextContent("3 of 18 residues of PD-1's site are covered (limit 8).");
});

it("reloads the selected request results without starting a run", async () => {
  window.history.replaceState({}, "", "/?panel=results&request=request_owned");
  render(<ProteinDesignModule />);
  expect(await screen.findByText("Status: Generation completed.")).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some((call) => String(call[1]?.method ?? "GET").toUpperCase() !== "GET")).toBe(false);
});
