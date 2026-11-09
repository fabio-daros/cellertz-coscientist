import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App, LegacyWorkspace } from "./App";
import { formatCost, type ConversationInstrumentation } from "./contracts";
import { prototypeWorkspace } from "./fixture";
import sessionFixture from "./session-fixture.json";

function answeredResponse(): Response {
  return {
    ok: true,
    json: async () => ({
      status: "answered",
      text: "Recorded ranking_score is not_recorded.",
      session_id: prototypeWorkspace.session_id,
      run_id: "protein_run_pdl1_smoke",
      read_only: true,
      evidence: [
        {
          capability: "get_ranking",
          entity_kind: "candidate",
          entity_id: "bindcraft_PDL1_l65_s585903_mpnn1",
          source: "session",
          attributes: { ranking_score: "not_recorded", ranking_signal: "raw_ipsae" },
        },
      ],
      instrumentation: {
        interaction_id: "interaction_1",
        recorded: false,
        total_tokens: null,
        observation: "no_model_usage",
        event: null,
      },
    }),
  } as Response;
}

const serializedKeys = [
  "agents",
  "artifacts",
  "budget",
  "candidates",
  "draft",
  "evidence",
  "execution_authorized",
  "messages",
  "pipeline",
  "project",
  "prototype_only",
  "runs",
  "session_id",
  "usage",
  "usage_snapshot",
];

function listedDraft(message: string, requestId: string, revision: number): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      status: "drafts_recorded",
      execution_authorized: false,
      executable: false,
      drafts: [
        {
          message,
          execution_authorized: false,
          executable: false,
          draft: { request_id: requestId, revision },
        },
      ],
    }),
  } as Response;
}

function emptyDraftList(): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      status: "drafts_recorded",
      drafts: [],
      execution_authorized: false,
      executable: false,
    }),
  } as Response;
}

function postCalls() {
  return vi.mocked(fetch).mock.calls.filter((call) => call[1]?.method === "POST");
}

describe("protein design prototype", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch");
  });

  it("shows the Cellertz header, an empty main, and the footer", async () => {
    vi.mocked(fetch).mockResolvedValue(emptyDraftList());
    render(<App />);
    expect(screen.getByRole("heading", { name: "AI Co-Scientist platform" })).toBeInTheDocument();
    expect(screen.getByText("Cellertz bio")).toBeInTheDocument();
    expect(screen.getByText("Frontend Prototype")).toBeInTheDocument();
    expect(screen.getByText("AI Agent - Supervisor")).toBeInTheDocument();
    expect(screen.queryByText("PDL1 demonstration")).not.toBeInTheDocument();
    expect(screen.getByText(`© ${new Date().getFullYear()} Cellertz Bio`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Language" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Account" })).toBeDisabled();
    const modules = screen.getByRole("navigation", { name: "Project modules" });
    expect(within(modules).getByRole("button", { name: "Protein Design" })).toBeEnabled();
    expect(
      within(modules).getAllByRole("button").map((item) => (item.textContent ?? "").replace(/\s+/g, " ").trim()),
    ).toEqual(["Protein Design", "Target DiscoveryNot available", "mRNA DesignNot available"]);
    expect(within(modules).getByRole("button", { name: "Protein Design" })).toHaveAttribute("aria-current", "page");
    expect(within(modules).getByRole("button", { name: /Target Discovery/ })).toBeDisabled();
    expect(within(modules).getByRole("button", { name: /mRNA Design/ })).toBeDisabled();
    expect(modules).toHaveTextContent("Not available");
    expect(screen.queryByRole("navigation", { name: "Protein Design" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Plan" })).not.toBeInTheDocument();
    const request = screen.getByRole("form", { name: "Design request" });
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("Select a saved request in Design request.");
    expect(request).toHaveTextContent("What would you like to design?");
    expect(request).not.toHaveTextContent("Describe the target protein, the desired interaction and any constraints.");
    expect(request).not.toHaveTextContent("PDL1");
    expect(screen.getByLabelText("Design intent")).toHaveValue("");
    expect(screen.getByLabelText("Design intent")).toHaveAttribute(
      "placeholder",
      "Describe your protein design objective…",
    );
    expect(screen.getByRole("button", { name: "Save request" })).toBeDisabled();
    expect(screen.queryByText("Saving will be available in the next step.")).not.toBeInTheDocument();
    const sections = screen.getByRole("tablist", { name: "Protein Design" });
    expect(within(sections).getByRole("tab", { name: "Design" })).toHaveAttribute("aria-selected", "true");
    expect(within(sections).getByRole("tab", { name: "Results" })).toHaveAttribute("aria-selected", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Execute" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New request" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Saved requests" })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith("/api/design-draft", { method: "GET" });
    expect(postCalls()).toHaveLength(0);
  });

  it("keeps a design intent across the theme change without saving it", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(emptyDraftList());
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    const save = screen.getByRole("button", { name: "Save request" });
    expect(save).toBeDisabled();
    await user.type(intent, "   ");
    expect(intent).toHaveValue("   ");
    expect(save).toBeDisabled();
    expect(screen.queryByText("Saving will be available in the next step.")).not.toBeInTheDocument();
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    await user.type(intent, "Bind a named target at the chosen site.");
    expect(save).toBeEnabled();
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(screen.getByLabelText("Design intent")).toHaveValue("   Bind a named target at the chosen site.");
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(screen.getByLabelText("Design intent")).toHaveValue("   Bind a named target at the chosen site.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(0);
  });

  function draftResponse(status: string, requestId: string, revision: number, httpStatus = 200) {
    return {
      ok: httpStatus === 200,
      status: httpStatus,
      json: async () => ({
        status,
        text: "A draft revision was created. It is not authorized and not executable.",
        session_id: prototypeWorkspace.session_id,
        execution_authorized: false,
        executable: false,
        changed_fields: ["objective"],
        questions: [{ field: "target_id", question: "Which scientific target should this request use?" }],
        issues: [{ field: "target_id", code: "missing" }],
        draft: {
          request_id: requestId,
          session_id: prototypeWorkspace.session_id,
          revision,
          completeness: "incomplete",
          execution_authorized: false,
          executable: false,
          fields: [
            { name: "target_id", value: null, provenance: "absent" },
            { name: "objective", value: "binder_design", provenance: "user" },
          ],
          issues: [{ field: "target_id", code: "missing" }],
        },
        request: null,
        instrumentation: {
          interaction_id: "interaction_draft",
          recorded: false,
          total_tokens: null,
          observation: "no_model_usage",
          event: null,
        },
      }),
    } as Response;
  }

  it("saves an incomplete design request and keeps the original text", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return emptyDraftList();
      }
      return draftResponse("draft_created", "request_saved", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "  Design a binder for a named target.  ");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(intent).toHaveValue("  Design a binder for a named target.  ");
    expect(screen.queryByText("target_id")).not.toBeInTheDocument();
    expect(screen.queryByText("binder_design")).not.toBeInTheDocument();
    expect(screen.queryByText("request_saved")).not.toBeInTheDocument();
    expect(screen.queryByText("PDL1")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Design a binder for a named target." })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(postCalls()).toHaveLength(1);
    expect(fetch).toHaveBeenCalledWith("/api/design-draft", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Design a binder for a named target.",
        request_id: null,
        revision: null,
      }),
    });
    const urls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("design-journeys"))).toBe(false);
    expect(urls.some((url) => url.includes("attempt-coordinator"))).toBe(false);
    expect(urls.some((url) => url.includes("conversation"))).toBe(false);
    expect(urls.some((url) => url.includes("design-approval"))).toBe(false);
  });

  it("creates another card when the next intent is saved", async () => {
    const user = userEvent.setup();
    const replies = [
      draftResponse("draft_created", "request_one", 1),
      draftResponse("draft_created", "request_two", 1),
    ];
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return emptyDraftList();
      }
      return replies.shift() ?? draftResponse("draft_created", "request_two", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "1 - Design a binder for the first request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    await user.clear(intent);
    await user.type(intent, "3 - Design a binder for the first request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    const cards = screen.getByRole("list", { name: "Saved requests" });
    expect(within(cards).getAllByRole("button")).toHaveLength(2);
    expect(within(cards).getByRole("button", { name: "1 - Design a binder for the first request." })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(postCalls().at(-1)?.[1]).toMatchObject({
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "3 - Design a binder for the first request.",
        request_id: null,
        revision: null,
      }),
    });
    expect(screen.queryByRole("button", { name: "Analyze Request" })).not.toBeInTheDocument();
  });

  it("shows only the supervisor analysis for the selected request", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for a named target.",
            summary: "The request asks for a binder.",
            extracted_questions: [
              {
                field: "target_id",
                question: "Which scientific target should this request use?",
                source: "recorded",
              },
            ],
            supervisor_questions: [
              { question: "Should the binder prefer a particular epitope?", source: "supervisor" },
            ],
            validation: { status: "accepted", findings: [] },
            understood: [{ name: "objective", value: "binder_design" }],
            execution_authorized: false,
            executable: false,
            model_call: {
              executed: true,
              model: "test-model",
              input_tokens: 11,
              output_tokens: 7,
              total_tokens: 18,
            },
            instrumentation: { recorded: true, observation: "recorded", provider: "openrouter", model: "test-model" },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1
          ? emptyDraftList()
          : listedDraft("Design a binder for a named target.", "request_one", 1);
      }
      const body = JSON.parse(String(init?.body));
      if (body.request_id === "request_one") {
        return draftResponse("draft_updated", "request_one", 2);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Analyze Request" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(await screen.findByRole("region", { name: "Agent: Supervisor" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Agent: Supervisor" })).toBeInTheDocument();
    expect(screen.getByText("Analysis of revision 1.")).toBeInTheDocument();
    expect(screen.getByText("The request asks for a binder.")).toBeInTheDocument();
    const review = screen.getByRole("region", { name: "Agent: Supervisor" });
    const usage = within(review).getByRole("group", { name: "Model usage" });
    expect(usage).toHaveTextContent("openrouter");
    expect(usage).toHaveTextContent("model: test-model");
    expect(usage).toHaveTextContent("input: 11");
    expect(usage).toHaveTextContent("output: 7");
    expect(usage).toHaveTextContent("total: 18");
    expect(screen.getByText("The request asks for a binder.")).not.toHaveTextContent("input: 11");
    expect(screen.getByRole("heading", { name: "Recorded gaps" })).toBeInTheDocument();
    expect(screen.getByText("These were already recorded. They are not a finding of the Supervisor.")).toBeInTheDocument();
    expect(screen.getByLabelText("Which scientific target should this request use?")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Supervisor questions" })).toBeInTheDocument();
    expect(screen.getByText("Should the binder prefer a particular epitope?")).toBeInTheDocument();
    expect(screen.queryByLabelText("Should the binder prefer a particular epitope?")).not.toBeInTheDocument();
    expect(screen.queryByText("binder_design")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Generation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Planning" })).not.toBeInTheDocument();
    const analysisCall = vi.mocked(fetch).mock.calls.find((call) => String(call[0]) === "/api/design-supervisor");
    expect(analysisCall?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        request_id: "request_one",
        revision: 1,
        message: "Design a binder for a named target.",
      }),
    });
    await user.type(intent, " edited");
    expect(screen.getByText("This analysis is out of date.")).toBeInTheDocument();
    expect(screen.getByText("Analyze the current revision before answering.")).toBeInTheDocument();
    expect(screen.getByText("The request asks for a binder.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send answers" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Which scientific target should this request use?")).not.toBeInTheDocument();
    await user.clear(intent);
    await user.type(intent, "Design a binder for a named target.");
    expect(screen.queryByText("This analysis is out of date.")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Which scientific target should this request use?"), "PDL1");
    await user.click(screen.getByRole("button", { name: "Send answers" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(screen.getByText("Analysis of revision 1.")).toBeInTheDocument();
    expect(screen.getByText("This analysis is out of date.")).toBeInTheDocument();
    expect(postCalls().at(-1)?.[1]).toMatchObject({
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Design a binder for a named target.",
        request_id: "request_one",
        revision: 1,
        clarifications: [{ field: "target_id", value: "PDL1" }],
      }),
    });
    expect(screen.getByLabelText("Design intent")).toHaveValue("Design a binder for a named target.");
    expect(screen.getByRole("button", { name: "Design a binder for a named target." })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /PDL1/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Which scientific target should this request use?")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter((call) => String(call[0]) === "/api/design-supervisor")).toHaveLength(1);
  });

  it("asks once for a candidate count and keeps that answer off the objective", async () => {
    const user = userEvent.setup();
    const message = "#3 design an new binder to PDL1";
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_new",
            revision: 1,
            message,
            summary: "The researcher wants a new binder to PDL1.",
            extracted_questions: [
              { field: "n_candidates", question: "How many candidates should be requested?", source: "recorded" },
            ],
            supervisor_questions: [
              { question: "How many candidates do you want?", source: "supervisor", field: null },
              { question: "Should the binder prefer a particular epitope?", source: "supervisor", field: null },
            ],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            current: true,
            model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1 ? emptyDraftList() : listedDraft(message, "request_new", 1);
      }
      const body = JSON.parse(String(init?.body));
      if (body.clarifications) {
        return draftResponse("draft_updated", "request_new", 2);
      }
      return draftResponse("draft_created", "request_new", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), message);
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: message }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(await screen.findByLabelText("How many candidates should be requested?")).toBeInTheDocument();
    expect(screen.queryByLabelText("How many candidates do you want?")).not.toBeInTheDocument();
    expect(screen.queryByText("How many candidates do you want?")).not.toBeInTheDocument();
    expect(screen.getByText("Should the binder prefer a particular epitope?")).toBeInTheDocument();
    expect(screen.queryByLabelText("Should the binder prefer a particular epitope?")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("How many candidates should be requested?"), "2");
    await user.click(screen.getByRole("button", { name: "Send answers" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(screen.getByLabelText("Design intent")).toHaveValue(message);
    expect(screen.getAllByRole("button", { name: message })).toHaveLength(1);
    expect(screen.getByText("This analysis is out of date.")).toBeInTheDocument();
    expect(screen.queryByLabelText("How many candidates should be requested?")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send answers" })).not.toBeInTheDocument();
    expect(postCalls().at(-1)?.[1]).toMatchObject({
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message,
        request_id: "request_new",
        revision: 1,
        clarifications: [{ field: "n_candidates", value: "2" }],
      }),
    });
    expect(vi.mocked(fetch).mock.calls.filter((call) => String(call[0]) === "/api/design-supervisor")).toHaveLength(1);
  });

  it("keeps the selected revision when a clarification conflicts", async () => {
    const user = userEvent.setup();
    const message = "#3 design an new binder to PDL1";
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_new",
            revision: 1,
            message,
            summary: "The researcher wants a new binder to PDL1.",
            extracted_questions: [
              { field: "n_candidates", question: "How many candidates should be requested?", source: "recorded" },
            ],
            supervisor_questions: [],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            current: true,
            model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1 ? emptyDraftList() : listedDraft(message, "request_new", 1);
      }
      const body = JSON.parse(String(init?.body));
      if (body.clarifications) {
        return draftResponse("revision_conflict", "request_new", 1, 409);
      }
      return draftResponse("draft_created", "request_new", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), message);
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: message }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    await user.type(await screen.findByLabelText("How many candidates should be requested?"), "2");
    await user.click(screen.getByRole("button", { name: "Send answers" }));
    expect(await screen.findByText("The answers were not saved.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: message })).toHaveLength(1);
    expect(screen.getByLabelText("Design intent")).toHaveValue(message);
    await user.type(screen.getByLabelText("Design intent"), " edited");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(JSON.parse(String(postCalls().at(-1)?.[1]?.body))).toMatchObject({
      request_id: "request_new",
      revision: 1,
      message: `${message} edited`,
    });
  });

  it("does not apply a clarification to the card selected while it is saving", async () => {
    const user = userEvent.setup();
    const first = "#3 design an new binder to PDL1";
    const second = "design an new binder to PDL1";
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_new",
            revision: 1,
            message: first,
            summary: "The researcher wants a new binder to PDL1.",
            extracted_questions: [
              { field: "n_candidates", question: "How many candidates should be requested?", source: "recorded" },
            ],
            supervisor_questions: [],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            current: true,
            model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "drafts_recorded",
            execution_authorized: false,
            executable: false,
            drafts: [
              {
                message: first,
                execution_authorized: false,
                executable: false,
                draft: { request_id: "request_new", revision: 1 },
              },
              {
                message: second,
                execution_authorized: false,
                executable: false,
                draft: { request_id: "request_old", revision: 2 },
              },
            ],
          }),
        } as Response;
      }
      const body = JSON.parse(String(init?.body));
      if (body.clarifications) {
        return pending;
      }
      return draftResponse("draft_created", "request_other", 1);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: first }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    await user.type(await screen.findByLabelText("How many candidates should be requested?"), "2");
    await user.click(screen.getByRole("button", { name: "Send answers" }));
    await user.click(screen.getByRole("button", { name: second }));
    release(draftResponse("draft_updated", "request_new", 2));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Analyze Request" })).toBeEnabled();
    });
    expect(screen.getByLabelText("Design intent")).toHaveValue(second);
    expect(screen.getAllByRole("button", { name: first })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: second })).toHaveLength(1);
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    expect(screen.queryByText("The researcher wants a new binder to PDL1.")).not.toBeInTheDocument();
    expect(JSON.parse(String(postCalls().at(-1)?.[1]?.body))).toMatchObject({
      message: first,
      request_id: "request_new",
      revision: 1,
      clarifications: [{ field: "n_candidates", value: "2" }],
    });
  });

  it("keeps an analysis through a failed call and a later reading of the same revision", async () => {
    const user = userEvent.setup();
    const message = "Design a binder for a named target.";
    let supervisorPosts = 0;
    let reads = 0;
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const analysisBody = (summary: string, analyzedAt: string) => ({
      status: "analyzed",
      agent: "Protein Design Supervisor",
      request_id: "request_one",
      revision: 1,
      message,
      summary,
      analysis_id: analyzedAt,
      analyzed_at: analyzedAt,
      extracted_questions: [
        { field: "target_id", question: "Which scientific target should this request use?", source: "recorded" },
      ],
      supervisor_questions: [],
      validation: { status: "accepted", findings: [] },
      execution_authorized: false,
      executable: false,
      current: true,
      model_call: { executed: true, model: "test-model", input_tokens: 11, output_tokens: 7, total_tokens: 18 },
      instrumentation: { provider: "openrouter", model: "test-model" },
    });
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/api/design-supervisor?")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyses_recorded",
            request_id: "request_one",
            analyses: [],
            execution_authorized: false,
            executable: false,
          }),
        } as Response;
      }
      if (url === "/api/design-supervisor") {
        supervisorPosts += 1;
        if (supervisorPosts === 1) {
          return { ok: true, status: 200, json: async () => analysisBody("The first reading.", "2026-11-06T21:00:00+00:00") } as Response;
        }
        if (supervisorPosts === 2) {
          return pending;
        }
        return { ok: true, status: 200, json: async () => analysisBody("The second reading.", "2026-11-06T21:05:00+00:00") } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1 ? emptyDraftList() : listedDraft(message, "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), message);
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: message }));
    expect(screen.queryByText("The first reading.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(await screen.findByText("The first reading.")).toBeInTheDocument();
    expect(screen.getByText("2026-11-06T21:00:00+00:00")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("model: test-model");
    await user.type(screen.getByLabelText("Which scientific target should this request use?"), "PDL1");
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(screen.getByRole("button", { name: "Analyzing…" })).toBeDisabled();
    expect(screen.getByText("The first reading.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send answers" })).toBeDisabled();
    release({
      ok: false,
      status: 502,
      json: async () => ({}),
    } as Response);
    expect(await screen.findByText("The Supervisor could not analyze this request. No scientific action was taken.")).toBeInTheDocument();
    expect(screen.getByText("The first reading.")).toBeInTheDocument();
    expect(screen.getByLabelText("Which scientific target should this request use?")).toHaveValue("PDL1");
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(await screen.findByText("The second reading.")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Previous analyses" })).toBeInTheDocument();
    expect(screen.queryByText("The first reading.")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Saved model usage" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "06 Nov 2026, 21:00:00 UTC" }));
    expect(screen.getByText("The first reading.")).toBeInTheDocument();
    expect(screen.getAllByText("Analysis of revision 1.")).toHaveLength(2);
    expect(screen.getByRole("group", { name: "Saved model usage" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("model: test-model");
    expect(screen.queryByRole("textbox", { name: "Which scientific target should this request use?" })).toBeInTheDocument();
  });

  it("shows a stored analysis on the selected card and keeps a late reading there", async () => {
    const user = userEvent.setup();
    const first = "#3 design an new binder to PDL1";
    const second = "design an new binder to PDL1";
    const stored = [
      {
        status: "analyzed",
        request_id: "request_new",
        revision: 1,
        message: first,
        summary: "Stored reading for the selected request.",
        analysis_id: "analysis_stored",
        analyzed_at: "2026-11-06T21:33:00+00:00",
        extracted_questions: [],
        supervisor_questions: [{ question: "Should the binder prefer a particular epitope?", source: "supervisor", field: null }],
        validation: { status: "accepted", findings: [] },
        execution_authorized: false,
        executable: false,
        current: true,
        model_call: { executed: true, model: "test-model", input_tokens: 4, output_tokens: 2, total_tokens: 6 },
        instrumentation: { provider: "openrouter", model: "test-model" },
      },
    ];
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/api/design-supervisor?")) {
        const requestId = new URL(url, "http://localhost").searchParams.get("request_id");
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyses_recorded",
            request_id: requestId,
            analyses: requestId === "request_new" ? stored : [],
            execution_authorized: false,
            executable: false,
          }),
        } as Response;
      }
      if (url === "/api/design-supervisor") {
        return pending;
      }
      if ((init?.method ?? "GET") === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "drafts_recorded",
            execution_authorized: false,
            executable: false,
            drafts: [
              {
                message: first,
                execution_authorized: false,
                executable: false,
                draft: { request_id: "request_new", revision: 1 },
              },
              {
                message: second,
                execution_authorized: false,
                executable: false,
                draft: { request_id: "request_old", revision: 2 },
              },
            ],
          }),
        } as Response;
      }
      return draftResponse("draft_created", "request_other", 1);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: first }));
    expect(await screen.findByText("Stored reading for the selected request.")).toBeInTheDocument();
    expect(screen.getByText("2026-11-06T21:33:00+00:00")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("input: 4");
    expect(screen.getByText("Should the binder prefer a particular epitope?")).toBeInTheDocument();
    expect(screen.queryByLabelText("Should the binder prefer a particular epitope?")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(screen.getByRole("button", { name: "Analyzing…" })).toBeDisabled();
    expect(screen.getByText("Stored reading for the selected request.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: second }));
    expect(screen.queryByText("Stored reading for the selected request.")).not.toBeInTheDocument();
    expect(screen.queryByText("input: 4")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Design intent")).toHaveValue(second);
    release({
      ok: true,
      status: 200,
      json: async () => ({
        status: "analyzed",
        agent: "Protein Design Supervisor",
        request_id: "request_new",
        revision: 1,
        message: first,
        summary: "Late reading that belongs to the first request.",
        analysis_id: "analysis_late",
        analyzed_at: "2026-11-06T21:40:00+00:00",
        extracted_questions: [],
        supervisor_questions: [],
        validation: { status: "accepted", findings: [] },
        execution_authorized: false,
        executable: false,
        current: true,
        model_call: { executed: true, model: "test-model", input_tokens: 9, output_tokens: 3, total_tokens: 12 },
        instrumentation: { provider: "openrouter", model: "test-model" },
      }),
    } as Response);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Analyze Request" })).toBeEnabled();
    });
    expect(screen.queryByText("Late reading that belongs to the first request.")).not.toBeInTheDocument();
    expect(screen.queryByText("input: 9")).not.toBeInTheDocument();
    stored.unshift({
      status: "analyzed",
      request_id: "request_new",
      revision: 1,
      message: first,
      summary: "Late reading that belongs to the first request.",
      analysis_id: "analysis_late",
      analyzed_at: "2026-11-06T21:40:00+00:00",
      extracted_questions: [],
      supervisor_questions: [],
      validation: { status: "accepted", findings: [] },
      execution_authorized: false,
      executable: false,
      current: true,
      model_call: { executed: true, model: "test-model", input_tokens: 9, output_tokens: 3, total_tokens: 12 },
      instrumentation: { provider: "openrouter", model: "test-model" },
    });
    await user.click(screen.getByRole("button", { name: first }));
    expect(await screen.findByText("Late reading that belongs to the first request.")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Previous analyses" })).toBeInTheDocument();
    expect(screen.queryByText("Stored reading for the selected request.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "06 Nov 2026, 21:33:00 UTC" }));
    expect(screen.getByText("Stored reading for the selected request.")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("input: 9");
  });

  it("shows that the Supervisor model was not executed", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      const url = String(_input);
      if (url === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "supervisor_unavailable",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for a named target.",
            summary: "The Supervisor model is not configured. No analysis was substituted.",
            extracted_questions: [],
            supervisor_questions: [],
            validation: { status: "not_run", findings: [] },
            execution_authorized: false,
            executable: false,
            model_call: {
              executed: false,
              model: "Model not run",
              input_tokens: "Model not run",
              output_tokens: "Model not run",
              total_tokens: 0,
            },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1
          ? emptyDraftList()
          : listedDraft("Design a binder for a named target.", "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(
      await screen.findByText("The Supervisor model is not configured. No analysis was substituted."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Agent: Supervisor" })).not.toBeInTheDocument();
    const usage = screen.getByRole("group", { name: "Model usage" });
    expect(usage).not.toHaveTextContent("openrouter");
    expect(usage).not.toHaveTextContent("input: 0");
    expect(usage).not.toHaveTextContent("total: 0");
  });

  it("shows an unavailable token count as not informed", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      const url = String(_input);
      if (url === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for a named target.",
            summary: "The request asks for a binder.",
            extracted_questions: [],
            supervisor_questions: [],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            model_call: {
              executed: true,
              model: "served-model",
              input_tokens: 11,
              output_tokens: "Not reported",
              total_tokens: null,
            },
            instrumentation: { provider: "openrouter", model: "served-model" },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1
          ? emptyDraftList()
          : listedDraft("Design a binder for a named target.", "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    const usage = await screen.findByRole("group", { name: "Model usage" });
    expect(usage).toHaveTextContent("openrouter");
    expect(usage).toHaveTextContent("model: served-model");
    expect(usage).toHaveTextContent("input: 11");
    expect(usage).toHaveTextContent("output: Not reported");
    expect(usage).toHaveTextContent("total: Not reported");
    expect(screen.queryByText(/total 0/)).not.toBeInTheDocument();
  });

  it("shows a server-configured generation method without a generator field", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      const url = String(_input);
      if (url === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for PDL1.",
            summary: "The saved design request text states the objective.",
            extracted_questions: [
              {
                field: "generator",
                question: "Which generator should be used?",
                source: "recorded",
              },
              {
                field: "n_candidates",
                question: "How many candidates should be requested?",
                source: "recorded",
              },
            ],
            supervisor_questions: [],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            model_call: {
              executed: true,
              model: "openai/gpt-4.1-mini",
              input_tokens: 278,
              output_tokens: 28,
              total_tokens: 306,
            },
            instrumentation: { provider: "openrouter", model: "openai/gpt-4.1-mini" },
            configured_choices: [
              {
                field: "generator",
                value: "bindcraft",
                text: "Generation method: BindCraft",
                source: "server_configuration",
              },
            ],
            generator_configuration_limitation: null,
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1 ? emptyDraftList() : listedDraft("Design a binder for PDL1.", "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for PDL1.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for PDL1." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    const review = await screen.findByRole("region", { name: "Agent: Supervisor" });
    expect(review).toHaveTextContent("Generation method: BindCraft");
    expect(review).not.toHaveTextContent("Which generator should be used?");
    expect(screen.getByLabelText("How many candidates should be requested?")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("openrouter");
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("model: openai/gpt-4.1-mini");
    expect(screen.getByRole("group", { name: "Model usage" })).toHaveTextContent("input: 278");
  });

  it("reports a missing generator configuration without assuming BindCraft", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      const url = String(_input);
      if (url === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for PDL1.",
            summary: "The saved design request text states the objective.",
            extracted_questions: [
              {
                field: "generator",
                question: "Which generator should be used?",
                source: "recorded",
              },
            ],
            supervisor_questions: [],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            model_call: {
              executed: true,
              model: "openai/gpt-4.1-mini",
              input_tokens: 10,
              output_tokens: 4,
              total_tokens: 14,
            },
            configured_choices: [],
            generator_configuration_limitation:
              "The server configuration does not name a generation method for this PDL1 request. BindCraft was not assumed.",
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1 ? emptyDraftList() : listedDraft("Design a binder for PDL1.", "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for PDL1.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for PDL1." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    const review = await screen.findByRole("region", { name: "Agent: Supervisor" });
    expect(review).toHaveTextContent("BindCraft was not assumed.");
    expect(review).not.toHaveTextContent("Generation method: BindCraft");
    expect(screen.getByLabelText("Which generator should be used?")).toBeInTheDocument();
  });

  it("refuses a stale selected revision before calling the Supervisor", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        throw new Error("supervisor was called");
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1 ? emptyDraftList() : listedDraft("A newer saved revision.", "request_one", 3);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(
      await screen.findByText("The analysis was not run. The selected revision is not the current revision."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Analysis of revision 3.")).not.toBeInTheDocument();
    await user.type(intent, " more");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(JSON.parse(String(postCalls().at(-1)?.[1]?.body))).toMatchObject({
      request_id: "request_one",
      revision: 1,
      message: "Design a binder for a named target. more",
    });
  });

  it("does not show an in-flight analysis on another card", async () => {
    const user = userEvent.setup();
    let reads = 0;
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        return pending;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1
          ? emptyDraftList()
          : listedDraft("Design a binder for the first request.", "request_one", 1);
      }
      const body = JSON.parse(String(init?.body)) as { message?: string };
      if (body.message?.includes("second")) {
        return draftResponse("draft_created", "request_two", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for the first request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.clear(intent);
    await user.type(intent, "Design a binder for the second request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(screen.getByRole("button", { name: "Design a binder for the first request." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    await waitFor(() => {
      expect(vi.mocked(fetch).mock.calls.some((call) => String(call[0]) === "/api/design-supervisor")).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: "Design a binder for the second request." }));
    release({
      ok: true,
      status: 200,
      json: async () => ({
        status: "analyzed",
        agent: "Protein Design Supervisor",
        request_id: "request_one",
        revision: 1,
        message: "Design a binder for the first request.",
        summary: "First request summary.",
        extracted_questions: [],
        supervisor_questions: [{ question: "Which epitope?", source: "supervisor" }],
        validation: { status: "accepted", findings: [] },
        execution_authorized: false,
        executable: false,
        current: true,
        model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
      }),
    } as Response);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Analyze Request" })).toBeEnabled();
    });
    expect(screen.getByLabelText("Design intent")).toHaveValue("Design a binder for the second request.");
    expect(screen.queryByText("First request summary.")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Which epitope?")).not.toBeInTheDocument();
  });

  it("keeps an edited request from sending the analysis that was already running", async () => {
    const user = userEvent.setup();
    let reads = 0;
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        return pending;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1
          ? emptyDraftList()
          : listedDraft("Design a binder for a named target.", "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    await waitFor(() => {
      expect(vi.mocked(fetch).mock.calls.some((call) => String(call[0]) === "/api/design-supervisor")).toBe(true);
    });
    await user.type(intent, " edited");
    release({
      ok: true,
      status: 200,
      json: async () => ({
        status: "analyzed",
        agent: "Protein Design Supervisor",
        request_id: "request_one",
        revision: 1,
        message: "Design a binder for a named target.",
        summary: "The researcher wants a binder and has not named the target.",
        extracted_questions: [
          { field: "target_id", question: "Which scientific target should this request use?", source: "recorded" },
        ],
        supervisor_questions: [],
        validation: { status: "accepted", findings: [] },
        execution_authorized: false,
        executable: false,
        current: true,
        model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
      }),
    } as Response);
    expect(await screen.findByText("This analysis is out of date.")).toBeInTheDocument();
    expect(screen.getByText("Analysis of revision 1.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send answers" })).not.toBeInTheDocument();
    expect(screen.getByText("The researcher wants a binder and has not named the target.")).toBeInTheDocument();
  });

  it("does not adopt a revision saved while the analysis is running", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).startsWith("/api/design-supervisor?")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyses_recorded",
            request_id: "request_one",
            analyses: [],
            execution_authorized: false,
            executable: false,
          }),
        } as Response;
      }
      if (String(input) === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for a named target.",
            summary: "The researcher wants a binder and has not named the target.",
            extracted_questions: [],
            supervisor_questions: [{ question: "Which epitope should the binder prefer?", source: "supervisor" }],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            current: true,
            model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          }),
        } as Response;
      }
      if (String(input).startsWith("/api/generation-plan")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "plans_recorded",
            request_id: "request_one",
            revision: 1,
            summary: null,
            plans: [],
            proposed_workflow: [],
            planning_origin: "deterministic_generation_plan",
            model_decided: false,
            agents_executed: false,
            approved: false,
            execution_authorized: false,
            executable: false,
            scientific_execution: "disabled",
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        if (reads === 1) {
          return emptyDraftList();
        }
        if (reads === 2) {
          return listedDraft("Design a binder for a named target.", "request_one", 1);
        }
        return listedDraft("Design a binder for a named target.\n1", "request_one", 3);
      }
      return draftResponse("draft_updated", "request_one", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(await screen.findByText("Analysis of revision 1.")).toBeInTheDocument();
    expect(screen.getByText("This analysis is out of date.")).toBeInTheDocument();
    expect(screen.queryByText("Analysis of revision 3.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send answers" })).not.toBeInTheDocument();
    await user.type(intent, " more");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(JSON.parse(String(postCalls().at(-1)?.[1]?.body))).toMatchObject({
      request_id: "request_one",
      revision: 1,
    });
  });

  it("does not send questions from an analysis the server marked historical", async () => {
    const user = userEvent.setup();
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/design-supervisor") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "analyzed",
            agent: "Protein Design Supervisor",
            request_id: "request_one",
            revision: 1,
            message: "Design a binder for a named target.",
            summary: "The researcher wants a binder and has not named the target.",
            extracted_questions: [],
            supervisor_questions: [{ question: "Which epitope should the binder prefer?", source: "supervisor" }],
            validation: { status: "accepted", findings: [] },
            execution_authorized: false,
            executable: false,
            current: false,
            model_call: { executed: true, model: "test-model", input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          }),
        } as Response;
      }
      if ((init?.method ?? "GET") === "GET") {
        reads += 1;
        return reads === 1
          ? emptyDraftList()
          : listedDraft("Design a binder for a named target.", "request_one", 1);
      }
      return draftResponse("draft_created", "request_one", 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "Design a binder for a named target." }));
    await user.click(screen.getByRole("button", { name: "Analyze Request" }));
    expect(await screen.findByText("Analysis of revision 1.")).toBeInTheDocument();
    expect(screen.getByText("This analysis is out of date.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send answers" })).not.toBeInTheDocument();
  });

  it("keeps saving after the same wording is rejected for the selected request", async () => {
    const user = userEvent.setup();
    const replies = [
      draftResponse("draft_created", "request_one", 1),
      draftResponse("clarification_needed", "request_one", 1),
      draftResponse("draft_created", "request_two", 1),
    ];
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return emptyDraftList();
      }
      return replies.shift() ?? draftResponse("draft_created", "request_two", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "2 - Design a binder for the first request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(screen.getByText("This request is already saved.")).toBeInTheDocument();
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "2 - Design a binder for the first request." }));
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(screen.getByText("This request is already saved.")).toBeInTheDocument();
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    expect(postCalls()).toHaveLength(1);
    await user.clear(intent);
    await user.type(intent, "4 - Design a binder for a different request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(screen.queryByText("The request was not saved.")).not.toBeInTheDocument();
    const cards = screen.getByRole("list", { name: "Saved requests" });
    expect(within(cards).getAllByRole("button")).toHaveLength(2);
    expect(postCalls().at(-1)?.[1]).toMatchObject({
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "4 - Design a binder for a different request.",
        request_id: null,
        revision: null,
      }),
    });
  });

  it("shows Saving and ignores a second submit until the first finishes", async () => {
    const user = userEvent.setup();
    let release: (response: Response) => void = () => {};
    vi.mocked(fetch).mockImplementation((_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return Promise.resolve(emptyDraftList());
      }
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    const saving = screen.getByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "Design request" }));
    expect(postCalls()).toHaveLength(1);
    release(draftResponse("draft_created", "request_saved", 1));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save request" })).toBeEnabled();
  });

  it("sends the saved revision when the request changes", async () => {
    const user = userEvent.setup();
    const replies = [
      draftResponse("draft_created", "request_saved", 1),
      draftResponse("draft_updated", "request_saved", 2),
    ];
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return emptyDraftList();
      }
      return replies.shift() ?? draftResponse("draft_updated", "request_saved", 2);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Design a binder for a named target." }));
    await user.type(intent, " Add a length constraint.");
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(intent).toHaveValue("Design a binder for a named target. Add a length constraint.");
    expect(postCalls().at(-1)).toEqual([
      "/api/design-draft",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          session_id: prototypeWorkspace.session_id,
          message: "Design a binder for a named target. Add a length constraint.",
          request_id: "request_saved",
          revision: 1,
        }),
      },
    ]);
  });

  it("keeps the text and allows another attempt when saving fails", async () => {
    const user = userEvent.setup();
    let posts = 0;
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return emptyDraftList();
      }
      posts += 1;
      if (posts === 1) {
        throw new Error("network down");
      }
      return draftResponse("draft_created", "request_saved", 1);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("The request was not saved.")).toBeInTheDocument();
    expect(intent).toHaveValue("Design a binder for a named target.");
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save request" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(screen.queryByText("The request was not saved.")).not.toBeInTheDocument();
    expect(fetch).toHaveBeenLastCalledWith("/api/design-draft", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Design a binder for a named target.",
        request_id: null,
        revision: null,
      }),
    });
  });

  it("does not adopt a conflicting revision on the next attempt", async () => {
    const user = userEvent.setup();
    const replies = [
      draftResponse("draft_created", "request_saved", 1),
      draftResponse("revision_conflict", "request_saved", 3, 409),
      draftResponse("revision_conflict", "request_saved", 3, 409),
    ];
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return emptyDraftList();
      }
      return replies.shift() ?? draftResponse("revision_conflict", "request_saved", 3, 409);
    });
    render(<App />);
    const intent = screen.getByLabelText("Design intent");
    await user.type(intent, "Design a binder for a named target.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Design a binder for a named target." }));
    await user.type(intent, " Revise the constraint.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("The request was not saved.")).toBeInTheDocument();
    expect(intent).toHaveValue("Design a binder for a named target. Revise the constraint.");
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("The request was not saved.")).toBeInTheDocument();
    expect(screen.queryByText("Request saved.")).not.toBeInTheDocument();
    expect(intent).toHaveValue("Design a binder for a named target. Revise the constraint.");
    expect(screen.getAllByRole("button", { name: "Design a binder for a named target." })).toHaveLength(1);
    expect(fetch).toHaveBeenLastCalledWith("/api/design-draft", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Design a binder for a named target. Revise the constraint.",
        request_id: "request_saved",
        revision: 1,
      }),
    });
  });

  it("lines saved requests up as cards and continues the one that is selected", async () => {
    const user = userEvent.setup();
    const saved = [
      { message: "Design a binder for the first request.", requestId: "request_alpha", revision: 1 },
      { message: "Design a binder for the second request.", requestId: "request_beta", revision: 2 },
      { message: "Design a binder for the third request.", requestId: "request_gamma", revision: 1 },
      { message: "Design a binder for the fourth request.", requestId: "request_delta", revision: 1 },
    ];
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if ((init?.method ?? "GET") === "GET") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: "drafts_recorded",
            execution_authorized: false,
            executable: false,
            drafts: saved.map((item) => ({
              message: item.message,
              execution_authorized: false,
              executable: false,
              draft: {
                request_id: item.requestId,
                session_id: prototypeWorkspace.session_id,
                revision: item.revision,
                completeness: "incomplete",
                execution_authorized: false,
                executable: false,
                fields: [],
                issues: [],
              },
            })),
          }),
        } as Response;
      }
      const body = JSON.parse(String(init?.body)) as { message: string; request_id: string | null; revision: number | null };
      if (body.request_id === null) {
        return draftResponse("draft_created", "request_epsilon", 1);
      }
      return draftResponse("draft_updated", body.request_id, (body.revision ?? 1) + 1);
    });
    render(<App />);
    const cards = await screen.findByRole("list", { name: "Saved requests" });
    expect(cards).toHaveClass("design-request-cards");
    expect(within(cards).getAllByRole("button")).toHaveLength(4);
    expect(within(cards).queryByRole("button", { name: "Open" })).not.toBeInTheDocument();
    expect(within(cards).queryByText("request_alpha")).not.toBeInTheDocument();
    expect(within(cards).queryByText("binder_design")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Execute" })).not.toBeInTheDocument();

    await user.click(within(cards).getByRole("button", { name: "Design a binder for the second request." }));
    expect(screen.getByLabelText("Design intent")).toHaveValue("Design a binder for the second request.");
    await user.type(screen.getByLabelText("Design intent"), " Continued.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(postCalls().at(-1)?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Design a binder for the second request. Continued.",
        request_id: "request_beta",
        revision: 2,
      }),
    });

    await user.click(screen.getByTitle("Design a binder for the second request. Continued."));
    expect(screen.getByLabelText("Design intent")).toHaveValue("");
    await user.type(screen.getByLabelText("Design intent"), "Design a binder for a separate request.");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByText("Request saved.")).toBeInTheDocument();
    expect(within(cards).getAllByRole("button")).toHaveLength(5);
    expect(postCalls().at(-1)?.[1]).toMatchObject({
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Design a binder for a separate request.",
        request_id: null,
        revision: null,
      }),
    });
    const urls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("design-journeys"))).toBe(false);
    expect(urls.some((url) => url.includes("conversation"))).toBe(false);
    expect(urls.some((url) => url.includes("attempt-coordinator"))).toBe(false);
  });

  it("keeps the previous workspace available without mounting it", async () => {
    const user = userEvent.setup();
    render(<LegacyWorkspace />);
    expect(screen.getByRole("heading", { name: "Research campaigns" })).toBeInTheDocument();
    expect(screen.getAllByText("PDL1 demonstration")).toHaveLength(1);
    expect(screen.queryByText("Read-only")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Session" }));
    expect(screen.getByRole("button", { name: "protein_run_pdl1_smoke" })).toBeInTheDocument();
    expect(screen.getAllByText(/raw ipSAE \(not a probability/).length).toBeGreaterThan(0);
  });

  it("switches the workspace between light and dark", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(emptyDraftList());
    const { unmount } = render(<App />);
    await user.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Switch to light theme" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    unmount();
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("loads the serialized application contract without a model call", () => {
    expect(Object.keys(sessionFixture).sort()).toEqual(serializedKeys);
    expect(prototypeWorkspace).toEqual(sessionFixture);
    expect(prototypeWorkspace.usage).toEqual([]);
    expect(prototypeWorkspace.usage_snapshot.estimated_model_cost).toBeNull();
    expect(prototypeWorkspace.usage_snapshot.scientific_infrastructure_cost).toBeNull();
    expect(prototypeWorkspace.candidates.map((item) => item.candidate_kind)).toEqual([
      "observed_identity",
      "placeholder",
    ]);
    expect(JSON.stringify(prototypeWorkspace.usage)).not.toContain('"none"');
  });

  it("updates the candidate detail when another candidate is selected", async () => {
    const user = userEvent.setup();
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Session" }));
    await user.click(screen.getByRole("button", { name: "fixture_display_slot" }));
    const detail = screen.getByRole("region", { name: "Candidate detail" });
    expect(detail).toHaveTextContent("Display slot only. Not an observed or validated candidate.");
    expect(detail).toHaveTextContent("Kind: placeholder");
    expect(detail).toHaveTextContent("Epistemic status: unknown");
  });

  it("shows observed evidence only for the selected candidate", async () => {
    const user = userEvent.setup();
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Session" }));
    expect(screen.getByRole("tab", { name: "Evidence", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("tabpanel", { name: "Evidence" })).toHaveTextContent(
      "protein_run_pdl1_smoke is an observed computational execution",
    );
    await user.click(screen.getByRole("button", { name: "fixture_display_slot" }));
    expect(screen.getByRole("tabpanel", { name: "Evidence" })).toHaveTextContent(
      "No observed evidence for this candidate.",
    );
    expect(screen.queryByText(/observed computational execution/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Artifacts" }));
    expect(screen.getByRole("tab", { name: "Artifacts", selected: true })).toHaveAttribute(
      "aria-controls",
      "panel-artifacts",
    );
    expect(screen.getByRole("tabpanel", { name: "Artifacts" })).toHaveTextContent(
      "No artifacts for this candidate.",
    );
    expect(screen.queryByText(/artifact:\/\//)).not.toBeInTheDocument();
  });

  it("keeps a local draft from claiming execution", async () => {
    const user = userEvent.setup();
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.type(screen.getByLabelText("Local draft message"), "note a binder idea");
    await user.click(screen.getByRole("button", { name: "Add to local draft" }));
    expect(screen.getByText("note a binder idea")).toBeInTheDocument();
    expect(screen.getByText("Local draft only. Not sent and not executed.")).toBeInTheDocument();
    const stamps = screen.getAllByRole("time").map((item) => item.getAttribute("datetime"));
    const latest = stamps[stamps.length - 1];
    expect(latest).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/);
    expect(latest).not.toBe("local");
  });

  it("does not offer Approve & Run from the pilot overview", () => {
    render(<LegacyWorkspace />);
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Execute" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Research campaigns" })).not.toHaveTextContent(
      "mBER version not recorded",
    );
  });

  it("renders unknown cost as Unknown and makes no network request", async () => {
    const user = userEvent.setup();
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Session" }));
    await user.click(screen.getByRole("tab", { name: "Budget" }));
    expect(screen.getByText("No model call was recorded.")).toBeInTheDocument();
    expect(screen.getAllByText(/Unknown/).length).toBeGreaterThan(0);
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
    expect(formatCost(null, null)).toBe("Unknown");
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(["/api/campaigns"]);
  });

  it("sends the selected session, run, and candidate", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(answeredResponse());
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.type(screen.getByLabelText("Read-only question"), "what is the ranking");
    await user.click(screen.getByRole("button", { name: "Ask read-only" }));
    expect(fetch).toHaveBeenCalledWith("/api/conversation", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        question: "what is the ranking",
        run_id: "protein_run_pdl1_smoke",
        candidate_id: "bindcraft_PDL1_l65_s585903_mpnn1",
      }),
    });
  });

  it("shows a loading state, then the answer and its evidence", async () => {
    const user = userEvent.setup();
    let resolveFetch: (value: Response) => void = () => undefined;
    vi.mocked(fetch).mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.type(screen.getByLabelText("Read-only question"), "what is the ranking");
    const clicked = user.click(screen.getByRole("button", { name: "Ask read-only" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Loading read-only answer.");
    resolveFetch(answeredResponse());
    await clicked;
    const answer = await screen.findByRole("region", { name: "Read-only answer" });
    expect(answer).toHaveTextContent("Mode: read-only.");
    expect(answer).toHaveTextContent("Recorded ranking_score is not_recorded.");
    expect(answer).toHaveTextContent("candidate bindcraft_PDL1_l65_s585903_mpnn1 (session)");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("separates model explanation, retrieved evidence, and fixture output", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "answered",
        text: "The supplied records describe a fixture run.",
        session_id: prototypeWorkspace.session_id,
        run_id: "protein_run_pdl1_smoke",
        read_only: true,
        evidence: [
          {
            capability: "get_semantic_knowledge",
            entity_kind: "knowledge_package",
            entity_id: "protein_scoring_bcma_boltz2_v2",
            source: "second_brain",
            attributes: { status: "unavailable", reason: "not_found" },
          },
          {
            capability: "get_fixture_results",
            entity_kind: "fixture_run",
            entity_id: "run_1",
            source: "fixture",
            attributes: { status: "completed", fixture: "true" },
          },
          {
            capability: "get_fixture_results",
            entity_kind: "stage_records",
            entity_id: "workflow_trace",
            source: "workflow_trace",
            attributes: { status: "unavailable", reason: "no_stage_record" },
          },
        ],
        instrumentation: {
          interaction_id: "interaction_2",
          recorded: true,
          total_tokens: 15,
          observation: "recorded",
          origin: { provider: "openrouter", model: "served-model" },
          event: {
            session_id: prototypeWorkspace.session_id,
            run_id: "protein_run_pdl1_smoke",
            agent_id: "readonly_conversation",
            stage: "conversation",
            provider: "openrouter",
            model: "served-model",
            input_tokens: 11,
            output_tokens: 4,
            cached_input_tokens: null,
            tool_call_count: null,
            estimated_cost: null,
            currency: null,
            started_at: "2026-10-17T12:00:00+00:00",
            completed_at: "2026-10-17T12:00:00+00:00",
            cost_kind: "model",
          },
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    expect(
      screen.getByText(
        "This conversation phrases supplied records. It is not five specialists and it does not start scientific execution. A question may be written in another language. The reply is in English.",
      ),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Read-only question"), "validated knowledge");
    await user.click(screen.getByRole("button", { name: "Ask read-only" }));
    const answer = await screen.findByRole("region", { name: "Read-only answer" });
    expect(answer).toHaveTextContent("Model explanation.");
    expect(answer).toHaveTextContent("Provider openrouter. Model served-model. Total tokens: 15.");
    const retrieved = screen.getByRole("region", { name: "Retrieved evidence" });
    expect(retrieved).toHaveTextContent("knowledge_package protein_scoring_bcma_boltz2_v2 (second_brain; unavailable)");
    const fixture = screen.getByRole("region", { name: "Fixture output" });
    expect(fixture).toHaveTextContent("fixture_run run_1 (fixture; completed)");
    expect(fixture).toHaveTextContent("stage_records workflow_trace (workflow_trace; unavailable)");
    expect(screen.queryByText(/five autonomous specialists/)).not.toBeInTheDocument();
  });

  it("keeps a model explanation when usage is absent or incomplete", async () => {
    const user = userEvent.setup();
    const base = {
      interaction_id: "interaction_origin",
      recorded: false,
      total_tokens: null,
      observation: "no_model_usage",
      event: null,
      origin: null,
    } satisfies ConversationInstrumentation;
    const cases: { instrumentation: ConversationInstrumentation; label: string; usage: string }[] = [
      {
        instrumentation: base,
        label: "Recorded-facts phrasing.",
        usage: "No model usage was recorded.",
      },
      {
        instrumentation: {
          ...base,
          recorded: true,
          total_tokens: 15,
          observation: "recorded",
          origin: { provider: "openrouter", model: "served-model" },
          event: {
            session_id: prototypeWorkspace.session_id,
            run_id: "protein_run_pdl1_smoke",
            agent_id: "readonly_conversation",
            stage: "conversation",
            provider: "openrouter",
            model: "served-model",
            input_tokens: 11,
            output_tokens: 4,
            cached_input_tokens: null,
            tool_call_count: null,
            estimated_cost: null,
            currency: null,
            started_at: "2026-10-17T12:00:00+00:00",
            completed_at: "2026-10-17T12:00:00+00:00",
            cost_kind: "model",
          },
        },
        label: "Model explanation.",
        usage: "Provider openrouter. Model served-model. Total tokens: 15.",
      },
      {
        instrumentation: {
          ...base,
          origin: { provider: "openrouter", model: "served-model" },
        },
        label: "Model explanation.",
        usage: "Provider openrouter. Model served-model. No token usage was reported.",
      },
      {
        instrumentation: {
          ...base,
          recorded: true,
          observation: "recorded",
          origin: { provider: "openrouter", model: "served-model" },
          event: {
            session_id: prototypeWorkspace.session_id,
            run_id: null,
            agent_id: "readonly_conversation",
            stage: "conversation",
            provider: "openrouter",
            model: "served-model",
            input_tokens: 11,
            output_tokens: null,
            cached_input_tokens: null,
            tool_call_count: null,
            estimated_cost: null,
            currency: null,
            started_at: "2026-10-17T12:00:00+00:00",
            completed_at: "2026-10-17T12:00:00+00:00",
            cost_kind: "model",
          },
        },
        label: "Model explanation.",
        usage: "Provider openrouter. Model served-model. Input tokens: 11. Output tokens: not reported. Total tokens: not reported.",
      },
    ];
    for (const item of cases) {
      vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
        if (String(input).split("?")[0] === "/api/campaigns") {
          return Response.json({ campaigns: [] });
        }
        return {
          ok: true,
          json: async () => ({
            status: "answered",
            text: "Phrased from supplied facts.",
            session_id: prototypeWorkspace.session_id,
            run_id: null,
            read_only: true,
            evidence: [],
            instrumentation: item.instrumentation,
          }),
        } as Response;
      });
      const view = render(<LegacyWorkspace />);
      await user.click(screen.getByRole("tab", { name: "Conversation" }));
      await user.type(screen.getByLabelText("Read-only question"), "session status");
      await user.click(screen.getByRole("button", { name: "Ask read-only" }));
      const answer = await screen.findByRole("region", { name: "Read-only answer" });
      expect(answer).toHaveTextContent(item.label);
      expect(answer).toHaveTextContent(item.usage);
      expect(answer).not.toHaveTextContent("Total tokens: 0");
      expect(answer).not.toHaveTextContent("Output tokens: 0");
      view.unmount();
    }
  });

  it("explains a provider credit limit without the provider body", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "provider_budget",
        text: "account can only afford 24986 credits at https://openrouter.ai/settings/credits",
        session_id: prototypeWorkspace.session_id,
        run_id: "protein_run_pdl1_smoke",
        read_only: true,
        evidence: [],
        instrumentation: {
          interaction_id: "interaction_budget",
          recorded: false,
          total_tokens: null,
          observation: "adapter_failure",
          event: null,
          origin: null,
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.type(screen.getByLabelText("Read-only question"), "what is the session status");
    await user.click(screen.getByRole("button", { name: "Ask read-only" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The language provider reached its credit or completion-token budget. No scientific action was taken.",
    );
    expect(alert).not.toHaveTextContent("24986");
    expect(alert).not.toHaveTextContent("openrouter.ai");
  });

  it("shows a safe error when the conversation request fails", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockRejectedValue(new Error("secret-stack"));
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.type(screen.getByLabelText("Read-only question"), "what is the ranking");
    await user.click(screen.getByRole("button", { name: "Ask read-only" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The conversational request failed. No scientific action was taken.",
    );
    expect(screen.queryByText(/secret-stack/)).not.toBeInTheDocument();
  });

  it("does not offer an executable control", async () => {
    const user = userEvent.setup();
    render(<LegacyWorkspace />);
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Execute" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Promote" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ingest" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    expect(
      screen.getByText("Read-only conversational access. This cannot execute, approve, or promote."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(screen.getByText("Describe the PDL1 binder you want to design, or open the saved demonstration.")).toBeInTheDocument();
    expect(screen.getByText("This pilot supports the configured PDL1 workflow.")).toBeInTheDocument();
    expect(screen.queryByText(/currently disabled/)).not.toBeInTheDocument();
  });

  it("sends a design intent and shows fields, gaps, and clarifications", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "draft_created",
        text: "A draft revision was created. It is not authorized and not executable.",
        session_id: prototypeWorkspace.session_id,
        execution_authorized: false,
        executable: false,
        changed_fields: ["target_id"],
        questions: [{ field: "generator", question: "Which generator should be used?" }],
        issues: [{ field: "generator", code: "missing" }],
        draft: {
          request_id: "request_1",
          session_id: prototypeWorkspace.session_id,
          revision: 1,
          completeness: "incomplete",
          execution_authorized: false,
          executable: false,
          fields: [
            { name: "target_id", value: "PDL1", provenance: "user" },
            { name: "generator", value: null, provenance: "absent" },
            { name: "ranking_signal", value: "raw_ipsae", provenance: "documented_default" },
          ],
          issues: [{ field: "generator", code: "missing" }],
        },
        request: null,
        instrumentation: {
          interaction_id: "interaction_1",
          recorded: false,
          total_tokens: null,
          observation: "no_model_usage",
          event: null,
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "Quero desenhar binders para PDL1");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    expect(fetch).toHaveBeenCalledWith("/api/design-draft", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "Quero desenhar binders para PDL1",
        request_id: null,
        revision: null,
      }),
    });
    const review = await screen.findByRole("region", { name: "Design draft review" });
    expect(review).toHaveTextContent("target_id: PDL1 (user)");
    expect(review).toHaveTextContent("generator: absent (absent)");
    expect(review).toHaveTextContent("ranking_signal: raw_ipsae (documented_default)");
    expect(review).toHaveTextContent("generator missing");
    expect(review).toHaveTextContent("generator: Which generator should be used?");
    expect(review).toHaveTextContent("Changed: target_id.");
    expect(screen.getByRole("region", { name: "Conversational design request" })).toHaveTextContent(
      "Describe the PDL1 binder you want to design, or open the saved demonstration.",
    );
  });

  it("sends the current draft revision when clarifying", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "draft_updated",
        text: "The draft was revised. It is not authorized and not executable.",
        session_id: prototypeWorkspace.session_id,
        execution_authorized: false,
        executable: false,
        changed_fields: ["generator"],
        questions: [],
        issues: [],
        draft: {
          request_id: "request_1",
          session_id: prototypeWorkspace.session_id,
          revision: 2,
          completeness: "incomplete",
          execution_authorized: false,
          executable: false,
          fields: [{ name: "generator", value: "bindcraft", provenance: "user" }],
          issues: [],
        },
        request: null,
        instrumentation: {
          interaction_id: "interaction_2",
          recorded: false,
          total_tokens: null,
          observation: "no_model_usage",
          event: null,
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "bindcraft");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await screen.findByRole("region", { name: "Design draft review" });
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    await user.clear(screen.getByLabelText("Design intent"));
    await user.type(screen.getByLabelText("Design intent"), "4 candidates");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(fetch).toHaveBeenCalledWith("/api/design-draft", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: prototypeWorkspace.session_id,
        message: "4 candidates",
        request_id: "request_1",
        revision: 2,
      }),
    });
  });

  it("reopens a saved journey without retyping the design sentence", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "cellertz.saved-design-journey",
      JSON.stringify({
        journeyId: "journey_saved",
        requestId: "request_saved",
        revision: 1,
        requestText: "Design a PDL1 binder for review.",
      }),
    );
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_saved",
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
            stage_id: "design_request",
            status: "completed",
            implementation_kind: "recorded_request",
            implementation: "DesignDrafting",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: { request_text: "Design a PDL1 binder for review." },
            limitations: [],
          },
        ],
        execution_review: {
          plan_id: "plan_saved",
          revision: 1,
          digest: null,
          input_identities: null,
          input_registration: null,
          resource_budget: {},
          questions: [],
          unresolved_blockers: [],
          recorded_plan_approval: false,
          server_execution: "disabled",
          execution_authorized: false,
          launches_compute: false,
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
      "/api/campaigns",
      "/api/campaigns",
      "/api/design-journeys",
    ]);
    expect(screen.getByRole("button", { name: "Open saved demonstration" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Open saved demonstration" }));
    expect(await screen.findByText(/journey_saved/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/design-journeys/journey_saved");
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method !== "POST")).toBe(true);
    expect(screen.getByRole("region", { name: "Saved request" })).toHaveTextContent("Design a PDL1 binder for review.");
    expect(screen.getByRole("button", { name: "Edit request" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Design intent")).not.toBeInTheDocument();
    expect(screen.getByText("This pilot supports the configured PDL1 workflow. New computational executions are currently disabled; saved results remain available.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(screen.getByRole("region", { name: "Saved request" })).toBeInTheDocument();
    window.localStorage.removeItem("cellertz.saved-design-journey");
  });

  it("reopens a server-saved request by click without typing a journey id", async () => {
    const user = userEvent.setup();
    const saved = {
      request_id: "request_saved",
      request_revision: 2,
      request_text: "Design a PDL1 binder with bindcraft, 1 candidates.",
      journey_id: "journey_saved",
      updated_at: "2026-10-28T00:00:00+00:00",
      plan_id: "plan_saved",
      plan_revision: 4,
    };
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const path = String(input).split("?")[0];
      const method = String(init?.method ?? "GET").toUpperCase();
      if (method === "GET" && path === "/api/campaigns") {
        return { ok: true, json: async () => ({ campaigns: [] }) } as Response;
      }
      if (method === "GET" && path === "/api/design-journeys") {
        return { ok: true, json: async () => ({ requests: [saved] }) } as Response;
      }
      if (method === "GET" && path === "/api/attempt-coordinator") {
        return { ok: true, json: async () => ({ traces: [], limitations: [] }) } as Response;
      }
      if (method === "GET" && path === "/api/design-journeys/journey_saved") {
        return {
          ok: true,
          json: async () => ({
            journey_id: "journey_saved",
            request_id: "request_saved",
            request_revision: 2,
            plan_id: "plan_saved",
            plan_revision: 4,
            gpu_execution: "disabled",
            approved: false,
            specialist_agents_executed: false,
            knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
            hermes: { status: "not_applicable", detail: "Hermes was not called." },
            stages: [
              {
                stage_id: "design_request",
                status: "completed",
                implementation_kind: "recorded_request",
                implementation: "DesignDrafting",
                tools: [],
                agent_executed: false,
                evidence_references: [],
                output: { request_text: saved.request_text },
                limitations: [],
              },
            ],
            execution_review: {
              plan_id: "plan_saved",
              revision: 4,
              digest: null,
              input_identities: null,
              input_registration: null,
              resource_budget: {},
              questions: [],
              unresolved_blockers: [],
              recorded_plan_approval: false,
              server_execution: "disabled",
              execution_authorized: false,
              launches_compute: false,
            },
          }),
        } as Response;
      }
      throw new Error(`unexpected ${method} ${path}`);
    });
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(await screen.findByRole("button", { name: saved.request_text }));
    expect(await screen.findByRole("region", { name: "Saved request" })).toHaveTextContent(saved.request_text);
    expect(await screen.findByText("No coordinator trace is recorded for this request.")).toBeInTheDocument();
    expect(await screen.findByText(/journey_saved/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Journey identifier")).not.toHaveValue("journey_saved");
    expect(vi.mocked(fetch).mock.calls.every((call) => String(call[1]?.method ?? "GET").toUpperCase() !== "POST")).toBe(true);
    expect(fetch).toHaveBeenCalledWith("/api/design-journeys/journey_saved");
  });

  it("shows the design entry without claiming an execution mode", async () => {
    const user = userEvent.setup();
    vi.stubEnv("VITE_CELLERTZ_DEMONSTRATION_JOURNEY_ID", "  ");
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(screen.getByText("Describe the PDL1 binder you want to design, or open the saved demonstration.")).toBeInTheDocument();
    expect(screen.getByText("Describe the target, desired interaction and relevant constraints.")).toBeInTheDocument();
    const intent = screen.getByLabelText("Design intent");
    expect(intent).toHaveValue("");
    expect(intent).toHaveAttribute("placeholder", "Design a 65-residue binder against PDL1, focusing on the configured hotspot.");
    expect(screen.getByRole("button", { name: "Save design request" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open saved demonstration" })).toBeDisabled();
    expect(screen.getByText("No demonstration journey is configured for this installation, and none is recorded in this browser.")).toBeInTheDocument();
    expect(screen.getByText("Advanced options").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Technical details").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("A saved revision stays immutable. The server request id and the expected revision travel together.")).toBeInTheDocument();
    expect(screen.queryByText(/currently disabled/)).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
      "/api/campaigns",
      "/api/campaigns",
      "/api/design-journeys",
    ]);
  });

  it("reports demonstration execution without saying it is disabled", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "cellertz.saved-design-journey",
      JSON.stringify({
        journeyId: "journey_saved",
        requestId: "request_saved",
        requestText: "Design a PDL1 binder for review.",
      }),
    );
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        journey_id: "journey_saved",
        request_id: "request_saved",
        request_revision: 1,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "design_request",
            status: "completed",
            implementation_kind: "recorded_request",
            implementation: "DesignDrafting",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: { request_text: "Design a PDL1 binder for review." },
            limitations: [],
          },
        ],
        execution_review: {
          plan_id: null,
          revision: null,
          digest: null,
          input_identities: null,
          input_registration: null,
          resource_budget: {},
          questions: [],
          unresolved_blockers: [],
          recorded_plan_approval: false,
          server_execution: "apptainer-demonstration",
          execution_authorized: false,
          launches_compute: false,
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Open saved demonstration" }));
    expect(await screen.findByText(/The server reports demonstration execution as configured/)).toBeInTheDocument();
    expect(screen.queryByText(/currently disabled/)).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method !== "POST")).toBe(true);
    window.localStorage.removeItem("cellertz.saved-design-journey");
  });

  it("keeps the entry when the saved demonstration cannot be read", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "cellertz.saved-design-journey",
      JSON.stringify({
        journeyId: "journey_missing",
        requestId: "request_missing",
        requestText: "Design a PDL1 binder for review.",
      }),
    );
    let reads = 0;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input).split("?")[0];
      if (path === "/api/campaigns") {
        return Response.json({ campaigns: [] });
      }
      if (path === "/api/design-journeys") {
        return Response.json({ requests: [] });
      }
      if (path === "/api/attempt-coordinator") {
        return Response.json({ traces: [], limitations: [] });
      }
      reads += 1;
      if (reads === 1) {
        return { ok: false, status: 500, json: async () => ({}) } as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          journey_id: "journey_missing",
          request_id: "request_missing",
          request_revision: 1,
          knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
          hermes: { status: "not_applicable", detail: "Hermes was not called." },
          stages: [
            {
              stage_id: "design_request",
              status: "completed",
              implementation_kind: "recorded_request",
              implementation: "DesignDrafting",
              tools: [],
              agent_executed: false,
              evidence_references: [],
              output: { request_text: "Design a PDL1 binder for review." },
              limitations: [],
            },
          ],
        }),
      } as Response;
    });
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Open saved demonstration" }));
    expect(await screen.findByText("The design journey could not be read. The server returned HTTP 500.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save design request" })).toBeInTheDocument();
    expect(screen.queryByText(/currently disabled/)).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => (call[1]?.method ?? "GET") !== "POST")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Saved request" })).toHaveTextContent("Design a PDL1 binder for review.");
    expect(await screen.findByText("No coordinator trace is recorded for this request.")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map((call) => String(call[0]))).toEqual([
      "/api/campaigns",
      "/api/campaigns",
      "/api/design-journeys",
      "/api/design-journeys/journey_missing",
      "/api/design-journeys/journey_missing",
      "/api/attempt-coordinator?request_id=request_missing&request_revision=1",
    ]);
    expect(vi.mocked(fetch).mock.calls.every((call) => (call[1]?.method ?? "GET") !== "POST")).toBe(true);
    expect(screen.queryByText(/currently disabled/)).not.toBeInTheDocument();
    window.localStorage.removeItem("cellertz.saved-design-journey");
  });

  it("opens the installation demonstration on a first visit with empty storage", async () => {
    const user = userEvent.setup();
    vi.stubEnv("VITE_CELLERTZ_DEMONSTRATION_JOURNEY_ID", "journey_configured");
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        journey_id: "journey_configured",
        request_id: "request_configured",
        request_revision: 1,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "design_request",
            status: "completed",
            implementation_kind: "recorded_request",
            implementation: "DesignDrafting",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: { request_text: "Design a PDL1 binder for review." },
            limitations: [],
          },
        ],
        execution_review: {
          plan_id: null,
          revision: null,
          digest: null,
          input_identities: null,
          input_registration: null,
          resource_budget: {},
          questions: [],
          unresolved_blockers: [],
          recorded_plan_approval: false,
          server_execution: "disabled",
          execution_authorized: false,
          launches_compute: false,
        },
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(window.localStorage.getItem("cellertz.saved-design-journey")).toBeNull();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
      "/api/campaigns",
      "/api/campaigns",
      "/api/design-journeys",
    ]);
    await user.click(screen.getByRole("button", { name: "Open saved demonstration" }));
    expect(await screen.findByText(/journey_configured/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/design-journeys/journey_configured");
    expect(vi.mocked(fetch).mock.calls.every((call) => (call[1]?.method ?? "GET") !== "POST")).toBe(true);
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(await screen.findByRole("region", { name: "Saved request" })).toBeInTheDocument();
  });

  it("prefers the configured demonstration over a different browser record", async () => {
    const user = userEvent.setup();
    vi.stubEnv("VITE_CELLERTZ_DEMONSTRATION_JOURNEY_ID", "journey_configured");
    window.localStorage.setItem(
      "cellertz.saved-design-journey",
      JSON.stringify({
        journeyId: "journey_browser",
        requestId: "request_browser",
        requestText: "Design a PDL1 binder for review.",
      }),
    );
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        journey_id: "journey_configured",
        request_id: "request_configured",
        request_revision: 1,
        knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
        hermes: { status: "not_applicable", detail: "Hermes was not called." },
        stages: [
          {
            stage_id: "design_request",
            status: "completed",
            implementation_kind: "recorded_request",
            implementation: "DesignDrafting",
            tools: [],
            agent_executed: false,
            evidence_references: [],
            output: { request_text: "Design a PDL1 binder for review." },
            limitations: [],
          },
        ],
      }),
    } as Response);
    render(<LegacyWorkspace />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Open saved demonstration" }));
    expect(await screen.findByText(/journey_configured/)).toBeInTheDocument();
    expect(await screen.findByText("No coordinator trace is recorded for this request.")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map((call) => String(call[0]))).toEqual([
      "/api/campaigns",
      "/api/campaigns",
      "/api/design-journeys",
      "/api/design-journeys/journey_configured",
      "/api/attempt-coordinator?request_id=request_configured&request_revision=1",
    ]);
    expect(vi.mocked(fetch).mock.calls.some((call) => String(call[0]).includes("journey_browser"))).toBe(false);
    window.localStorage.removeItem("cellertz.saved-design-journey");
  });

  function storedPlan(requestId: string, revision: number): Record<string, unknown> {
    return {
      plan_id: `plan_${requestId}_${revision}`,
      revision: 1,
      design_request_id: requestId,
      design_request_revision: revision,
      status: "draft",
      digest: "a".repeat(64),
      approved: false,
      execution_authorized: false,
      execution_connected: false,
      scientific_execution: "disabled",
      fields: {},
      questions: [{ field: "binder_length", question: "How many residues should the binder have?" }],
    };
  }

  function planCatalog(
    requestId: string,
    revision: number,
    message: string,
    plans: Array<Record<string, unknown>>,
    status = "plans_recorded",
    summary?: Record<string, unknown>,
    changed?: boolean,
  ): Response {
    return {
      ok: true,
      status: 200,
      json: async () => ({
        status,
        request_id: requestId,
        revision,
        summary: summary ?? {
          objective: { value: message, source: "researcher", pending: false },
          target: { value: null, source: "pending", pending: true },
          candidates_requested: { value: null, source: "pending", pending: true },
          generation_method: { value: "BindCraft", source: "server_configuration", pending: false },
        },
        plans,
        proposed_workflow: [
          "Read the saved design request at this revision.",
          "Record only parameters present on that revision or verified by the server.",
          "Leave every other parameter pending.",
          "Stop. This draft does not approve, authorize, or start execution.",
        ],
        planning_origin: "deterministic_generation_plan",
        model_decided: false,
        agents_executed: false,
        approved: false,
        execution_authorized: false,
        executable: false,
        scientific_execution: "disabled",
        ...(changed === undefined ? {} : { plan_revision_changed: changed }),
      }),
    } as Response;
  }

  it("opens a plan for the selected revision and keeps another card apart", async () => {
    const user = userEvent.setup();
    const plans = new Map<string, Array<Record<string, unknown>>>();
    const revisions = new Map<string, number>();
    const messages = new Map<string, string>();
    const posts: string[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.startsWith("/api/generation-plan")) {
        const requestId = method === "GET"
          ? new URL(url, "http://local").searchParams.get("request_id") ?? ""
          : "";
        if (method === "POST") {
          const body = JSON.parse(String(init?.body)) as { request_id: string; revision: number };
          posts.push(JSON.stringify(body));
          const existing = plans.get(body.request_id) ?? [];
          if (!existing.some((item) => item.design_request_revision === body.revision)) {
            existing.unshift(storedPlan(body.request_id, body.revision));
            plans.set(body.request_id, existing);
          }
          return planCatalog(body.request_id, revisions.get(body.request_id) ?? body.revision, messages.get(body.request_id) ?? "", existing, "plan_recorded");
        }
        return planCatalog(requestId, revisions.get(requestId) ?? 1, messages.get(requestId) ?? "", plans.get(requestId) ?? []);
      }
      if (method === "GET") {
        return emptyDraftList();
      }
      const body = JSON.parse(String(init?.body)) as { message: string; request_id: string | null; revision: number | null };
      const requestId = body.request_id ?? (body.message.includes("second") ? "request_two" : "request_one");
      const revision = body.request_id ? Number(body.revision) + 1 : 1;
      revisions.set(requestId, revision);
      messages.set(requestId, body.message);
      return draftResponse(body.request_id ? "draft_updated" : "draft_created", requestId, revision);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the first target");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.clear(screen.getByLabelText("Design intent"));
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the second target");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "design a binder for the first target" }));
    expect(await screen.findByRole("button", { name: "Create plan" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("Saved request, revision 1");
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("design a binder for the first target");
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("Generation plan");
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("BindCraft");
    expect(posts).toEqual([]);
    expect(screen.queryByRole("button", { name: "Execute" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create plan" }));
    expect(await screen.findByText("Status: Draft")).toBeInTheDocument();
    expect(screen.getByText("Needs configuration")).toBeInTheDocument();
    expect(screen.getByText("Binder length (amino acids) is still to be defined.")).toBeInTheDocument();
    expect(screen.getByText("Creating a plan does not start generation.")).toBeInTheDocument();
    expect(screen.getByText("The requested quantity does not guarantee approved candidates.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Proposed workflow" })).not.toBeInTheDocument();
    expect(screen.queryByText("Read the saved design request at this revision.")).not.toBeInTheDocument();
    expect(screen.getByText("GPU").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Plan id: plan_request_one_1").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Planning origin: deterministic generation plan. It was recorded without a model. A later specialist consultation is separate and does not change this origin.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create plan" })).not.toBeInTheDocument();
    expect(posts).toEqual(['{"request_id":"request_one","revision":1}']);
    await user.click(screen.getByRole("button", { name: "design a binder for the first target" }));
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("Select a saved request in Design request.");
    await user.click(screen.getByRole("button", { name: "design a binder for the first target" }));
    expect(await screen.findByText("Status: Draft")).toBeInTheDocument();
    expect(posts).toEqual(['{"request_id":"request_one","revision":1}']);
    await user.clear(screen.getByLabelText("Design intent"));
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the first target revised");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    expect(await screen.findByRole("button", { name: "Create plan" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plan" })).toHaveTextContent("Saved request, revision 2");
    expect(screen.getByRole("heading", { name: "Plan of revision 1." })).toBeInTheDocument();
    expect(screen.getByText("Plan id: plan_request_one_1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "design a binder for the second target" }));
    const plan = screen.getByRole("region", { name: "Plan" });
    expect(plan).not.toHaveTextContent("Plan id: plan_request_one_1");
    expect(plan).toHaveTextContent("Saved request, revision 1");
    expect(plan).toHaveTextContent("design a binder for the second target");
    expect(plan).not.toHaveTextContent("Plan id: plan_request_one_1");
    expect(plan).not.toHaveTextContent("design a binder for the first target revised");
  });

  it("does not apply a late plan reading to the request selected afterwards", async () => {
    const user = userEvent.setup();
    let release: (response: Response) => void = () => undefined;
    const late = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.startsWith("/api/generation-plan")) {
        const requestId = new URL(url, "http://local").searchParams.get("request_id");
        if (requestId === "request_one") {
          return late;
        }
        return planCatalog("request_two", 1, "design a binder for the second target", [
          storedPlan("request_two", 1),
        ]);
      }
      if (method === "GET") {
        return emptyDraftList();
      }
      const body = JSON.parse(String(init?.body)) as { message: string; request_id: string | null };
      const requestId = body.message.includes("second") ? "request_two" : "request_one";
      return draftResponse("draft_created", requestId, 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the first target");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.clear(screen.getByLabelText("Design intent"));
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the second target");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "design a binder for the first target" }));
    expect(screen.getByText("Loading the plan.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "design a binder for the second target" }));
    expect(screen.queryByText("Plan id: plan_request_one_1")).not.toBeInTheDocument();
    expect(await screen.findByText("Plan id: plan_request_two_1")).toBeInTheDocument();
    release(planCatalog("request_one", 1, "design a binder for the first target", [storedPlan("request_one", 1)]));
    expect(screen.getByText("Plan id: plan_request_two_1")).toBeInTheDocument();
    expect(screen.queryByText("Plan id: plan_request_one_1")).not.toBeInTheDocument();
  });

  it("presents recorded plan fields and leaves absent ones pending", async () => {
    const user = userEvent.setup();
    const filledSummary = {
      objective: { value: "design a binder for the prepared target", source: "researcher", pending: false },
      target: { value: "PDL1", source: "researcher", pending: false },
      candidates_requested: { value: 1, source: "researcher", pending: false },
      generation_method: { value: "BindCraft", source: "server_configuration", pending: false },
    };
    const filledPlan = {
      ...storedPlan("request_prepared", 1),
      questions: [
        { field: "cpu_count", question: "How many CPUs should the resource limit record?" },
        { field: "memory_mib", question: "What host RAM limit, in MiB, should the plan record? This is host memory, not GPU VRAM." },
        { field: "execution_deadline_seconds", question: "What execution deadline, in seconds, should the plan record?" },
      ],
      fields: {
        binder_length: 80,
        binder_length_provenance: "user",
        hotspot_assumption: "A56",
        hotspot_assumption_source: "professor",
        hotspot_assumption_provenance: "user",
        filter_policy: "no_filters",
        filter_policy_provenance: "user",
        gpu: "not_assigned",
        gpu_provenance: "user",
        seed: 1,
        seed_source: "professor",
        seed_provenance: "user",
      },
    };
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.startsWith("/api/generation-plan")) {
        expect(method).toBe("GET");
        const requestId = new URL(url, "http://local").searchParams.get("request_id");
        if (requestId === "request_prepared") {
          return planCatalog(requestId, 1, "design a binder for the prepared target", [filledPlan], "plans_recorded", filledSummary);
        }
        return planCatalog("request_open", 1, "design a binder that is still open", [storedPlan("request_open", 1)]);
      }
      if (method === "GET") {
        return emptyDraftList();
      }
      const body = JSON.parse(String(init?.body)) as { message: string };
      const requestId = body.message.includes("open") ? "request_open" : "request_prepared";
      return draftResponse("draft_created", requestId, 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the prepared target");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.clear(screen.getByLabelText("Design intent"));
    await user.type(screen.getByLabelText("Design intent"), "design a binder that is still open");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "design a binder for the prepared target" }));
    const plan = await screen.findByRole("region", { name: "Plan" });
    expect(plan).toHaveTextContent("Generation plan");
    expect(plan).toHaveTextContent("The requested quantity does not guarantee approved candidates.");
    expect(plan).toHaveTextContent("Target");
    expect(plan).toHaveTextContent("PDL1");
    expect(plan).toHaveTextContent("Researcher");
    expect(plan).toHaveTextContent("Server configuration");
    expect(plan).toHaveTextContent("80");
    expect(plan).toHaveTextContent("A56");
    expect(plan).toHaveTextContent("Technical demonstration — no scientific filtering.");
    expect(plan).toHaveTextContent("CPUs are still to be defined.");
    expect(plan).toHaveTextContent("A GPU is not selected.");
    expect(plan).not.toHaveTextContent("ready to execute");
    const execution = screen.getByText("Execution settings").closest("details");
    expect(execution).not.toHaveAttribute("open");
    expect(execution).toHaveTextContent("not_assigned");
    expect(execution).toHaveTextContent("Pending");
    expect(execution).toHaveTextContent("Host RAM (MiB)");
    expect(screen.getByText("Technical details").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByRole("button", { name: "Create plan" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "design a binder that is still open" }));
    expect(screen.queryByText("Prepare a BindCraft run requesting 1 binder candidate against PDL1.")).not.toBeInTheDocument();
    expect(await screen.findByText("Binder length (amino acids) is still to be defined.")).toBeInTheDocument();
    expect(screen.getByText("Plan id: plan_request_open_1")).toBeInTheDocument();
    expect(screen.queryByText("Plan id: plan_request_prepared_1")).not.toBeInTheDocument();
    expect(screen.queryByText("A56")).not.toBeInTheDocument();
  });

  it("saves plan edits on the selected card and leaves the other card unchanged", async () => {
    const user = userEvent.setup();
    let prepared = storedPlan("request_prepared", 1);
    const posts: Array<Record<string, unknown>> = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/generation-plan/revision" && method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        posts.push(body);
        if (body.expected_plan_revision !== prepared.revision) {
          return { ok: false, status: 409, json: async () => ({ status: "plan_revision_conflict" }) } as Response;
        }
        const scientific = body.scientific as Record<string, unknown>;
        const technical = body.technical as Record<string, unknown>;
        if (scientific.binder_length === 0) {
          return { ok: false, status: 400, json: async () => ({ status: "rejected" }) } as Response;
        }
        if (scientific.binder_length === 91) {
          return { ok: false, status: 409, json: async () => ({ status: "plan_revision_conflict" }) } as Response;
        }
        const changed = scientific.binder_length === 90;
        if (changed) {
          prepared = {
            ...prepared,
            revision: 2,
            digest: "b".repeat(64),
            fields: {
              binder_length: 90,
              binder_length_provenance: "user",
              binder_length_execution_integration: "pending",
              binder_length_execution_support: "not_connected",
              hotspot_strategy: "none",
              hotspot_assumption_execution_integration: "pending",
              hotspot_assumption_execution_support: "not_connected",
              filter_policy: "no_filters",
              filter_policy_mode: "technical_demonstration",
              filter_policy_provenance: "user",
              filter_policy_execution_integration: "pending",
              filter_policy_execution_support: "not_connected",
            },
            questions: [],
          };
        }
        return planCatalog(
          "request_prepared",
          1,
          "design a binder for the prepared target",
          [prepared],
          "plan_recorded",
          {
            objective: { value: "design a binder for the prepared target", source: "researcher", pending: false },
            target: { value: "PDL1", source: "researcher", pending: false },
            candidates_requested: { value: 1, source: "researcher", pending: false },
            generation_method: { value: "BindCraft", source: "server_configuration", pending: false },
          },
          changed,
        );
      }
      if (url.startsWith("/api/generation-plan")) {
        const requestId = new URL(url, "http://local").searchParams.get("request_id");
        if (requestId === "request_prepared") {
          return planCatalog(requestId, 1, "design a binder for the prepared target", [prepared], "plans_recorded", {
            objective: { value: "design a binder for the prepared target", source: "researcher", pending: false },
            target: { value: "PDL1", source: "researcher", pending: false },
            candidates_requested: { value: 1, source: "researcher", pending: false },
            generation_method: { value: "BindCraft", source: "server_configuration", pending: false },
          });
        }
        return planCatalog("request_open", 1, "design a binder that is still open", [storedPlan("request_open", 1)]);
      }
      if (method === "GET") {
        return emptyDraftList();
      }
      const body = JSON.parse(String(init?.body)) as { message: string };
      const requestId = body.message.includes("open") ? "request_open" : "request_prepared";
      return draftResponse("draft_created", requestId, 1);
    });
    render(<App />);
    await user.type(screen.getByLabelText("Design intent"), "design a binder for the prepared target");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.clear(screen.getByLabelText("Design intent"));
    await user.type(screen.getByLabelText("Design intent"), "design a binder that is still open");
    await user.click(screen.getByRole("button", { name: "Save request" }));
    await user.click(await screen.findByRole("button", { name: "design a binder for the prepared target" }));
    const plan = await screen.findByRole("region", { name: "Plan" });
    expect(screen.getByText("Execution settings").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Technical details").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByLabelText("Binder length (amino acids)")).not.toBeInTheDocument();
    expect(plan).toHaveTextContent("Filling these fields does not make the plan ready to run.");
    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    expect(screen.getByText("Execution settings").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByLabelText("Binder length (amino acids)")).toHaveValue("");
    expect(screen.getByLabelText("Binding site (hotspots)")).toHaveValue("");
    expect(screen.getByRole("option", { name: "Select how to define the binding site" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Specify hotspot residues" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Design without predefined hotspots" })).toBeInTheDocument();
    expect(plan).toHaveTextContent("Optionally guide binder design toward specific residues on the target protein.");
    expect(screen.queryByLabelText("Hotspot residues — target PDB numbering")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Technical demonstration — no scientific filtering" })).toBeInTheDocument();
    expect(plan).toHaveTextContent("This process seed does not guarantee reproduction of the BindCraft trajectory.");
    await user.type(screen.getByLabelText("Binder length (amino acids)"), "90");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Binder length (amino acids)")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save plan changes" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    expect(screen.getByLabelText("Binder length (amino acids)")).toHaveValue("");
    await user.type(screen.getByLabelText("Binder length (amino acids)"), "90");
    await user.selectOptions(screen.getByLabelText("Binding site (hotspots)"), "residues");
    expect(plan).toHaveTextContent("Use chain and residue numbers from the selected target structure.");
    await user.type(screen.getByLabelText("Hotspot residues — target PDB numbering"), "1");
    await user.click(screen.getByRole("button", { name: "Save plan changes" }));
    expect(await screen.findByText(/A1 is not in chain A of the verified PDB/)).toBeInTheDocument();
    expect(posts).toHaveLength(0);
    await user.clear(screen.getByLabelText("Hotspot residues — target PDB numbering"));
    await user.type(screen.getByLabelText("Hotspot residues — target PDB numbering"), "A20");
    expect(plan).toHaveTextContent("Candidates will not be screened using scientific quality filters.");
    await user.selectOptions(screen.getByLabelText("Candidate filtering"), "technical_demonstration");
    await user.click(screen.getByRole("button", { name: "Save plan changes" }));
    expect(await screen.findByText("Plan changes saved.")).toBeInTheDocument();
    expect(posts[0]?.request_text).toBeUndefined();
    expect(JSON.stringify(posts[0])).not.toContain("technical-smoke-test");
    expect(posts[0]).toMatchObject({
      scientific: {
        binder_length: 90,
        hotspot: { mode: "residues", value: "A20" },
        filter_policy: { mode: "technical_demonstration", policy: "no_filters" },
      },
      technical: {},
    });
    expect(plan).toHaveTextContent("Plan revision: 2");
    expect(plan).toHaveTextContent("Design without predefined hotspots");
    expect(plan).toHaveTextContent("Technical demonstration — no scientific filtering");
    expect(plan).toHaveTextContent("Preparing settings does not start BindCraft.");
    expect(plan).toHaveTextContent("A binder length the installed pipeline can accept is not a scientific success guarantee.");
    expect(plan).toHaveTextContent("Server execution: the current server does not allow execution.");
    expect(plan).toHaveTextContent("Status: Draft");
    expect(plan).not.toHaveTextContent("Ready for review");
    expect(screen.queryByLabelText("Binder length (amino acids)")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    expect(screen.getByLabelText("Binder length (amino acids)")).toHaveValue("90");
    expect(screen.getByLabelText("Binding site (hotspots)")).toHaveValue("none");
    expect(screen.queryByLabelText("Hotspot residues — target PDB numbering")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Candidate filtering")).toHaveValue("technical_demonstration");
    await user.click(screen.getByRole("button", { name: "Save plan changes" }));
    expect(await screen.findByText("No plan change was recorded.")).toBeInTheDocument();
    expect(posts.at(-1)).toMatchObject({ scientific: {}, technical: {}, expected_plan_revision: 2 });
    expect(screen.queryByRole("button", { name: "Save plan changes" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    await user.clear(screen.getByLabelText("Binder length (amino acids)"));
    await user.type(screen.getByLabelText("Binder length (amino acids)"), "0");
    await user.click(screen.getByRole("button", { name: "Save plan changes" }));
    expect(await screen.findByText("The plan change was rejected. The saved plan was left unchanged.")).toBeInTheDocument();
    expect(screen.getByLabelText("Binder length (amino acids)")).toHaveValue("0");
    expect(screen.getByRole("button", { name: "Save plan changes" })).toBeInTheDocument();
    expect(plan).toHaveTextContent("Plan revision: 2");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("The plan change was rejected. The saved plan was left unchanged.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    expect(screen.getByLabelText("Binder length (amino acids)")).toHaveValue("90");
    await user.clear(screen.getByLabelText("Binder length (amino acids)"));
    await user.type(screen.getByLabelText("Binder length (amino acids)"), "91");
    await user.click(screen.getByRole("button", { name: "Save plan changes" }));
    expect(await screen.findByText("This plan revision is no longer current. The saved plan was left unchanged.")).toBeInTheDocument();
    expect(screen.getByLabelText("Binder length (amino acids)")).toHaveValue("91");
    expect(plan).toHaveTextContent("Plan revision: 2");
    expect(plan).toHaveTextContent("90");
    await user.click(screen.getByRole("button", { name: "design a binder that is still open" }));
    const other = await screen.findByRole("region", { name: "Plan" });
    expect(other).not.toHaveTextContent("Plan revision: 2");
    expect(other).not.toHaveTextContent("A binder length the installed pipeline can accept is not a scientific success guarantee.");
    expect(other).not.toHaveTextContent("90");
    expect(other).not.toHaveTextContent("91");
    expect(screen.queryByLabelText("Binder length (amino acids)")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit plan" })).toBeInTheDocument();
    expect(other).toHaveTextContent("Plan id: plan_request_open_1");
  });
});

describe("vitest mock restoration", () => {
  it("spies on fetch inside one test", () => {
    vi.spyOn(globalThis, "fetch");
    expect(vi.isMockFunction(globalThis.fetch)).toBe(true);
  });

  it("starts the next test with a fresh campaign-list fetch", async () => {
    const response = await fetch("/api/campaigns");
    expect(response.ok).toBe(true);
    await expect(fetch("/api/conversation")).rejects.toThrow(/unexpected fetch/);
  });
});
