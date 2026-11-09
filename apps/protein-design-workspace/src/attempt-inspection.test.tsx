import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";
import { AttemptInspection, type AttemptInspectionRecord } from "./AttemptInspection";

const pdb = [
  "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00  0.00           C",
  "ATOM      2  CA  ALA B   1       3.000   0.000   0.000  1.00  0.00           C",
  "END",
].join("\n");

function inspection(): AttemptInspectionRecord {
  const contacts = {
    cutoff_angstrom: 5,
    binder_residues_in_contact: 1,
    target_residues_in_contact: 1,
    residue_pairs: 1,
    minimum_heavy_atom_distance_angstrom: 3,
    binder_heavy_atoms: 1,
    target_heavy_atoms: 1,
  };
  return {
    status: "ok",
    attempt_id: "attempt.c5c1f13e",
    candidate: "PDL1_l65_s400711_mpnn1",
    selected_model: "model1",
    origin: {
      kind: "recovered",
      execution: "recovered_without_apptainer",
      detail: "Recovered from a completed attempt. This is not live generation.",
    },
    live_processing: false,
    specialist_activity: [],
    specialist_activity_note: "This inspection reads the recorded review. It does not replay specialists.",
    pdb_verified: true,
    execution_compliance_established: false,
    scientific_validation_established: false,
    scientific_evaluation: "pending",
    ranking_score: "not_recorded",
    ranking_note: "ranking_score is raw ipSAE. BindCraft i_pTM is a recorded metric and does not replace ipSAE.",
    filter_policy: "no_filters",
    chains: { target: "A", binder: "B" },
    sequence: "SYKELKEK",
    representative: { model: "model1", rule: "Accepted and Ranked copies match this model's SHA-256" },
    interface_residues: { kind: "empty_field" },
    metrics: [
      {
        metric: "pLDDT",
        model1: { kind: "recorded", text: "0.91" },
        model2: { kind: "recorded", text: "0.9" },
        average: { kind: "recorded", text: "0.9" },
      },
      {
        metric: "n_InterfaceResidues",
        model1: { kind: "recorded", text: "15" },
        model2: { kind: "recorded_zero", text: "0" },
        average: { kind: "recorded", text: "7.5" },
      },
      {
        metric: "InterfaceHbondsPercentage",
        model1: { kind: "recorded", text: "0.2" },
        model2: { kind: "empty_field" },
        average: { kind: "absent" },
      },
      {
        metric: "i_pTM",
        model1: { kind: "recorded", text: "0.16" },
        model2: { kind: "recorded", text: "0.05" },
        average: { kind: "recorded", text: "0.1" },
      },
    ],
    execution_constraints_reported: true,
    execution_constraints: {
      unmet: true,
      requested_max_trajectories: 1,
      accepted_trajectory_files: ["Trajectory/Relaxed/PDL1_l65_s400711.pdb"],
      low_confidence_trajectory_files: [
        "Trajectory/LowConfidence/PDL1_l65_s439581.pdb",
        "Trajectory/LowConfidence/PDL1_l65_s128037.pdb",
      ],
    },
    models: {
      model1: {
        id: "model1",
        representative: true,
        sequence: "SYKELKEK",
        sha256: "abc",
        hash_matches: true,
        pdb_url: "/api/attempts/attempt.c5c1f13e/structures/model1.pdb",
        ca_counts: { A: 1, B: 1 },
        calculated_contacts: contacts,
        recorded_contacts: {
          residue_pairs: null,
          binder_residues_in_contact: null,
          target_residues_in_contact: null,
        },
      },
      model2: {
        id: "model2",
        representative: false,
        sequence: "SYKELKEK",
        sha256: "def",
        hash_matches: true,
        pdb_url: "/api/attempts/attempt.c5c1f13e/structures/model2.pdb",
        ca_counts: { A: 1, B: 1 },
        calculated_contacts: contacts,
        recorded_contacts: {
          residue_pairs: null,
          binder_residues_in_contact: null,
          target_residues_in_contact: null,
        },
      },
    },
    evidence_references: [
      { kind: "attempt", attempt_id: "attempt.c5c1f13e", candidate: "PDL1_l65_s400711_mpnn1" },
      { kind: "structure", model_id: "model1", sha256: "abc", href: "/api/attempts/attempt.c5c1f13e/structures/model1.pdb" },
      { kind: "stats_csv", sha256: "csvhash" },
    ],
  };
}

describe("recovered attempt results", () => {
  beforeEach(() => {
    window.history.replaceState(
      {},
      "",
      "/?area=results&attempt=attempt.c5c1f13e&candidate=PDL1_l65_s400711_mpnn1&model=model1",
    );
  });

  it("shows the recovered attempt and does not open the historical review", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith(".pdb")) {
        return { ok: true, text: async () => pdb } as Response;
      }
      return { ok: true, json: async () => inspection() } as Response;
    });
    render(<AttemptInspection />);
    const region = await screen.findByRole("region", { name: "Attempt results" });
    expect(region).toHaveTextContent("attempt.c5c1f13e");
    expect(region).toHaveTextContent("PDL1_l65_s400711_mpnn1");
    expect(region).toHaveTextContent("Recovered from a completed attempt");
    expect(region).toHaveTextContent("unmet is true");
    expect(region).toHaveTextContent("requested_max_trajectories is 1");
    expect(region).toHaveTextContent("Trajectory/LowConfidence/PDL1_l65_s439581.pdb");
    expect(region).toHaveTextContent("empty field");
    expect(region).toHaveTextContent("0 (recorded zero)");
    expect(region).toHaveTextContent("not recorded");
    expect(region).toHaveTextContent("does not replace ipSAE");
    expect(region).toHaveTextContent("ranking_score is not_recorded");
    expect(region).toHaveTextContent("abc");
    expect(region).toHaveTextContent("csvhash");
    expect(region).toHaveTextContent("does not replay specialists");
    expect(region).toHaveTextContent("No recorded coordinator trace");
    expect(region).not.toHaveTextContent("attempt.2LUTUOX4");
    expect(await screen.findByText(/Calculated from the served PDB|calculated from the served PDB/)).toBeInTheDocument();
  });

  it("shows a persisted candidate score separately from the review and from each model", async () => {
    const body = inspection();
    body.saved_specialist_processing = {
      status: "linked",
      detail: "The saved execution and specialist record match this result.",
      journey_id: "journey_saved",
      round_id: "round_saved",
      plan_id: "plan_saved",
      plan_revision: 4,
      digest: "digest-saved",
      candidate_id: "bindcraft_PDL1_l65_s400711_mpnn1",
      ranking_score: 0.424242,
      ranking_score_scope: "candidate",
      applies_to_each_model: false,
      per_model_ranking_scores: "not_recorded",
      score_source: "ProteinIpSAECalculator",
      ranking_signal: "ipsae",
      model_ids: ["1", "2"],
      artifact_model_ids: ["model1", "model2"],
      aggregation: { model_aggregation: "not_recorded", within_model_aggregation: "not_recorded", protocol_id: "not_recorded" },
      rank_note: "Rank 1 among 1 recorded candidate. This does not establish comparative superiority.",
      critique_agreement: "unknown",
      critique_kind: "deterministic",
      prioritization_bucket: "core",
      prioritization_reason: "The candidate is in the core set.",
      prioritization_kind: "deterministic",
      stages: [
        {
          stage_id: "scoring",
          status: "completed",
          agent_executed: true,
          implementation: "BindCraftIpSAEEvidenceProvider",
          evidence_references: [{ attempt_id: "attempt.c5c1f13e", candidate_id: "bindcraft_PDL1_l65_s400711_mpnn1", revision: 4 }],
        },
      ],
      superseded: [{ status: "superseded", used_as_current_score: false, ranking_score: "not_recorded" }],
      attempted_trajectory_budget: {
        status: "recorded",
        compliance_established: true,
        requested_attempted_trajectories: 4,
        termination_reason: "successful_early_completion",
        establishes_execution_compliance: false,
        missing_is_violation: false,
      },
    };
    body.execution_compliance_note = "Overall execution compliance is not established by PDB verification, attempted-trajectory-budget compliance, or a missing constraint record.";
    body.coordinator_traces = [{ trace_id: "trace_blank", source_query: "   ", execution_record: null, conversation_id: "conv", attempt_id: "attempt.c5c1f13e", steps: [] }];
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => body } as Response);
    render(<AttemptInspection />);
    const region = await screen.findByRole("region", { name: "Attempt results" });
    const specialist = await screen.findByRole("region", { name: "Persisted specialist processing" });
    expect(specialist).toHaveTextContent("0.424242");
    expect(specialist).toHaveTextContent("not an independent score for each model");
    expect(specialist).toHaveTextContent("Scoring audit: not recorded");
    expect(specialist).toHaveTextContent("Agreement unknown");
    expect(specialist).toHaveTextContent("Bucket core");
    expect(specialist).toHaveTextContent("does not establish comparative superiority");
    expect(specialist).toHaveTextContent("does not establish overall execution compliance");
    expect(specialist).toHaveTextContent("Used as the current ranking_score: false");
    expect(region).toHaveTextContent("Recorded review ranking_score is not_recorded");
    expect(region).toHaveTextContent("i_pTM");
    const model = screen.getByRole("article", { name: "Model model1" });
    expect(model).not.toHaveTextContent("0.424242");
    expect(region).toHaveTextContent("A saved record without a question is not a coordinator trace.");
  });

  it("keeps the original score when a later audit disagrees and collapses technical details", async () => {
    const body = inspection();
    body.saved_specialist_processing = {
      status: "linked",
      detail: "The saved execution and specialist record match this result.",
      ranking_score: 0.11,
      original_ranking_score: 0.11,
      ranking_score_scope: "candidate",
      applies_to_each_model: false,
      per_model_ranking_scores: [
        { model_number: 1, ipsae: 0.2, ipsae_ab: 0.2, ipsae_ba: 0.1 },
        { model_number: 2, ipsae: 0.4, ipsae_ab: 0.3, ipsae_ba: 0.4 },
      ],
      aggregation: {
        model_aggregation: "mean",
        within_model_aggregation: "max",
        protocol_id: "CARPNN_2026_REFERENCE",
        pae_cutoff: 10,
        distance_cutoff: 10,
        chain_a: "A",
        chain_b: "B",
      },
      scoring_audit: {
        status: "recorded",
        records: [
          {
            status: "completed",
            agrees: false,
            agreement: "disagrees",
            original_ranking_score: 0.11,
            recalculated_ranking_score: 0.3,
            model_aggregation: "mean",
            protocol_id: "CARPNN_2026_REFERENCE",
            calculated_models: [
              { model_number: 1, ipsae: 0.2, ipsae_ab: 0.2, ipsae_ba: 0.1 },
              { model_number: 2, ipsae: 0.4, ipsae_ab: 0.3, ipsae_ba: 0.4 },
            ],
          },
        ],
      },
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => body } as Response);
    render(<AttemptInspection />);
    const specialist = await screen.findByRole("region", { name: "Persisted specialist processing" });
    expect(specialist).toHaveTextContent("Original persisted ranking_score 0.11");
    expect(specialist).toHaveTextContent("Agreement disagrees");
    expect(specialist).toHaveTextContent("recalculated ranking_score 0.3");
    expect(specialist).toHaveTextContent("original persisted ranking_score stays 0.11");
    expect(screen.getByText("Scoring technical details")).toBeInTheDocument();
    expect(specialist).toHaveTextContent("model1 ipSAE 0.2 ab 0.2 ba 0.1");
    expect(specialist).toHaveTextContent("CARPNN_2026_REFERENCE");
  });

  it("shows a saved coordinator trace without treating it as a new analysis", async () => {
    const body = inspection();
    body.coordinator_traces = [
      {
        trace_id: "trace_saved",
        source_query: "Compare both models of attempt.c5c1f13e",
        execution_record: null,
        conversation_id: "conv-recovered",
        attempt_id: "attempt.c5c1f13e",
        steps: [
          {
            tool: "get_attempt_evidence",
            specialist_role: "ranking",
            execution: "deterministic_tool",
            agent_executed: false,
            status: "ok",
            requested_id: "attempt.c5c1f13e",
            resolved_attempt_id: "attempt.c5c1f13e",
            summary: "Reads the verified selection rule and provenance.",
            evidence_references: [
              {
                models: {
                  model1: {
                    sha256: "abc",
                    structure_href: "/api/attempts/attempt.c5c1f13e/structures/model1.pdb",
                  },
                },
              },
            ],
          },
          {
            tool: null,
            specialist_role: "prioritization",
            execution: "language_model_synthesis",
            agent_executed: false,
            status: "answered",
            requested_id: null,
            resolved_attempt_id: null,
            summary: "Interpreted the tool evidence in English. This is not a new measurement.",
            evidence_references: [],
          },
        ],
      },
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => body,
    } as Response);
    render(<AttemptInspection />);
    const closed = await screen.findByText("Coordinator trace");
    expect(closed.closest("details")).not.toHaveAttribute("open");
    await userEvent.click(closed);
    expect(closed.closest("details")).toHaveAttribute("open");
    const region = await screen.findByRole("region", { name: "Recorded coordinator trace" });
    expect(region).toHaveTextContent("Compare both models of attempt.c5c1f13e");
    expect(region).toHaveTextContent("Deterministic tool");
    expect(region).toHaveTextContent("Language-model synthesis");
    expect(region).toHaveTextContent("get_attempt_evidence");
    expect(region).toHaveTextContent("role ranking");
    expect(region).toHaveTextContent("Specialist agent executed: false");
    expect(region).toHaveTextContent("This is not a new analysis");
    expect(region).toHaveTextContent("/api/attempts/attempt.c5c1f13e/structures/model1.pdb");
    expect(region).not.toHaveTextContent("No recorded coordinator trace");
  });

  it("stays empty when no attempt is selected", async () => {
    window.history.replaceState({}, "", "/?area=results");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<AttemptInspection />);
    expect(await screen.findByText("No attempt is selected. Results stays empty until a round records one.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Manual review of attempt.2LUTUOX4" })).not.toBeInTheDocument();
  });

  it("does not open a fixed attempt from the workspace tab", async () => {
    window.history.replaceState({}, "", "/");
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ campaigns: [], requests: [] }),
    } as Response);
    render(<App />);
    await userEvent.click(screen.getByRole("tab", { name: "Results" }));
    expect(window.location.search).not.toContain("attempt=");
    expect(await screen.findByText("No attempt is selected. Results stays empty until a round records one.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Manual review of attempt.2LUTUOX4" })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/api/attempts/"))).toBe(false);
  });

  it("replaces the visible attempt when the URL changes for the same candidate", async () => {
    window.history.pushState({}, "", "/?area=results&attempt=attempt.one&candidate=SAME&model=model1");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const attempt = String(input).includes("attempt.two") ? "attempt.two" : "attempt.one";
      const body = inspection();
      body.attempt_id = attempt;
      body.candidate = "SAME";
      body.sequence = attempt === "attempt.one" ? "SEQUENCE_ONE" : "SEQUENCE_TWO";
      return { ok: true, json: async () => body } as Response;
    });
    render(<AttemptInspection />);
    expect(await screen.findByText(/SEQUENCE_ONE/)).toBeInTheDocument();
    window.history.pushState({}, "", "/?area=results&attempt=attempt.two&candidate=SAME&model=model1");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText(/SEQUENCE_TWO/)).toBeInTheDocument();
    expect(screen.queryByText(/SEQUENCE_ONE/)).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/attempts/attempt.two/inspection?candidate=SAME&model=model1",
    );
  });

  it("does not let a late response replace the current attempt", async () => {
    let resolveFirst: (value: Response) => void = () => undefined;
    const first = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    window.history.pushState({}, "", "/?area=results&attempt=attempt.one&candidate=SAME&model=model1");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("attempt.two")) {
        const body = inspection();
        body.attempt_id = "attempt.two";
        body.candidate = "SAME";
        body.sequence = "SEQUENCE_TWO";
        body.ranking_score = "0.5353599717583406";
        return Promise.resolve({ ok: true, json: async () => body } as Response);
      }
      return first;
    });
    render(<AttemptInspection />);
    window.history.pushState({}, "", "/?area=results&attempt=attempt.two&candidate=SAME&model=model1");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText(/SEQUENCE_TWO/)).toBeInTheDocument();
    expect(screen.getAllByText(/0\.5353599717583406/).length).toBeGreaterThan(0);
    const late = inspection();
    late.attempt_id = "attempt.one";
    late.candidate = "SAME";
    late.sequence = "SEQUENCE_ONE";
    late.ranking_score = "0.5816821382716431";
    await act(async () => {
      resolveFirst({ ok: true, json: async () => late } as Response);
    });
    expect(screen.queryByText(/SEQUENCE_ONE/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.5816821382716431/)).not.toBeInTheDocument();
    expect(screen.getByText(/SEQUENCE_TWO/)).toBeInTheDocument();
  });

  it("shows recorded original provenance separately from a later audit", async () => {
    const body = inspection();
    body.target = "PDL1";
    body.saved_specialist_processing = {
      status: "linked",
      detail: "The saved execution and specialist record match this result.",
      round_id: "round_recorded",
      ranking_score: 0.5353599717583406,
      ranking_score_scope: "candidate",
      score_source: "ProteinIpSAECalculator",
      ranking_signal: "ipsae",
      ipsae_provenance: { protocol_id: "recorded-protocol", model_aggregation: "mean" },
      aggregation: { model_aggregation: "mean", within_model_aggregation: "not_recorded" },
      per_model_ranking_scores: [{ model_number: 1, ipsae: 0.5, ipsae_ab: 0.4, ipsae_ba: 0.6 }],
      scoring_audit: { status: "not_recorded", records: [] },
      rank_note: "Rank 1 among 1 recorded candidate. This does not establish comparative superiority.",
      critique_agreement: "unknown",
      prioritization_bucket: "core",
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => body } as Response);
    render(<AttemptInspection />);
    const summary = await screen.findByRole("region", { name: "Result summary" });
    expect(summary).toHaveTextContent("PDL1");
    expect(summary).toHaveTextContent("round_recorded");
    expect(summary).toHaveTextContent("Binder length");
    expect(summary).toHaveTextContent("8");
    expect(summary).toHaveTextContent("0.5353599717583406");
    expect(summary).not.toHaveTextContent("validated");
    const specialist = await screen.findByRole("region", { name: "Persisted specialist processing" });
    expect(specialist).toHaveTextContent("Original ipSAE provenance is recorded. Protocol recorded-protocol. Model aggregation mean.");
    expect(specialist).toHaveTextContent("Scoring audit: not recorded");
    expect(screen.getByRole("region", { name: "Per-model scores" })).toHaveTextContent("model1 ipSAE 0.5");
    expect(screen.getByText("Generation metrics").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Scoring technical details").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Provenance, hashes, and references").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Coordinator trace").closest("details")).not.toHaveAttribute("open");
  });

  it("keeps a missing original provenance absent when only an audit exists", async () => {
    const body = inspection();
    body.saved_specialist_processing = {
      status: "linked",
      detail: "The saved execution and specialist record match this result.",
      ranking_score: 0.5816821382716431,
      original_ranking_score: 0.5816821382716431,
      ipsae_provenance: "not_recorded",
      aggregation: { model_aggregation: "not_recorded", within_model_aggregation: "not_recorded" },
      per_model_ranking_scores: "not_recorded",
      scoring_audit: {
        status: "recorded",
        records: [
          {
            status: "completed",
            agrees: true,
            recalculated_ranking_score: 0.5816821382716431,
            original_ranking_score: 0.5816821382716431,
          },
        ],
      },
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => body } as Response);
    render(<AttemptInspection />);
    const specialist = await screen.findByRole("region", { name: "Persisted specialist processing" });
    expect(specialist).toHaveTextContent("Original ipSAE provenance is not recorded.");
    expect(specialist).toHaveTextContent("Scoring audit recalculated ranking_score 0.5816821382716431");
    expect(specialist).toHaveTextContent("Model aggregation is not recorded.");
    expect(screen.queryByRole("region", { name: "Per-model scores" })).not.toBeInTheDocument();
  });

  it("reports a structure that fails integrity without opening it", async () => {
    const body = inspection();
    body.pdb_verified = false;
    body.models.model1.hash_matches = false;
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => body } as Response);
    render(<AttemptInspection />);
    expect(await screen.findByRole("alert")).toHaveTextContent("A hash mismatch or a missing file is not replaced.");
    expect(screen.queryByRole("img", { name: /Alpha-carbon trace/ })).not.toBeInTheDocument();
  });

  it("copies the binder sequence and exposes the verified PDB routes", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).endsWith(".pdb")) {
        return { ok: true, text: async () => pdb } as Response;
      }
      return { ok: true, json: async () => inspection() } as Response;
    });
    render(<AttemptInspection />);
    await screen.findByRole("button", { name: "model1, representative" });
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "model1, representative" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "model2" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Copy binder sequence" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(writeText).toHaveBeenCalledWith("SYKELKEK");
    expect(await screen.findByText("Binder sequence copied.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download model1 PDB" })).toHaveAttribute(
      "href",
      "/api/attempts/attempt.c5c1f13e/structures/model1.pdb",
    );
    expect(screen.getByRole("link", { name: "Download model2 PDB" })).toHaveAttribute(
      "href",
      "/api/attempts/attempt.c5c1f13e/structures/model2.pdb",
    );
    await userEvent.click(screen.getByRole("button", { name: "model2" }));
    expect(screen.getByRole("button", { name: "model2" })).toHaveAttribute("aria-pressed", "true");
  });
});
