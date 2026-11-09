import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { DesignPlan } from "./DesignPlan";

const digest = "b".repeat(64);

function catalog(questions: Array<{ field: string; question: string }>, permitted: boolean) {
  return {
    status: "plans_recorded",
    request_id: "request_shown",
    revision: 2,
    summary: {
      objective: { value: "design a binder", source: "researcher", pending: false },
      target: { value: "PDL1", source: "researcher", pending: false },
      candidates_requested: { value: 1, source: "researcher", pending: false },
      generation_method: { value: "BindCraft", source: "server_configuration", pending: false },
    },
    plans: [
      {
        plan_id: "plan_shown",
        revision: 3,
        design_request_id: "request_shown",
        design_request_revision: 2,
        status: "ready_for_review",
        digest,
        approved: false,
        execution_authorized: false,
        execution_connected: false,
        scientific_execution: "disabled",
        fields: {
          binder_length: 65,
          hotspot_strategy: "none",
          filter_policy: "no_filters",
          filter_policy_mode: "technical_demonstration",
          gpu: "0",
          cpu_count: 4,
          memory_mib: 32768,
          execution_deadline_seconds: 7200,
        },
        questions,
      },
    ],
    proposed_workflow: ["Stop. This draft does not approve, authorize, or start execution."],
    planning_origin: "deterministic_generation_plan",
    model_decided: false,
    agents_executed: false,
    approved: false,
    execution_authorized: false,
    executable: false,
    scientific_execution: "disabled",
    execution_permitted: permitted,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

it("keeps Run design unavailable while the server does not allow execution", async () => {
  vi.mocked(fetch).mockResolvedValue(Response.json(catalog([], false)));
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  expect(await screen.findByRole("region", { name: "Plan" })).toHaveTextContent(
    "Server execution: the current server does not allow execution.",
  );
  expect(screen.getByText("Status: Ready for review")).toBeInTheDocument();
  expect(screen.queryByText("This action will use the GPU.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run design" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Reviewer identification")).not.toBeInTheDocument();
  await waitFor(() => {
    expect(vi.mocked(fetch).mock.calls.map((call) => String(call[0]))).toEqual([
      "/api/generation-plan?request_id=request_shown",
      "/api/generation-plan/suggestion?request_id=request_shown&plan_id=plan_shown",
    ]);
  });
  expect(vi.mocked(fetch).mock.calls.every((call) => (call[1] as RequestInit | undefined)?.method !== "POST")).toBe(true);
});

it("hides Run design while the plan is still being edited", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if ((init?.method ?? "GET") === "GET" && String(input).startsWith("/api/design-journeys/by-request/")) {
      return Response.json({});
    }
    return Response.json(catalog([], true));
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  expect(await screen.findByRole("button", { name: "Run design" })).toBeInTheDocument();
  const editingActions = screen.getByRole("group", { name: "Plan editing" });
  expect(within(editingActions).getByRole("button", { name: "Edit plan" })).toBeInTheDocument();
  expect(within(editingActions).getByRole("button", { name: "Edit plan with agent" }).querySelector("svg")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Edit plan" }));
  expect(screen.queryByRole("button", { name: "Edit plan with agent" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run design" })).not.toBeInTheDocument();
  const actions = screen.getByRole("group", { name: "Plan changes" });
  expect(within(actions).getByRole("button", { name: "Save plan changes" })).toHaveClass("save-plan");
  expect(within(actions).getByRole("button", { name: "Cancel" })).toHaveClass("cancel-plan");
  await user.click(within(actions).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Run design" })).toBeInTheDocument();
});

it("hides Run design while a configuration choice is missing", async () => {
  vi.mocked(fetch).mockResolvedValue(
    Response.json(
      catalog([{ field: "binder_length", question: "How many residues should the binder have?" }], true),
    ),
  );
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  expect(await screen.findByText("Binder length (amino acids) is still to be defined.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run design" })).not.toBeInTheDocument();
});

it("disables Run design while a generation is already running", async () => {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if ((init?.method ?? "GET") === "GET" && url.startsWith("/api/design-journeys/by-request/")) {
      return Response.json({
        execution: { source: "run_design", status: "running", terminal: false },
        live_output: "Stage 1: Test Logits",
      });
    }
    if ((init?.method ?? "GET") === "GET") {
      return Response.json(catalog([], true));
    }
    throw new Error("Run design was submitted");
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  expect(await screen.findByRole("button", { name: "Running…" })).toBeDisabled();
  expect(screen.getByRole("form", { name: "Run design" })).toHaveTextContent("A generation is already running.");
  expect(await screen.findByText("Stage 1: Test Logits")).toBeInTheDocument();
  fireEvent.submit(screen.getByRole("form", { name: "Run design" }));
  expect(vi.mocked(fetch).mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === "POST")).toHaveLength(0);
});

it("shows BindCraft output while Run design is still in progress", async () => {
  const user = userEvent.setup();
  let release: (response: Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if ((init?.method ?? "GET") === "GET" && url.startsWith("/api/design-journeys/by-request/")) {
      return Response.json({ live_output: "Stage 1: Test Logits" });
    }
    if ((init?.method ?? "GET") === "GET") {
      return Response.json(catalog([], true));
    }
    return pending;
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  await user.click(await screen.findByRole("button", { name: "Run design" }));
  expect(await screen.findByText("Stage 1: Test Logits")).toBeInTheDocument();
  expect(screen.getByRole("log", { name: "BindCraft output" })).toHaveTextContent("Stage 1: Test Logits");
  expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
  release(Response.json({
    run: {
      attempt_id: "attempt.shown01",
      status: "stub_diagnosis",
      diagnosis: "generation was not started",
      launched: false,
      automatic_retry: false,
      plan_id: "plan_shown",
      revision: 3,
      digest,
    },
  }));
  expect(await screen.findByText(/Attempt attempt.shown01 is linked to this request/)).toBeInTheDocument();
});

it("runs only the displayed revision and keeps the diagnosis after another click", async () => {
  const user = userEvent.setup();
  const calls: Array<Record<string, unknown>> = [];
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if ((init?.method ?? "GET") === "GET") {
      return Response.json(catalog([], true));
    }
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    calls.push(body);
    return Response.json({
      run: {
        attempt_id: "attempt.shown01",
        status: "stub_diagnosis",
        diagnosis: "generation was not started",
        executor_log: "FATAL: controlled failure",
        launched: false,
        automatic_retry: false,
        plan_id: "plan_shown",
        revision: 3,
        digest,
      },
    });
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  const runButton = await screen.findByRole("button", { name: "Run design" });
  const technical = screen.getByText("Technical details");
  expect(technical.compareDocumentPosition(runButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(runButton).toHaveClass("run-design");
  expect(runButton.nextElementSibling).toHaveTextContent("This action will use the GPU.");
  expect(runButton.parentElement?.lastElementChild).toBe(runButton.nextElementSibling);
  await user.click(runButton);
  expect(await screen.findByText(/Attempt attempt.shown01 is linked to this request/)).toBeInTheDocument();
  const run = screen.getByRole("form", { name: "Run design" });
  expect(run).toHaveTextContent("Status stub_diagnosis.");
  expect(run).toHaveTextContent(/Elapsed \d+s/);
  expect(run).toHaveTextContent("generation was not started.");
  expect(run).toHaveTextContent("Generation was not started.");
  expect(screen.getByText(/Diagnosis generation was not started/)).toBeInTheDocument();
  expect(screen.getByText(/This attempt was not repeated/)).toBeInTheDocument();
  expect(screen.getByText("Executor log")).toBeInTheDocument();
  expect(screen.getByText("FATAL: controlled failure")).toBeInTheDocument();
  expect(calls).toHaveLength(1);
  expect(calls[0]).toEqual({
    request_id: "request_shown",
    request_revision: 2,
    plan_id: "plan_shown",
    revision: 3,
    digest,
    idempotency_key: `run_plan_shown_3_${digest}`,
  });
  expect(calls[0]).not.toHaveProperty("reviewer_id");
  expect(calls[0]).not.toHaveProperty("reason");
  expect(screen.queryByRole("button", { name: "Run design" })).toBeDisabled();
});

it("reloads the current plan when the displayed revision is no longer current", async () => {
  const user = userEvent.setup();
  let reads = 0;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if ((init?.method ?? "GET") === "GET" && url.startsWith("/api/design-journeys/by-request/")) {
      return Response.json({});
    }
    if ((init?.method ?? "GET") === "GET" && url.startsWith("/api/generation-plan/suggestion")) {
      return Response.json({ request_id: "request_shown", suggestions: [] });
    }
    if ((init?.method ?? "GET") === "GET") {
      reads += 1;
      const body = catalog([], true);
      const plan = body.plans[0] as { digest: string; revision: number };
      if (reads > 1) {
        plan.digest = "c".repeat(64);
        plan.revision = 4;
      }
      return Response.json(body);
    }
    return Response.json(
      { status: "plan_revision_conflict", launched: false, automatic_retry: false },
      { status: 409 },
    );
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  await user.click(await screen.findByRole("button", { name: "Run design" }));
  expect(await screen.findByText("The plan changed. Review the current data before running.")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("Digest: " + "c".repeat(64))).toBeInTheDocument());
  expect(screen.queryByText(/Attempt /)).not.toBeInTheDocument();
});

it("applies a selected suggestion without saving or starting a run", async () => {
  const user = userEvent.setup();
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "GET" && url.startsWith("/api/generation-plan/suggestion")) {
      return Response.json({ request_id: "request_shown", suggestions: [] });
    }
    if (method === "GET" && url.startsWith("/api/design-journeys/")) {
      return Response.json({});
    }
    if (method === "GET") {
      return Response.json(catalog([], true));
    }
    if (url === "/api/generation-plan/suggestion") {
      return Response.json({
        suggestion_id: "suggestion_one",
        status: "suggested",
        applicable: true,
        request_id: "request_shown",
        request_revision: 2,
        plan_id: "plan_shown",
        plan_revision: 3,
        digest,
        summary: "One retrieved note mentions residue A20.",
        suggestions: [{
          field: "hotspot_assumption",
          label: "Binding site (hotspots)",
          current: "none",
          suggested: "A20",
          source: "hotspot_identification_agent",
          kind: "agent_recommendation",
          applicable: true,
        }],
        uncertainties: ["Absence of evidence is not a recommendation to leave hotspots undefined."],
        delegation: { agent: "Hotspot Identification Agent", model_call: { model: "scripted-model", total_tokens: 20 } },
        tools: {
          "knowledge.list": { available: true },
          "literature.search": {
            available: true,
            limitation: "The literature search retrieved abstracts only. Full text was not retrieved. These records are not a verified residue map for the selected structure.",
            results: [{
              id: "MED:123",
              title: "PD-L1 binding studies",
              url: "https://europepmc.org/article/MED/123",
              excerpt: "PD-L1 binding site is discussed in this abstract.",
              text_level: "abstract",
              full_text_retrieved: false,
              retrieved_at: "2026-11-08T12:00:00+00:00",
            }],
          },
        },
        region_of_interest: {
          statement: "The retrieved abstract discusses the PD-L1 binding region.",
          applicable_residues: false,
        },
      });
    }
    throw new Error(`unexpected ${method} ${url}`);
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  expect(await screen.findByRole("button", { name: "Run design" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Edit plan with agent" }));
  expect(await screen.findByText(/One retrieved note mentions residue A20/)).toBeInTheDocument();
  expect(screen.getByText(/The literature search retrieved abstracts only/)).toBeInTheDocument();
  expect(screen.getByText(/MED:123: PD-L1 binding studies/)).toBeInTheDocument();
  expect(screen.getByText(/This is not a residue recommendation/)).toBeInTheDocument();
  expect(screen.getByText(/Absence of evidence is not a recommendation/)).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Apply selected suggestions" }));
  expect(screen.getByLabelText("Hotspot residues — target PDB numbering")).toHaveValue("A20");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByLabelText("Hotspot residues — target PDB numbering")).not.toBeInTheDocument();
  expect(screen.getByText(/One retrieved note mentions residue A20/)).toBeInTheDocument();
  const runButton = screen.getByRole("button", { name: "Run design" });
  expect(runButton.nextElementSibling).toHaveTextContent("This action will use the GPU.");
  expect(runButton.parentElement).toHaveClass("run-design-action");
  expect(within(screen.getByRole("region", { name: "Configuration suggestion" })).queryByText("This action will use the GPU.")).not.toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some((call) => String(call[0]).includes("/run"))).toBe(false);
});

it("shows a stored budget interruption without treating it as a scientific conclusion", async () => {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.startsWith("/api/design-journeys/")) {
      return Response.json({});
    }
    if (method === "GET" && url.startsWith("/api/generation-plan/suggestion")) {
      return Response.json({
        suggestions: [{
          suggestion_id: "suggestion_budget",
          status: "token_budget_exceeded",
          applicable: false,
          request_id: "request_shown",
          plan_id: "plan_shown",
          plan_revision: 3,
          digest,
          summary: "Insufficient target-specific evidence to recommend hotspots.",
          suggestions: [],
          uncertainties: ["The shared token budget was exhausted."],
          delegation: {
            agent: "Hotspot Identification Agent",
            supervisor_model_call: { executed: true, input_tokens: 361, output_tokens: 37, total_tokens: 398 },
            model_call: { executed: true, input_tokens: 9856, output_tokens: 126, total_tokens: 9982 },
          },
          tools: {
            "literature.search": {
              available: true,
              limitation: "The literature search retrieved abstracts only. Full text was not retrieved. These records are not a verified residue map for the selected structure.",
              results: [{
                id: "MED:41388338",
                title: "PD-1/PD-L1 interaction",
                url: "https://europepmc.org/article/MED/41388338",
                excerpt: "PD-L1 binding interface.",
                text_level: "abstract",
                full_text_retrieved: false,
                retrieved_at: "2026-11-08T19:54:51+00:00",
              }],
            },
          },
        }],
      });
    }
    if (method === "GET") {
      return Response.json(catalog([], true));
    }
    throw new Error(`unexpected ${method} ${url}`);
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  expect(await screen.findByText("Configuration suggestion incomplete — token budget reached")).toBeInTheDocument();
  expect(screen.queryByText("Insufficient target-specific evidence to recommend hotspots.")).not.toBeInTheDocument();
  expect(screen.queryByText("The shared token budget was exhausted.")).not.toBeInTheDocument();
  expect(screen.getByText(/MED:41388338: PD-1\/PD-L1 interaction/)).toBeInTheDocument();
  expect(screen.getByText("These abstracts are not a verified residue map for the selected PDB.")).toBeInTheDocument();
  expect(screen.getByText("Manual plan values were left unchanged.")).toBeInTheDocument();
  expect(screen.getByText("Tokens: Supervisor in 361 out 37 total 398; specialist in 9856 out 126 total 9982")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run design" }).nextElementSibling).toHaveTextContent("This action will use the GPU.");
  expect(vi.mocked(fetch).mock.calls.every((call) => (call[1] as RequestInit | undefined)?.method !== "POST")).toBe(true);
});

it("shows literature, missing residue mapping, and no plan change without character fragments", async () => {
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && url.startsWith("/api/design-journeys/")) {
      return Response.json({});
    }
    if (method === "GET" && url.startsWith("/api/generation-plan/suggestion")) {
      return Response.json({
        suggestions: [{
          suggestion_id: "suggestion_latest",
          status: "not_applicable",
          applicable: false,
          request_id: "request_shown",
          plan_id: "plan_shown",
          plan_revision: 3,
          digest,
          summary: "Insufficient target-specific evidence to recommend hotspots.",
          suggestions: [],
          uncertainties: [
            "Insufficient target-specific evidence to recommend hotspots.",
            "N",
            "o",
            "Earlier generation results were not consulted as evidence for this suggestion.",
          ],
          delegation: {
            agent: "Hotspot Identification Agent",
            supervisor_model_call: { executed: true, input_tokens: 361, output_tokens: 38, total_tokens: 399 },
            model_call: { executed: true, input_tokens: 1784, output_tokens: 89, total_tokens: 1873 },
          },
          tools: {
            "literature.search": {
              available: true,
              cache_hit: true,
              retrieved_at: "2026-11-08T19:54:51.395890+00:00",
              limitation: "The literature search retrieved abstracts only. Full text was not retrieved. These records are not a verified residue map for the selected structure.",
              results: [{
                id: "MED:41388338",
                title: "PD-1/PD-L1 interaction",
                url: "https://europepmc.org/article/MED/41388338",
                excerpt: "PD-L1 binding interface.",
                text_level: "abstract",
                full_text_retrieved: false,
                retrieved_at: "2026-11-08T19:54:51.395890+00:00",
              }],
            },
          },
        }],
      });
    }
    if (method === "GET") {
      return Response.json(catalog([], true));
    }
    throw new Error(`unexpected ${method} ${url}`);
  });
  render(
    <DesignPlan
      selection={{ requestId: "request_shown", revision: 2, message: "#5 design an new binder to PDL1" }}
    />,
  );
  const card = await screen.findByRole("article", { name: "Suggestion suggestion_latest" });
  expect(card).toHaveTextContent(
    "Literature retrieved. No hotspot recommendation could be mapped to this structure. Plan unchanged.",
  );
  expect(within(card).getByText("Stored suggestion is incomplete. Text that was not stored is not shown.")).toBeInTheDocument();
  expect(within(card).getByText("Direct protein-interface evidence")).toBeInTheDocument();
  expect(screen.queryByText("N", { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByText("o", { exact: true })).not.toBeInTheDocument();
  expect(screen.getByText("Literature records were served from cache. Original retrieval: 2026-11-08T19:54:51.395890+00:00.")).toBeInTheDocument();
  expect(screen.getByText(/MED:41388338: PD-1\/PD-L1 interaction/)).toBeInTheDocument();
  expect(screen.getByText(/Earlier generation results were not consulted as evidence for this suggestion/)).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.every((call) => (call[1] as RequestInit | undefined)?.method !== "POST")).toBe(true);
});
