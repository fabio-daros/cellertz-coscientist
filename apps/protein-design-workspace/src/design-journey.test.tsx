import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { clearJourneyLoadCache, DesignJourney } from "./DesignJourney";

const savedJourney = {
  journey_id: "journey_saved",
  request_id: "request_1",
  request_revision: 1,
  plan_id: "plan_saved",
  plan_revision: 1,
  gpu_execution: "disabled",
  approved: false,
  specialist_agents_executed: false,
  knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
  hermes: { status: "not_applicable", detail: "Hermes was not called." },
  stages: [
    {
      stage_id: "generation_planning",
      status: "completed",
      implementation_kind: "deterministic",
      implementation: "GenerationPlanService.draft",
      tools: ["generation_plan_draft"],
      agent_executed: false,
      evidence_references: [],
      output: { approved: false },
      limitations: ["GPU execution is disabled."],
    },
  ],
};

describe("design journey", () => {
  it("shows recorded stages and does not mark later specialists complete", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 1,
        gpu_execution: "disabled",
        approved: false,
        specialist_agents_executed: false,
        knowledge: {
          status: "irrelevant",
          detail: "No validated Second Brain source matched this PDL1 design request.",
          sources: [],
        },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "generation_planning",
            status: "completed",
            implementation_kind: "deterministic",
            implementation: "GenerationPlanService.draft",
            tools: ["generation_plan_draft"],
            agent_executed: false,
            evidence_references: [{ kind: "generation_plan", plan_id: "plan_saved", revision: 1 }],
            output: { approved: false },
            limitations: ["GPU execution is disabled."],
          },
          {
            stage_id: "ranking",
            status: "not_applicable",
            implementation_kind: "deterministic",
            implementation: "ProteinSupervisorV1",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Ranking sorts raw ipSAE. No ipSAE evidence exists."],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const region = await screen.findByRole("region", { name: "Design journey" });
    expect(region).toHaveTextContent("generation_planning: completed");
    expect(region).toHaveTextContent("ranking: not applicable");
    expect(region).toHaveTextContent("GPU execution disabled");
    expect(region).toHaveTextContent("Approved false");
    expect(region).toHaveTextContent("Specialist agents executed false");
    expect(region).toHaveTextContent("does not rerun specialists");
  });

  it("shows a transport failure separately from an absent journey", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    const { rerender } = render(
      <DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />,
    );
    expect(await screen.findByText("The design journey could not be read.")).toBeInTheDocument();
    expect(screen.queryByText("The design journey was not recorded.")).not.toBeInTheDocument();

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("empty");
      },
    } as unknown as Response);
    rerender(<DesignJourney requestId="request_2" revision={1} requestText="Design a PDL1 binder." />);
    expect(await screen.findByText(/The design journey could not be read\. The server returned HTTP 500\./)).toBeInTheDocument();

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({ status: "journey_absent" }),
    } as Response);
    rerender(<DesignJourney requestId="request_3" revision={1} requestText="Design a PDL1 binder." />);
    expect(await screen.findByText("The design journey was not recorded.")).toBeInTheDocument();
  });

  it("sends explicit draft fields and restores a saved journey", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => savedJourney,
    } as Response);
    render(
      <DesignJourney
        requestId="request_1"
        revision={1}
        requestText="Design a PDL1 binder for review."
        fields={[
          { name: "target_id", value: "PDL1", provenance: "user" },
          { name: "generator", value: null, provenance: "absent" },
          { name: "ranking_signal", value: "raw_ipsae", provenance: "documented_default" },
        ]}
      />,
    );
    expect(await screen.findByText(/journey_saved/)).toBeInTheDocument();
    const bodies = fetchMock.mock.calls.map((call) => JSON.parse(String((call[1] as RequestInit).body)));
    expect(bodies.length).toBeGreaterThan(0);
    expect(bodies.every((body) => body.request_id === "request_1" && body.request_revision === 1)).toBe(true);
    expect(bodies[0].fields).toEqual([
      { name: "target_id", value: "PDL1", provenance: "user" },
      { name: "generator", value: null, provenance: "absent" },
      { name: "ranking_signal", value: "raw_ipsae", provenance: "documented_default" },
    ]);
  });

  it("records approval for the displayed plan and keeps generation blocked", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 3,
        gpu_execution: "disabled",
        approved: false,
        specialist_agents_executed: false,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "generation_planning",
            status: "completed",
            implementation_kind: "deterministic",
            implementation: "GenerationPlanService.draft",
            tools: ["generation_plan_draft"],
            agent_executed: false,
            evidence_references: [],
            output: {
              plan_revision: 3,
              digest: "a".repeat(64),
              inputs: { input_artifacts: null },
              parameters: { seed: null },
              resource_limits: { gpu: null, memory_mib: 32768 },
            },
            limitations: ["GPU execution is disabled."],
          },
          {
            stage_id: "approval",
            status: "pending",
            implementation_kind: "not_executed",
            implementation: "human_review",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: { approved: false },
            limitations: ["Explicit human approval is still required."],
          },
          {
            stage_id: "generation",
            status: "blocked",
            implementation_kind: "adapter",
            implementation: "ProteinGenerationAgent",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["ProteinGenerationAgent was not called."],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    expect(await screen.findByText("a".repeat(64))).toBeInTheDocument();
    expect(screen.getByText("32768")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record approval" })).toBeDisabled();
    await user.type(screen.getByLabelText("Reviewer identity"), "professor.ada");
    await user.type(screen.getByLabelText("Reason"), "I reviewed this saved revision.");
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 3,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: false,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "approval",
            status: "completed",
            implementation_kind: "human_review",
            implementation: "GenerationPlanService.approve",
            tools: ["generation_plan_decision"],
            agent_executed: false,
            evidence_references: [],
            output: { reviewer_id: "professor.ada" },
            limitations: ["Approval does not establish scientific validation."],
          },
          {
            stage_id: "generation",
            status: "blocked",
            implementation_kind: "adapter",
            implementation: "ProteinGenerationAgent",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: [
              "Recorded plan approval does not launch generation.",
            ],
          },
        ],
      }),
    } as Response);
    await user.click(screen.getByRole("button", { name: "Record approval" }));
    const region = await screen.findByRole("region", { name: "Design journey" });
    expect(region).toHaveTextContent("approval: completed");
    expect(region).toHaveTextContent("generation: blocked");
    expect(region).toHaveTextContent("does not launch generation");
    expect(region).not.toHaveTextContent("running");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/design-journeys/journey_saved/approval",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows blockers before authorization and keeps launch separate", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const review = {
      plan_id: "plan_saved",
      revision: 4,
      digest: "b".repeat(64),
      input_identities: [{ role: "target_structure", sha256: "c".repeat(64) }],
      input_registration: "verified",
      resource_budget: { gpu: "0", cpu_count: 4, memory_mib: 16384, max_trajectories: null },
      questions: [],
      unresolved_blockers: ["recorded_plan_approval_is_not_execution_authorization"],
      recorded_plan_approval: true,
      execution_authorized: false,
      launches_compute: false,
    };
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 4,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: false,
        execution_review: review,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "approval",
            status: "completed",
            implementation_kind: "human_review",
            implementation: "GenerationPlanService.approve",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: [],
          },
          {
            stage_id: "execution_authorization",
            status: "blocked",
            implementation_kind: "human_review",
            implementation: "GenerationPlanService.approve",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Recorded plan approval does not authorize execution."],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const blocked = await screen.findByRole("region", { name: "Execution review" });
    expect(blocked).toHaveTextContent("b".repeat(64));
    expect(blocked).toHaveTextContent("recorded_plan_approval_is_not_execution_authorization");
    expect(blocked).toHaveTextContent("16384");
    expect(screen.queryByRole("button", { name: "Authorize execution" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Launch execution" })).not.toBeInTheDocument();

    const readyReview = {
      ...review,
      unresolved_blockers: [],
      recorded_plan_approval: false,
    };
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_ready",
        request_id: "request_ready",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 4,
        gpu_execution: "disabled",
        approved: false,
        specialist_agents_executed: false,
        execution_review: readyReview,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "execution_authorization",
            status: "pending",
            implementation_kind: "not_executed",
            implementation: "GenerationPlanService.approve",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Execution authorization does not launch generation."],
          },
          {
            stage_id: "scoring",
            status: "not_applicable",
            implementation_kind: "not_executed",
            implementation: "ProteinScoringToolV2",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["No scoring evidence exists."],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_ready" revision={1} requestText="Design a ready binder." />);
    expect(await screen.findByRole("button", { name: "Authorize execution" })).toBeDisabled();
    await user.type(screen.getByLabelText("Execution reviewer"), "professor.ada");
    await user.type(screen.getByLabelText("Execution reason"), "Authorize this revision.");
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_ready",
        request_id: "request_ready",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 4,
        gpu_execution: "disabled",
        approved: false,
        specialist_agents_executed: false,
        execution_authorization: { execution_authorized: true, launches_compute: false },
        execution_review: { ...readyReview, execution_authorized: true },
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "execution_authorization",
            status: "completed",
            implementation_kind: "human_review",
            implementation: "GenerationPlanService.approve",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Execution authorization does not launch generation."],
          },
          {
            stage_id: "generation",
            status: "blocked",
            implementation_kind: "adapter",
            implementation: "ProteinGenerationAgent",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Execution is authorized. Launch has not been requested."],
          },
          {
            stage_id: "scoring",
            status: "not_applicable",
            implementation_kind: "not_executed",
            implementation: "ProteinScoringToolV2",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["No scoring evidence exists."],
          },
        ],
      }),
    } as Response);
    await user.click(screen.getByRole("button", { name: "Authorize execution" }));
    expect(await screen.findByRole("button", { name: "Launch execution" })).toBeDisabled();
    const authorizedRegion = screen.getAllByRole("region", { name: "Design journey" }).at(-1);
    expect(authorizedRegion).toHaveTextContent("generation: blocked");
    expect(authorizedRegion).toHaveTextContent("scoring: not applicable");
    await user.type(screen.getByLabelText("Launch reviewer"), "professor.ada");
    await user.type(screen.getByLabelText("Launch reason"), "Launch the CPU double.");
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_ready",
        request_id: "request_ready",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 4,
        gpu_execution: "disabled",
        approved: false,
        specialist_agents_executed: false,
        execution_authorization: { execution_authorized: true, launches_compute: false },
        execution_review: { ...readyReview, execution_authorized: true },
        execution: {
          status: "verified",
          consumed: true,
          terminal: true,
          accepted_candidate: true,
          successful_candidate_generation: true,
          progress: [{ status: "started" }, { status: "verified" }],
          result_link: {
            journey_id: "journey_ready",
            attempt_id: "attempt.abc123",
            candidate_id: "PDL1_l65_s7_mpnn1",
          },
        },
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "generation",
            status: "completed",
            implementation_kind: "adapter",
            implementation: "GenerationPlanService.launch_apptainer_demonstration",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["An accepted candidate was linked. Scientific validation is not established."],
          },
          {
            stage_id: "scoring",
            status: "blocked",
            implementation_kind: "not_executed",
            implementation: "ProteinScoringToolV2",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Required evidence for this stage was not recorded."],
          },
        ],
      }),
    } as Response);
    await user.click(screen.getByRole("button", { name: "Launch execution" }));
    const finished = await screen.findByRole("region", { name: "Execution outcome" });
    expect(finished).toHaveTextContent("attempt.abc123");
    expect(finished).toHaveTextContent("PDL1_l65_s7_mpnn1");
    expect(screen.getAllByRole("region", { name: "Design journey" }).at(-1)).toHaveTextContent("scoring: blocked");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/design-journeys/journey_ready/execution-authorization",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/design-journeys/journey_ready/launch",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("offers authorization for a saved recorded plan and keeps launch gated", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_saved",
        plan_revision: 2,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: false,
        approval: { scope: "recorded_plan", revision: 2, digest: "b".repeat(64) },
        execution_review: {
          plan_id: "plan_saved",
          revision: 2,
          digest: "b".repeat(64),
          input_identities: [],
          input_registration: "verified",
          resource_budget: { gpu: "1", cpu_count: 4, memory_mib: 32768 },
          questions: [],
          unresolved_blockers: [],
          configuration_blockers: [],
          recorded_plan_approval: true,
          authorization_available: true,
          server_execution: "apptainer-demonstration",
          journey_gpu_execution: "disabled",
          execution_authorized: false,
          launches_compute: false,
        },
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "execution_authorization",
            status: "blocked",
            implementation_kind: "human_review",
            implementation: "GenerationPlanService.approve",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: null,
            limitations: ["Recorded plan approval does not authorize execution."],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney mode="restore" journeyId="journey_saved" />);
    const review = await screen.findByRole("region", { name: "Execution review" });
    expect(review).toHaveTextContent("Server execution");
    expect(review).toHaveTextContent("apptainer-demonstration");
    expect(review).toHaveTextContent("Saved journey GPU execution");
    expect(review).toHaveTextContent("historical");
    expect(screen.getByRole("button", { name: "Authorize execution" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Launch execution" })).not.toBeInTheDocument();
    expect(review).not.toHaveTextContent("demonstration_not_configured");
  });

  it("initializes the launch GPU index from the approved assignment", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_gpu",
        request_id: "request_gpu",
        request_revision: 1,
        plan_id: "plan_gpu",
        plan_revision: 2,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: false,
        execution_authorization: { execution_authorized: true, launches_compute: false },
        execution_review: {
          plan_id: "plan_gpu",
          revision: 2,
          digest: "d".repeat(64),
          input_identities: [],
          input_registration: "verified",
          resource_budget: { gpu: "1", cpu_count: 4, memory_mib: 32768 },
          questions: [],
          unresolved_blockers: [],
          recorded_plan_approval: false,
          execution_authorized: true,
          launches_compute: false,
        },
        execution: {
          status: "budget_exhausted",
          terminal: true,
          consumed: true,
          accepted_candidate: false,
          successful_candidate_generation: false,
          termination_reason: "attempted_trajectory_budget_exhausted",
          budget_compliance_established: true,
          scientific_validation_established: false,
          failure_counters: { "Trajectory_one-hot_pLDDT": "1", "Trajectory_final_pLDDT": "1" },
          progress: [{ status: "rejected" }, { status: "budget_exhausted" }],
        },
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [],
      }),
    } as Response);
    render(<DesignJourney mode="restore" journeyId="journey_gpu" />);
    const outcome = await screen.findByRole("region", { name: "Execution outcome" });
    expect(outcome).toHaveTextContent("Budget exhausted without an accepted candidate.");
    expect(outcome).toHaveTextContent("Budget compliance");
    expect(outcome).toHaveTextContent("These failure counters are not metric scores.");
    expect(screen.queryByRole("button", { name: "Launch execution" })).not.toBeInTheDocument();
  });

  it("shows the approved GPU index in the launch field", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        journey_id: "journey_gpu",
        request_id: "request_gpu",
        request_revision: 1,
        plan_id: "plan_gpu",
        plan_revision: 2,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: false,
        execution_authorization: { execution_authorized: true, launches_compute: false },
        execution_review: {
          plan_id: "plan_gpu",
          revision: 2,
          digest: "d".repeat(64),
          input_identities: [],
          input_registration: "verified",
          resource_budget: { gpu: "1" },
          questions: [],
          unresolved_blockers: [],
          recorded_plan_approval: false,
          execution_authorized: true,
          launches_compute: false,
        },
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [],
      }),
    } as Response);
    render(<DesignJourney mode="restore" journeyId="journey_gpu" />);
    expect(await screen.findByLabelText("GPU index")).toHaveValue("1");
    expect(screen.getByRole("button", { name: "Launch execution" })).toBeDisabled();
  });

  it("reopens a saved journey without posting a plan", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => savedJourney,
    } as Response);
    render(<DesignJourney mode="restore" journeyId="journey_saved" />);
    expect(await screen.findByText(/journey_saved/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/design-journeys/journey_saved");
    expect(fetchMock.mock.calls.every((call) => (call[1] as RequestInit | undefined)?.method !== "POST")).toBe(true);
  });

  it("reviews demonstration values before saving and keeps non-recordable limits out", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const journey = {
      ...savedJourney,
      stages: [
        {
          stage_id: "design_request",
          status: "completed",
          implementation_kind: "recorded_request",
          implementation: "DesignDrafting",
          tools: [],
          agent_executed: false,
          evidence_references: [],
          output: {
            request_text: "Design a PDL1 binder for review.",
            conflicts: [],
            historical_conflicts: [
              {
                field: "filter_policy",
                conflict:
                  "no_filters was not recorded. The technical demonstration acknowledgement is required. no_filters does not establish scientific quality.",
                status: "historical",
                plan_revision: 2,
                digest: "831192b269bba75517ca67966644b39128c5e174ef60d55baf015a6ef39cd5b8",
                detail:
                  "This conflict was stored on the journey. The current plan revision records the technical demonstration acknowledgement, so this conflict is not current evidence.",
              },
            ],
            acknowledgement_evidence: {
              filter_policy: "no_filters",
              recorded: true,
              text: "technical-smoke-test no_filters. This filter policy is for the technical demonstration and does not establish scientific quality.",
              establishes_scientific_quality: false,
            },
          },
          limitations: [],
        },
        ...savedJourney.stages,
      ],
      approval_history: [
        { revision: 1, digest: "abc", reviewer_id: "professor.ada", scope: "recorded_plan" },
      ],
      completion: {
        request_fields: {},
        conflicts: [
          {
            field: "hotspot_assumption",
            conflict: "The stated hotspot conflicts with the verified settings. The stated hotspot was kept.",
          },
        ],
        historical_conflicts: [
          {
            field: "filter_policy",
            conflict:
              "no_filters was not recorded. The technical demonstration acknowledgement is required. no_filters does not establish scientific quality.",
            status: "historical",
            detail: "This conflict is not current evidence.",
          },
        ],
        acknowledgement_evidence: {
          filter_policy: "no_filters",
          recorded: true,
          text: "technical-smoke-test no_filters. This filter policy is for the technical demonstration and does not establish scientific quality.",
          establishes_scientific_quality: false,
        },
        approval_history: [],
        technical_demonstration: {
          name: "Technical demonstration",
          profile_id: "pdl1-bindcraft-apptainer-demonstration-v1",
          authorizes_execution: false,
          launches_compute: false,
          acknowledgement:
            "technical-smoke-test no_filters. This filter policy is for the technical demonstration and does not establish scientific quality.",
          values: [
            {
              name: "filter_policy",
              value: "no_filters",
              source: "technical_demonstration_profile",
              recordable: true,
              note: "no_filters is for the technical demonstration and does not establish scientific quality.",
            },
            {
              name: "memory_mib",
              value: 32768,
              source: "technical_demonstration_profile",
              recordable: true,
              note: "Host RAM in MiB. This is not GPU VRAM.",
            },
            {
              name: "seed",
              value: 1,
              source: "technical_demonstration_profile",
              recordable: true,
              note: "",
            },
            {
              name: "sequence_limits",
              value: { max_trajectories: 1, num_seqs: 1, max_mpnn_sequences: 1 },
              source: "technical_demonstration_profile",
              recordable: false,
              note: "They are not saved.",
            },
          ],
          conflicts: [],
        },
      },
    };
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => journey,
    } as Response);
    render(
      <DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder for review." />,
    );
    const form = await screen.findByRole("form", { name: "Complete saved plan" });
    expect(form).toHaveTextContent("Host RAM (MiB)");
    expect(form).toHaveTextContent("Host RAM. This is not GPU VRAM.");
    expect(form).toHaveTextContent("does not establish scientific quality");
    expect(form).toHaveTextContent("The current plan records this acknowledgement");
    expect(form).toHaveTextContent("Historical conflict");
    expect(form).toHaveTextContent("not current evidence");
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByText(/stated hotspot was kept/)).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "design_request completed" })).toHaveTextContent(
      "not current evidence",
    );
    expect(screen.getByRole("region", { name: "Approval history" })).toHaveTextContent("Revision 1");
    await user.click(screen.getByRole("button", { name: "Review technical demonstration values" }));
    await user.click(screen.getByRole("button", { name: "Save plan changes" }));
    const save = fetchMock.mock.calls
      .map((call) => call[1] as RequestInit | undefined)
      .map((init) => (init?.body ? JSON.parse(String(init.body)) : null))
      .find((body) => Array.isArray(body?.fields) && body.fields.some((field: { name: string }) => field.name === "memory_mib"));
    expect(save.request_id).toBe("request_1");
    expect(save.request_revision).toBe(1);
    expect(save.acknowledgements).toEqual(["technical-smoke-test"]);
    expect(save.fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "memory_mib",
          value: 32768,
          provenance: "user",
          source: "technical_demonstration_profile",
        }),
        expect.objectContaining({ name: "filter_policy", value: "no_filters", provenance: "user" }),
      ]),
    );
    expect(save.fields.some((field: { name: string }) => field.name === "sequence_limits")).toBe(false);
  });

  it("shows the exhausted attempt as its own round and prepares the next round once", async () => {
    const historical = {
      journey_id: "journey_saved",
      request_id: "request_1",
      request_revision: 1,
      plan_id: "plan_parent",
      plan_revision: 2,
      gpu_execution: "disabled",
      approved: true,
      specialist_agents_executed: false,
      approval: { scope: "recorded_plan", reviewer_id: "professor.ada" },
      execution_authorization: null,
      knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
      hermes: { status: "not_applicable", detail: "Hermes was not called." },
      stages: [
        {
          stage_id: "generation_planning",
          status: "completed",
          implementation_kind: "deterministic",
          implementation: "GenerationPlanService.draft",
          tools: [],
          agent_executed: false,
          evidence_references: [],
          output: { digest: "parent-digest" },
          limitations: [],
        },
      ],
      rounds: [
        {
          round_id: "round_journey_saved_1",
          parent_round_id: null,
          child_round_id: null,
          ordinal: 1,
          active: true,
          plan_id: "plan_parent",
          plan_revision: 2,
          digest: "parent-digest",
          target: "PDL1",
          outcome: "budget_exhausted",
          outcome_label: "Budget exhausted without an accepted candidate.",
          execution_completed: true,
          successful_candidate_generation: false,
          candidate_count: 0,
          budget_usage: {
            attempted_trajectories: 1,
            low_confidence_trajectories: 1,
            relaxed_trajectories: 0,
          },
          budget_compliance_established: true,
          scientific_validation_established: false,
          recorded_review_status: "recorded_plan",
          execution_authorization_status: "not_authorized",
          approved_gpu: "1",
          launch_gpu_index: 0,
          attempt_id: "attempt.0fb068b6",
          parameters: [{ name: "gpu", value: "1", source: "recorded_round" }],
        },
      ],
    };
    const prepared = {
      ...historical,
      plan_id: "plan_child",
      plan_revision: 1,
      approved: false,
      approval: null,
      rounds: [
        { ...historical.rounds[0], active: false, child_round_id: "round_journey_saved_2" },
        {
          round_id: "round_journey_saved_2",
          parent_round_id: "round_journey_saved_1",
          child_round_id: null,
          ordinal: 2,
          active: true,
          plan_id: "plan_child",
          plan_revision: 1,
          digest: "child-digest",
          target: "PDL1",
          outcome_label: "No launch recorded.",
          execution_completed: false,
          successful_candidate_generation: false,
          candidate_count: 0,
          budget_usage: null,
          recorded_review_status: "not_recorded",
          execution_authorization_status: "not_authorized",
          gpu_proposal: "1",
          profile_gpu: null,
          profile_accepts_gpu_proposal: true,
          profile_restriction:
            "The technical demonstration profile does not assign a host GPU. Choose an explicit GPU index on this plan.",
          parameters: [{ name: "gpu", value: "1", source: "previous_round", provenance: "preserved" }],
          sequence_limits: { max_trajectories: 1, num_seqs: 1, max_mpnn_sequences: 1 },
        },
      ],
    };
    let release: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).endsWith("/rounds")) {
        return pending;
      }
      return { ok: true, status: 200, json: async () => historical } as Response;
    });
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const region = await screen.findByRole("region", { name: "Design journey" });
    expect(await screen.findByText("Budget exhausted without an accepted candidate.")).toBeInTheDocument();
    expect(region).toHaveTextContent("Budget exhausted without an accepted candidate.");
    expect(region).toHaveTextContent("1 attempted, 1 LowConfidence, 0 relaxed");
    expect(region).toHaveTextContent("Attempt attempt.0fb068b6.");
    expect(region).toHaveTextContent("Approved GPU 1. Launch GPU 0. This round does not ask for another GPU index.");
    expect(region).toHaveTextContent("Budget compliance established.");
    expect(region).toHaveTextContent("Scientific validation is not established.");
    expect(region).toHaveTextContent("recorded_plan");
    expect(region).toHaveTextContent("not_authorized");
    expect(region).toHaveTextContent("Successful candidate generation");
    expect(screen.getByText("Technical details")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Prepare next round" });
    fireEvent.click(button);
    fireEvent.click(button);
    const posts = fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/rounds"));
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toEqual({
      parent_round_id: "round_journey_saved_1",
    });
    release({ ok: true, json: async () => prepared } as Response);
    expect(await screen.findByRole("article", { name: "Round 2" })).toHaveTextContent(
      "does not assign a host GPU",
    );
    expect(screen.getByRole("article", { name: "Round 2" })).toHaveTextContent("GPU proposal 1");
    expect(screen.getByRole("article", { name: "Round 2" })).not.toHaveTextContent("index 1 only");
    expect(screen.getByRole("article", { name: "Round 1" })).toHaveTextContent(
      "Budget exhausted without an accepted candidate.",
    );
    expect(screen.getAllByRole("button", { name: "Prepare next round" })).toHaveLength(1);
    fetchMock.mockRestore();
  });

  it("collapses each round independently and cancels only the unlaunched round", async () => {
    const journey = {
      journey_id: "journey_saved",
      request_id: "request_1",
      request_revision: 1,
      plan_id: "plan_child",
      plan_revision: 1,
      gpu_execution: "disabled",
      approved: false,
      specialist_agents_executed: false,
      knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
      hermes: { status: "not_applicable", detail: "Hermes was not called." },
      stages: [
        {
          stage_id: "generation_planning",
          status: "completed",
          implementation_kind: "deterministic",
          implementation: "GenerationPlanService.draft",
          tools: [],
          agent_executed: false,
          evidence_references: [],
          output: { digest: "child-digest" },
          limitations: [],
        },
      ],
      rounds: [
        {
          round_id: "round_1",
          ordinal: 1,
          active: false,
          status: "open",
          cancellable: false,
          child_round_id: "round_2",
          outcome_label: "Budget exhausted without an accepted candidate.",
          target: "PDL1",
          candidate_count: 0,
          execution_completed: true,
          successful_candidate_generation: false,
          recorded_review_status: "recorded_plan",
          execution_authorization_status: "authorized",
          attempt_id: "attempt.0fb068b6",
          approved_gpu: "1",
          launch_gpu_index: 0,
          budget_compliance_established: true,
        },
        {
          round_id: "round_2",
          ordinal: 2,
          active: true,
          parent_round_id: "round_1",
          status: "open",
          cancellable: true,
          outcome_label: "No launch recorded.",
          target: "PDL1",
          candidate_count: 0,
          execution_completed: false,
          successful_candidate_generation: false,
          recorded_review_status: "not_recorded",
          execution_authorization_status: "not_authorized",
          plan_id: "plan_child",
          plan_revision: 1,
          parameters: [{ name: "gpu", value: "1", source: "previous_round", provenance: "preserved" }],
        },
      ],
    };
    const cancelled = {
      ...journey,
      rounds: [
        journey.rounds[0],
        {
          ...journey.rounds[1],
          status: "cancelled",
          cancellable: false,
          cancelled_at: "2026-10-24T12:00:00+00:00",
          outcome_label: "Cancelled.",
        },
      ],
    };
    let release: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).endsWith("/cancellation")) {
        return pending;
      }
      return { ok: true, status: 200, json: async () => journey } as Response;
    });
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    expect(await screen.findByRole("button", { name: "Cancel round" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel round" })).toBeInTheDocument();
    const details = screen.getAllByText("Technical details");
    expect(details).toHaveLength(2);
    fireEvent.click(details[1]);
    expect(screen.getAllByText("Technical details")).toHaveLength(1);
    expect(screen.getByText("Hide technical details")).toBeInTheDocument();
    const cancel = screen.getByRole("button", { name: "Cancel round" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    const posts = fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/cancellation"));
    expect(posts).toHaveLength(1);
    release({ ok: true, status: 200, json: async () => cancelled } as Response);
    const cancelledSection = await screen.findByRole("group", { name: "Cancelled rounds (1)" });
    expect(cancelledSection).not.toHaveAttribute("open");
    expect(screen.getByRole("article", { name: "Round 1" })).toHaveTextContent(
      "Budget exhausted without an accepted candidate.",
    );
    expect(screen.getByRole("button", { name: "Prepare another round" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel round" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter((call) => String(call[0]).endsWith("/rounds"))).toHaveLength(0);
    expect(document.querySelector(".round-card pre")).toBeNull();
    fireEvent.click(screen.getByText("Cancelled rounds (1)"));
    const cancelledRound = screen.getByRole("article", { name: "Round 2" });
    expect(cancelledRound).toHaveTextContent("Cancelled");
    expect(cancelledRound).toHaveTextContent("PDL1");
    expect(cancelledRound).toHaveTextContent("2026-10-24T12:00:00+00:00");
    expect(cancelledRound.querySelector("details")).not.toHaveAttribute("open");
    fireEvent.click(within(cancelledRound).getByText("Technical details"));
    expect(cancelledRound).toHaveTextContent("plan_child");
    expect(cancelledRound).toHaveTextContent("previous_round");
    expect(cancelledRound.querySelector("pre")).toBeNull();
    fetchMock.mockRestore();
  });

  it("shows a cancellation refusal without removing the round", async () => {
    const journey = {
      journey_id: "journey_saved",
      request_id: "request_1",
      request_revision: 1,
      plan_id: "plan_child",
      plan_revision: 1,
      gpu_execution: "disabled",
      approved: false,
      specialist_agents_executed: false,
      knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
      hermes: { status: "not_applicable", detail: "Hermes was not called." },
      stages: [
        {
          stage_id: "generation_planning",
          status: "completed",
          implementation_kind: "deterministic",
          implementation: "GenerationPlanService.draft",
          tools: [],
          agent_executed: false,
          evidence_references: [],
          output: null,
          limitations: [],
        },
      ],
      rounds: [
        {
          round_id: "round_2",
          ordinal: 2,
          active: true,
          parent_round_id: "round_1",
          status: "open",
          cancellable: true,
          outcome_label: "No launch recorded.",
          target: "PDL1",
          candidate_count: 0,
        },
      ],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).endsWith("/cancellation")) {
        return {
          ok: false,
          status: 409,
          json: async () => ({
            status: "round_launch_consumed",
            detail: "This round already consumed a launch. Cancellation does not stop that execution.",
          }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => journey } as Response;
    });
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    fireEvent.click(await screen.findByText("Technical details"));
    expect(screen.getByText("Hide technical details")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel round" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This round already consumed a launch. Cancellation does not stop that execution.",
    );
    expect(screen.getByRole("article", { name: "Round 2" })).toHaveTextContent("No launch recorded.");
    expect(screen.getByRole("button", { name: "Cancel round" })).toBeInTheDocument();
    expect(screen.getByText("Hide technical details")).toBeInTheDocument();
    expect(screen.queryByText(/Cancelled rounds/)).not.toBeInTheDocument();
  });

  it("keeps the executed round visible and files cancelled rounds together", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_parent",
        plan_revision: 2,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: false,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "generation",
            status: "completed",
            implementation_kind: "adapter",
            implementation: "GenerationPlanService.launch_apptainer_demonstration",
            tools: [],
            agent_executed: false,
            evidence_references: [
              { kind: "journey_attempt", journey_id: "journey_saved", attempt_id: "attempt.0fb068b6" },
            ],
            output: { attempt_id: "attempt.0fb068b6", designs_directory: "/evidence/attempt.0fb068b6/designs" },
            limitations: [],
          },
        ],
        rounds: [
          {
            round_id: "round_1",
            ordinal: 1,
            active: false,
            status: "open",
            cancellable: false,
            child_round_id: "round_4",
            outcome_label: "Budget exhausted without an accepted candidate.",
            target: "PDL1",
            candidate_count: 0,
            execution_completed: true,
            attempt_id: "attempt.0fb068b6",
            execution: {
              attempt_id: "attempt.0fb068b6",
              attempt_directory: "/evidence/attempt.0fb068b6",
              designs_directory: "/evidence/attempt.0fb068b6/designs",
              result_link: { journey_id: "journey_saved", attempt_id: "attempt.0fb068b6", candidate_id: null },
            },
          },
          cancelledRound(2, "round_1"),
          cancelledRound(3, "round_2"),
          { ...cancelledRound(4, "round_3"), active: true },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    expect(await screen.findByRole("article", { name: "Round 1" })).toHaveTextContent(
      "Budget exhausted without an accepted candidate.",
    );
    const cancelledSection = screen.getByRole("group", { name: "Cancelled rounds (3)" });
    expect(cancelledSection).not.toHaveAttribute("open");
    expect(screen.getByRole("button", { name: "Prepare another round" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel round" })).not.toBeInTheDocument();
    expect(cancelledSection).toContainElement(screen.getByRole("article", { name: "Round 4" }));
    expect(screen.getByRole("article", { name: "Round 1" }).closest(".cancelled-rounds")).toBeNull();
    fireEvent.click(screen.getByText("Cancelled rounds (3)"));
    const round4 = screen.getByRole("article", { name: "Round 4" });
    expect(round4).toHaveTextContent("Cancelled");
    expect(round4).toHaveTextContent("PDL1");
    expect(round4).toHaveTextContent("2026-10-24T18:04:00+00:00");
    expect(round4).not.toHaveTextContent("Budget usage");
    fireEvent.click(within(screen.getByRole("article", { name: "Round 1" })).getByText("Technical details"));
    expect(screen.getByRole("article", { name: "Round 1" })).toHaveTextContent("/evidence/attempt.0fb068b6/designs");
    expect(screen.getByRole("article", { name: "Round 1" })).toHaveTextContent("attempt.0fb068b6");
    expect(document.querySelector(".round-card pre")).toBeNull();
    expect(document.body.textContent).not.toContain('{"attempt_id"');
  });

  it("labels a recorded budget, a historical proposal, and blocked scoring", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_1",
        request_revision: 1,
        plan_id: "plan_child",
        plan_revision: 4,
        gpu_execution: "disabled",
        approved: true,
        specialist_agents_executed: true,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [],
        rounds: [
          {
            round_id: "round_parent",
            ordinal: 1,
            active: false,
            target: "PDL1",
            outcome_label: "Earlier round.",
          },
          {
            round_id: "round_child",
            parent_round_id: "round_parent",
            ordinal: 2,
            active: true,
            target: "PDL1",
            outcome_label: "Accepted candidate.",
            execution_completed: true,
            sequence_limits: { max_trajectories: 4, num_seqs: 1, max_mpnn_sequences: 1 },
            gpu_proposal: "0",
            gpu_proposal_source: "previous_round",
            parameters: [{ name: "gpu", value: "0", source: "previous_round", provenance: "preserved" }],
            specialist_stages: [
              {
                stage_id: "scoring",
                status: "blocked",
                implementation_kind: "structural_scoring",
                implementation: "BindCraftIpSAEEvidenceProvider",
                tools: [],
                agent_executed: false,
                evidence_references: [],
                output: null,
                limitations: [],
                blocking_reason: "No accepted candidate.",
              },
            ],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const round = await screen.findByRole("article", { name: "Round 2" });
    expect(round).toHaveTextContent("Planned recorded attempted-trajectory budget 4. The executed budget is not recorded.");
    expect(round).not.toHaveTextContent("The trajectory budget was not increased.");
    expect(round).toHaveTextContent("Scoring did not run. No ranking_score was calculated.");
    fireEvent.click(within(round).getByText("Technical details"));
    expect(round).toHaveTextContent("Historical proposal from an earlier round.");
  });

  it("shows queued generation separately from post-processing and does not launch on read", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        ...savedJourney,
        execution: { status: "running", consumed: true, terminal: false, attempt_id: "attempt.new1" },
        rounds: [
          {
            round_id: "round_new",
            ordinal: 3,
            active: true,
            status: "open",
            cancellable: false,
            outcome_label: "Running.",
            execution_completed: false,
            execution: {
              status: "running",
              consumed: true,
              terminal: false,
              attempt_id: "attempt.new1",
              candidate_identity: { design: "PDL1_l65_s875688_mpnn1" },
            },
            post_generation: {
              automatic: true,
              status: "queued",
              phase: "generation",
              events: [
                {
                  sequence: 1,
                  at: "2026-10-25T15:00:00+00:00",
                  kind: "dispatch_queued",
                  status: "queued",
                  detail: "The launch identity was saved.",
                },
              ],
            },
            results_publication: null,
            specialist_progress_available: false,
          },
        ],
      }),
    } as Response);
    const view = render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const round = await screen.findByRole("article", { name: "Round 3" });
    expect(round).toHaveTextContent("Generation is queued. Scoring has not started.");
    expect(round).toHaveTextContent("Scientific validation is not established");
    expect(within(round).queryByRole("button", { name: "Prepare next round" })).not.toBeInTheDocument();
    expect(within(round).queryByRole("button", { name: "Record specialist progress" })).not.toBeInTheDocument();
    expect(within(round).queryByRole("button", { name: "Cancel round" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/launch"))).toBe(false);
    const clearInterval = vi.spyOn(window, "clearInterval");
    view.unmount();
    expect(clearInterval).toHaveBeenCalled();
  });

  it("keeps a parent review when the child approval has another digest", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        ...savedJourney,
        plan_id: "plan_child",
        approval: { digest: "child-digest", scope: "recorded_plan" },
        execution_authorization: { digest: "child-digest" },
        events: [
          { kind: "approval_recorded", digest: "parent-digest" },
          { kind: "execution_authorized", digest: "parent-digest" },
          { kind: "approval_recorded", digest: "child-digest" },
        ],
        rounds: [
          {
            round_id: "round_parent",
            ordinal: 10,
            active: false,
            status: "open",
            digest: "parent-digest",
            plan_id: "plan_parent",
            plan_revision: 4,
            recorded_review_status: "not_recorded",
            execution_authorization_status: "not_authorized",
            execution_completed: true,
            attempt_id: "attempt.parent",
            approved_gpu: null,
            launch_gpu_index: 0,
            gpu_proposal: "0",
            gpu_proposal_source: "previous_round",
            execution: {
              status: "verified",
              consumed: true,
              terminal: true,
              attempt_id: "attempt.parent",
              digest: "parent-digest",
              gpu_index: 0,
              candidate_identity: { design: "PDL1_l65_s875688_mpnn1" },
              budget_usage: { requested_attempted_trajectories: 4 },
            },
            results_publication: { status: "verified" },
            specialist_stages: [
              {
                stage_id: "generation",
                status: "completed",
                implementation: "ProteinGenerationAgent",
                implementation_kind: "adapter",
                tools: [],
                agent_executed: true,
                evidence_references: [],
                output: { protein_generation_agent_executed: true },
                limitations: [],
                contribution: "ProteinGenerationAgent normalized saved design PDL1_l65_s875688_mpnn1.",
              },
            ],
          },
          {
            round_id: "round_child",
            ordinal: 11,
            active: true,
            status: "open",
            parent_round_id: "round_parent",
            digest: "child-digest",
            plan_id: "plan_child",
            recorded_review_status: "recorded_plan",
            execution_authorization_status: "authorized",
            approval: { digest: "child-digest", scope: "recorded_plan" },
            execution_authorization: { digest: "child-digest" },
            execution_completed: false,
            sequence_limits: { max_trajectories: 4, num_seqs: 1, max_mpnn_sequences: 1 },
            specialist_stages: [],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const parent = await screen.findByRole("article", { name: "Round 10" });
    const child = screen.getByRole("article", { name: "Round 11" });
    expect(parent).toHaveTextContent("recorded_plan");
    expect(parent).toHaveTextContent("authorized");
    expect(parent).toHaveTextContent("Saved approval event matches this round's digest.");
    expect(parent).toHaveTextContent("attempt.parent");
    expect(parent).toHaveTextContent("Executed attempted-trajectory budget 4.");
    expect(parent).toHaveTextContent("BindCraft produced the artifacts on the GPU. ProteinGenerationAgent normalized those saved artifacts for this round.");
    expect(parent).not.toHaveTextContent("ProteinGenerationAgent did not run");
    expect(parent).not.toHaveTextContent("Choose an explicit index");
    expect(child).toHaveTextContent("recorded_plan");
    expect(child).toHaveTextContent("Specialists have not run for this round.");
    expect(child).toHaveTextContent("Planned recorded attempted-trajectory budget 4.");
    expect(child).not.toHaveTextContent("BindCraft produced the artifacts");
    const link = within(parent).getByRole("link", { name: "View results" });
    expect(link).toHaveAttribute("href", expect.stringContaining("attempt=attempt.parent"));
    expect(link).toHaveAttribute("href", expect.stringContaining("candidate=PDL1_l65_s875688_mpnn1"));
  });

  it("binds an approved GPU to the matching plan and leaves an unbound round absent", async () => {
    const digest = "2153b8652fe3a88562ff7c49c4c8a09caee6f789de4ff0e878dd3a786a55abb2";
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        ...savedJourney,
        plan_id: "plan_child",
        plan_revision: 2,
        execution_review: {
          plan_id: "plan_child",
          revision: 2,
          digest,
          resource_budget: { gpu: "0" },
          questions: [],
          unresolved_blockers: [],
          recorded_plan_approval: true,
          execution_authorized: true,
          input_identities: null,
          input_registration: "verified",
        },
        rounds: [
          {
            round_id: "round_parent",
            ordinal: 10,
            active: false,
            status: "open",
            digest: "parent-digest",
            plan_id: "plan_parent",
            plan_revision: 4,
            recorded_review_status: "recorded_plan",
            recorded_review_evidence: "journey_event",
            execution_authorization_status: "authorized",
            approved_gpu: null,
            launch_gpu_index: 0,
            gpu_proposal: "0",
            gpu_proposal_source: "previous_round",
            execution_completed: true,
            attempt_id: "attempt.parent",
          },
          {
            round_id: "round_child",
            ordinal: 11,
            active: true,
            status: "open",
            digest,
            plan_id: "plan_child",
            plan_revision: 2,
            recorded_review_status: "recorded_plan",
            execution_authorization_status: "authorized",
            approved_gpu: null,
            launch_gpu_index: 0,
            gpu_proposal: "0",
            gpu_proposal_source: "previous_round",
            approval: { plan_id: "plan_child", revision: 2, digest, scope: "recorded_plan" },
            execution_authorization: { plan_id: "plan_child", revision: 2, digest },
            execution_completed: true,
          },
          {
            round_id: "round_divergent",
            ordinal: 12,
            active: false,
            status: "open",
            digest: "other-digest",
            plan_id: "plan_child",
            plan_revision: 2,
            approved_gpu: null,
            launch_gpu_index: 0,
            gpu_proposal: "0",
            gpu_proposal_source: "previous_round",
            approval: { plan_id: "plan_child", revision: 2, digest: "other-digest", scope: "recorded_plan" },
            execution_completed: true,
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const parent = await screen.findByRole("article", { name: "Round 10" });
    const child = screen.getByRole("article", { name: "Round 11" });
    const divergent = screen.getByRole("article", { name: "Round 12" });
    expect(child).toHaveTextContent("Approved GPU 0.");
    expect(child).toHaveTextContent("Historical GPU proposal 0. It is not this round's approved GPU.");
    expect(child).toHaveTextContent("Launch GPU 0.");
    expect(parent).toHaveTextContent("Approved GPU is not recorded on this round.");
    expect(parent).not.toHaveTextContent("Approved GPU 0.");
    expect(divergent).toHaveTextContent("Approved GPU is not recorded on this round.");
  });

  it("leads a completed round with the result, then the next round and technical details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        ...savedJourney,
        gpu_execution: "disabled",
        execution_review: {
          plan_id: "plan_saved",
          revision: 1,
          digest: "a".repeat(64),
          recorded_plan_approval: true,
          execution_authorized: true,
          unresolved_blockers: [],
          configuration_blockers: ["demonstration_not_configured"],
          questions: [],
          resource_budget: { max_trajectories: 4, gpu: "0", cpu_count: 4, memory_mib: 32768 },
          server_execution: "disabled",
        },
        rounds: [
          {
            round_id: "round_11",
            ordinal: 11,
            active: true,
            status: "open",
            target: "PDL1",
            candidate_count: 1,
            recorded_review_status: "recorded_plan",
            execution_authorization_status: "authorized",
            execution_completed: true,
            successful_candidate_generation: true,
            attempt_id: "attempt.fff8b608",
            results_publication: { status: "verified" },
            execution: {
              attempt_id: "attempt.fff8b608",
              consumed: true,
              candidate_identity: { design: "PDL1_l65_s875688_mpnn1" },
            },
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const journey = await screen.findByRole("region", { name: "Design journey" });
    const round = await screen.findByRole("article", { name: "Round 11" });
    expect(round).toHaveTextContent("Round 11 · Completed");
    expect(round).toHaveTextContent("PDL1 · 1 candidate · review and authorization recorded");
    expect(within(round).getByRole("link", { name: "View results" })).toBeInTheDocument();
    expect(within(round).getByRole("button", { name: "Prepare next round" })).toBeInTheDocument();
    expect(within(round).getByText("Technical details")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("New executions are disabled. Saved results remain available.");
    const journeyText = journey.textContent ?? "";
    expect(journeyText.indexOf("Round 11 · Completed")).toBeLessThan(journeyText.indexOf("View results"));
    expect(journeyText.indexOf("View results")).toBeLessThan(journeyText.indexOf("Prepare next round"));
    expect(journeyText).not.toContain("Next:");
    expect(screen.queryByRole("region", { name: "Next step" })).not.toBeInTheDocument();
    fireEvent.click(within(round).getByText("Technical details"));
    expect(within(round).getByRole("region", { name: "Plan" })).toBeInTheDocument();
    expect(within(round).getByRole("region", { name: "Approval and authorization" })).toBeInTheDocument();
    expect(within(round).getByRole("region", { name: "Execution" })).toBeInTheDocument();
    expect(within(round).getByRole("region", { name: "Specialists" })).toBeInTheDocument();
  });

  it("shows the saved specialist overlap in details and keeps the recorded times", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        ...savedJourney,
        rounds: [
          {
            round_id: "round_overlap",
            ordinal: 11,
            active: true,
            status: "open",
            execution_completed: true,
            specialist_stages: [
              {
                stage_id: "generation",
                status: "completed",
                implementation: "ProteinGenerationAgent",
                implementation_kind: "adapter",
                tools: [],
                agent_executed: true,
                evidence_references: [],
                output: { protein_generation_agent_executed: true },
                limitations: [],
                started_at: "2026-10-25T16:54:42.359488+00:00",
                completed_at: "2026-10-25T16:54:42.376214+00:00",
              },
              {
                stage_id: "scoring",
                status: "completed",
                implementation: "BindCraftIpSAEEvidenceProvider",
                implementation_kind: "structural_scoring",
                tools: [],
                agent_executed: true,
                evidence_references: [],
                output: null,
                limitations: [],
                started_at: "2026-10-25T16:54:42.289138+00:00",
                completed_at: "2026-10-25T16:54:42.359529+00:00",
              },
              {
                stage_id: "ranking",
                status: "completed",
                implementation: "ProteinRankingAgentV1",
                implementation_kind: "deterministic",
                tools: [],
                agent_executed: true,
                evidence_references: [],
                output: null,
                limitations: [],
                started_at: "2026-10-25T16:54:42.359552+00:00",
                completed_at: "2026-10-25T16:54:42.359576+00:00",
              },
            ],
          },
        ],
      }),
    } as Response);
    render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    const round = await screen.findByRole("article", { name: "Round 11" });
    const details = within(round).getByText("Technical details").closest("details");
    expect(details).toHaveTextContent("Scoring starts before the recorded normalization start.");
    expect(details).toHaveTextContent("2026-10-25T16:54:42.359488+00:00");
    expect(details).toHaveTextContent("2026-10-25T16:54:42.289138+00:00");
    expect(details).toHaveTextContent("These saved timestamps were not rewritten.");
    expect(round.textContent?.replace(details?.textContent ?? "", "")).not.toContain("Saved timestamps overlap");
  });

  it("names one next step for each professor state", async () => {
    const base = {
      journey_id: "journey_saved",
      request_id: "request_1",
      request_revision: 1,
      plan_id: "plan_saved",
      plan_revision: 1,
      gpu_execution: "disabled",
      approved: false,
      specialist_agents_executed: false,
      knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
      hermes: { status: "not_applicable", detail: "Hermes is not an action." },
      stages: [
        {
          stage_id: "generation_planning",
          status: "completed",
          implementation_kind: "deterministic",
          implementation: "GenerationPlanService.draft",
          tools: [],
          agent_executed: false,
          evidence_references: [],
          output: {
            digest: "a".repeat(64),
            parameters: {
              target_identity: "PDL1",
              binder_length: 65,
              hotspot_assumption: "interface",
              filter_policy: "no_filters",
              seed: 1,
            },
          },
          limitations: [],
        },
        {
          stage_id: "approval",
          status: "pending",
          implementation_kind: "not_executed",
          implementation: "human_review",
          tools: [],
          agent_executed: false,
          evidence_references: [],
          output: null,
          limitations: [],
        },
      ],
      execution_review: {
        plan_id: "plan_saved",
        revision: 1,
        digest: "a".repeat(64),
        recorded_plan_approval: false,
        execution_authorized: false,
        unresolved_blockers: ["open_questions"],
        configuration_blockers: [],
        questions: [{ field: "seed", question: "Which seed?" }],
        resource_budget: { max_trajectories: 1, gpu: "0", cpu_count: 4, memory_mib: 32768, execution_deadline_seconds: 7200 },
        server_execution: "enabled",
      },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValue({ ok: true, json: async () => base } as Response);
    const { unmount } = render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
    expect(await screen.findByRole("region", { name: "Next step" })).toHaveTextContent("Review and save the plan");
    expect(screen.getByRole("region", { name: "Plan summary" })).toHaveTextContent("PDL1");
    expect(screen.getByRole("region", { name: "Plan summary" })).toHaveTextContent("no_filters");
    expect(screen.getByText("Information is missing. The plan still has open questions.")).toBeInTheDocument();
    unmount();

    const cases: { label: string; patch: Record<string, unknown> }[] = [
      {
        label: "Record approval, then authorize execution",
        patch: { execution_review: { ...base.execution_review, unresolved_blockers: [], questions: [] } },
      },
      {
        label: "Wait for the operator",
        patch: {
          approved: true,
          approval: { status: "recorded_plan", plan_id: "plan_saved", revision: 1, digest: "a".repeat(64) },
          execution_authorization: { status: "authorized" },
          execution_review: {
            ...base.execution_review,
            recorded_plan_approval: true,
            execution_authorized: true,
            unresolved_blockers: [],
            questions: [],
            server_execution: "disabled",
            configuration_blockers: ["demonstration_not_configured"],
          },
        },
      },
    ];
    for (const item of cases) {
      clearJourneyLoadCache();
      fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ...base, ...item.patch }) } as Response);
      const view = render(<DesignJourney requestId="request_1" revision={1} requestText="Design a PDL1 binder." />);
      expect(await screen.findByRole("region", { name: "Next step" })).toHaveTextContent(item.label);
      view.unmount();
    }
    fetchMock.mockRestore();
  });
});

function cancelledRound(ordinal: number, parentRoundId: string) {
  return {
    round_id: `round_${ordinal}`,
    ordinal,
    active: false,
    parent_round_id: parentRoundId,
    status: "cancelled",
    cancellable: false,
    cancelled_at: "2026-10-24T18:04:00+00:00",
    outcome_label: "Cancelled.",
    target: "PDL1",
    candidate_count: 0,
    plan_id: `plan_round_${ordinal}`,
    plan_revision: 1,
  };
}
