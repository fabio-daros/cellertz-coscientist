import { render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";
import { CAMPAIGN_ABOUT_STORAGE_KEY } from "./CampaignAbout";
import { campaignPreview } from "./campaign-preview";
import { prototypeWorkspace } from "./fixture";

function expectCampaignListOnly() {
  const calls = vi.mocked(fetch).mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  expect(
    calls.every(([url, init]) => {
      const method = String(init?.method ?? "GET").toUpperCase();
      const path = String(url).split("?")[0];
      return method === "GET" && (path === "/api/campaigns" || path === "/api/design-journeys");
    }),
  ).toBe(true);
}

describe("guided protein design workspace", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.spyOn(globalThis, "fetch");
  });

  it("shows research campaigns and lists planned modules without actions", async () => {
    render(<App />);
    const overview = screen.getByRole("region", { name: "Research campaigns" });
    expect(overview).toHaveTextContent("Research campaigns");
    expect(overview).toHaveTextContent("Organize protein design work around a scientific objective.");
    expect(await screen.findByText("No campaigns are saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New campaign" })).toBeInTheDocument();
    expect(overview).not.toHaveTextContent("No campaign record links a journey or a result");
    expect(overview).not.toHaveTextContent("mBER version not recorded");
    const demonstration = screen.getByRole("region", { name: "Available demonstration" });
    expect(demonstration).toHaveTextContent("PDL1 binder design");
    expect(within(overview).queryByText("PDL1 binder design")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Design intent")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save design request" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve request" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create fixture run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run approved demonstration" })).not.toBeInTheDocument();
    const planned = screen.getByRole("region", { name: "Planned modules" });
    expect(planned).toHaveTextContent("Target Discovery");
    expect(planned).toHaveTextContent("mRNA Design");
    expect(planned).toHaveTextContent("Experimental feedback");
    expect(planned).toHaveTextContent("These modules are planned.");
    expect(within(planned).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Reference configurations").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Technical details").closest("details")).not.toHaveAttribute("open");
    const technical = screen.getByText("Technical details").closest("details");
    expect(technical).toHaveTextContent("That projection is not a saved campaign");
    expect(technical).toHaveTextContent("Legacy prototype records");
    expect(technical).toHaveTextContent("protein_run_pdl1_smoke");
    expectCampaignListOnly();
  });

  it("explains Campaign on the first visit and stays closed across tabs", async () => {
    const user = userEvent.setup();
    window.sessionStorage.removeItem(CAMPAIGN_ABOUT_STORAGE_KEY);
    render(<App />);
    const dialog = screen.getByRole("dialog", { name: "About this workspace" });
    expect(dialog).toHaveTextContent(
      "This page organizes research campaigns around a scientific objective.",
    );
    expect(dialog).toHaveTextContent(
      "A campaign records a name, a scientific objective written by the researcher, and optional notes.",
    );
    expect(dialog).toHaveTextContent(
      "Saving a campaign does not create a design request, a plan, or an execution.",
    );
    expect(dialog).toHaveTextContent("Reference configurations: consult the CD22 and TACI examples.");
    expect(dialog).toHaveTextContent("Planned modules: see capabilities planned for future development.");
    expect(dialog).toHaveTextContent("Opening this page does not start a computation.");
    expect(dialog).toHaveTextContent(
      "Available demonstration: open the recorded PDL1 binder workflow and its saved results when one is configured for this installation.",
    );
    expectCampaignListOnly();
    await user.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "About this page" })).toBeInTheDocument();
    expectCampaignListOnly();
  });

  it("keeps the explanation open when the workspace mounts twice in one session", () => {
    window.sessionStorage.removeItem(CAMPAIGN_ABOUT_STORAGE_KEY);
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    expect(screen.getByRole("dialog", { name: "About this workspace" })).toBeInTheDocument();
    expectCampaignListOnly();
  });

  it("reopens the explanation and returns focus when it closes", async () => {
    const user = userEvent.setup();
    render(<App />);
    const about = screen.getByRole("button", { name: "About this page" });
    await user.click(about);
    const dialog = screen.getByRole("dialog", { name: "About this workspace" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(about).toHaveFocus();
    await user.click(about);
    expect(screen.getByRole("dialog", { name: "About this workspace" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(about).toHaveFocus();
    expectCampaignListOnly();
  });

  it("opens design request without creating a request", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(screen.getByRole("tab", { name: "Design request", selected: true })).toBeInTheDocument();
    expect(document.getElementById("area-panel-design")).toHaveTextContent("Design intent");
    expectCampaignListOnly();
  });

  it("opens the general results screen without a campaign link or a write", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      json: async () => ({ status: "not_found", error: "not found" }),
    } as Response);
    render(<App />);
    expect(screen.getByRole("region", { name: "Research campaigns" })).toHaveTextContent(
      "Organize protein design work around a scientific objective.",
    );
    await user.click(screen.getByRole("tab", { name: "Results" }));
    expect(screen.getByRole("tab", { name: "Results", selected: true })).toBeInTheDocument();
    expect(window.location.search).not.toContain("attempt=");
    expect(window.location.search).not.toContain("campaign");
    expect(await screen.findByText("No attempt is selected. Results stays empty until a round records one.")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/api/attempts/"))).toBe(false);
    expect(vi.mocked(fetch).mock.calls.every((call) => call[1]?.method !== "POST")).toBe(true);
  });

  it("switches CD22 and TACI locally and keeps the chain namespaces", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "Design a PDL1 binder");
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await user.click(screen.getByText("Reference configurations"));
    const summary = screen.getByRole("region", { name: "Reference summary" });
    expect(summary).toHaveTextContent("CD22");
    expect(summary).toHaveTextContent("VHH");
    expect(summary).toHaveTextContent("PDB 7O52");
    expect(screen.getByRole("button", { name: "CD22 / PDB 7O52" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "TACI / PDB 1XU1" }));
    expectCampaignListOnly();
    expect(screen.getByRole("region", { name: "Research campaigns" })).not.toHaveTextContent("Campaign: TACI VHH");
    expect(screen.getByRole("region", { name: "Reference summary" })).toHaveTextContent("PDB 1XU1");
    await user.click(screen.getByText("Reference details"));
    const target = screen.getByRole("region", { name: "Target and hotspots" });
    expect(target).toHaveTextContent("1XU1");
    expect(target).toHaveTextContent("source partner chain A != prepared target chain A");
    expect(target).toHaveTextContent("source target R → prepared target A");
    expect(target).toHaveTextContent("4.5 Å");
    expect(screen.getByText("prepared A78")).toBeInTheDocument();
    expect(target).toHaveTextContent("Remove solvent");
    expect(target).not.toHaveTextContent("1. 1.");
    expect(target).toHaveTextContent("pdb:1XU1");
    expect(target).toHaveTextContent("Not recorded");
    await user.click(screen.getByRole("button", { name: "CD22 / PDB 7O52" }));
    expect(screen.getByText("prepared A624")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Target and hotspots" })).toHaveTextContent("7O52");
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    expect(screen.getByLabelText("Design intent")).toHaveValue("Design a PDL1 binder");
    expectCampaignListOnly();
  });

  it("keeps reference tools inside the reference and omits empty evaluations", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText("Reference configurations"));
    const summary = screen.getByRole("region", { name: "Reference summary" });
    expect(summary).not.toHaveTextContent("5,000");
    expect(summary).not.toHaveTextContent("50,000");
    expect(screen.queryByRole("region", { name: "Screen and evaluate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Compare and prioritize" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await user.click(screen.getByText("Reference details"));
    const tools = screen.getByRole("region", { name: "Reference tools" });
    expect(tools).toHaveTextContent("mber");
    expect(tools).toHaveTextContent("Not recorded");
    expect(tools).toHaveTextContent("iPTM ≥ 0.75");
    expect(tools).toHaveTextContent("pLDDT ≥ 0.70");
    expect(tools).toHaveTextContent("Generator filter");
    expect(tools).toHaveTextContent("ranking_score remains raw ipSAE");
    expect(tools).toHaveTextContent("BindCraft is not the generator protocol of this reference");
    expect(tools).toHaveTextContent("The PDL1 demonstration is conducted from Design request.");
    expect(tools).not.toHaveTextContent("Not integrated here");
    expect(tools).toHaveTextContent(
      "That policy is not the budget of the PDL1 demonstration.",
    );
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expect(screen.queryByText("composite_score")).not.toBeInTheDocument();
    expectCampaignListOnly();
  });

  it("keeps references and planned modules from acting", () => {
    render(<App />);
    const planned = screen.getByRole("region", { name: "Planned modules" });
    expect(planned).toHaveTextContent("mRNA Design");
    expect(within(planned).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Research campaigns" })).not.toHaveTextContent(
      "mBER version not recorded",
    );
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expectCampaignListOnly();
  });

  it("opens Design request by click and keyboard without sending a request", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    const designTab = screen.getByRole("tab", { name: "Design request", selected: true });
    expect(designTab).toHaveAttribute("aria-controls", "area-panel-design");
    const panel = document.getElementById("area-panel-design");
    expect(panel).not.toBeNull();
    expect(panel).toHaveTextContent("Design request");
    expect(panel).toHaveTextContent("Design intent");
    expectCampaignListOnly();
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    screen.getByRole("tab", { name: "Campaigns" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Design request", selected: true })).toBeInTheDocument();
    expect(document.getElementById("area-panel-design")).toHaveTextContent("Save design request");
    expectCampaignListOnly();
  });

  it("discloses the masked scaffold inside the reference configuration", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText("Reference configurations"));
    await user.click(screen.getByText("Reference details"));
    await user.click(screen.getByText("VHH scaffold"));
    expect(screen.getByText("operational-vhh-masked-framework")).toBeInTheDocument();
    expect(screen.getByText(/CDR1 26–35/)).toBeInTheDocument();
    expect(screen.getByText(/CDR2 49–65/)).toBeInTheDocument();
    expect(screen.getByText(/CDR3 96–109/)).toBeInTheDocument();
  });

  it("keeps the read-only chat and reports answer provenance", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "answered",
        text: "The ranking signal is raw ipSAE.",
        session_id: prototypeWorkspace.session_id,
        run_id: "protein_run_pdl1_smoke",
        read_only: true,
        evidence: [
          {
            capability: "get_ranking",
            entity_kind: "candidate",
            entity_id: "bindcraft_PDL1_l65_s585903_mpnn1",
            source: "session",
            attributes: {},
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
    } as Response);
    render(<App />);
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    expect(screen.getByLabelText("Conversation")).toHaveTextContent(
      "Selected operational reference: CD22 (cd22-operational). This reference is for presentation only.",
    );
    expect(screen.getByLabelText("Conversation")).toHaveTextContent(
      `This chat consults session ${prototypeWorkspace.session_id}`,
    );
    expect(screen.getByLabelText("Conversation")).toHaveTextContent(
      "The selected VHH campaign is not sent and is not the corpus of the answer.",
    );
    expect(screen.getByLabelText("Conversation")).not.toHaveTextContent(
      "Sources consulted: the operational reference projection",
    );
    expect(screen.getByLabelText("Conversation")).toHaveTextContent(
      "does not execute tools, approve campaigns, change ranking",
    );
    expect(
      screen.getByText("Questions consult available records. This chat does not execute a campaign."),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("Read-only question"), "what was recorded");
    await user.click(screen.getByRole("button", { name: "Ask read-only" }));
    expect(fetch).toHaveBeenCalledWith("/api/conversation", expect.any(Object));
    const request = vi.mocked(fetch).mock.calls.find(([url]) => String(url) === "/api/conversation")?.[1] as RequestInit;
    const body = JSON.parse(String(request.body));
    expect(body).toEqual({
      session_id: prototypeWorkspace.session_id,
      question: "what was recorded",
      run_id: "protein_run_pdl1_smoke",
      candidate_id: "bindcraft_PDL1_l65_s585903_mpnn1",
    });
    expect(body).not.toHaveProperty("campaign_id");
    expect(JSON.stringify(body)).not.toMatch(/CD22|TACI|7O52|1XU1|cd22-operational|taci-operational/);
    expect(await screen.findByRole("region", { name: "Read-only answer" })).toHaveTextContent(
      "candidate bindcraft_PDL1_l65_s585903_mpnn1 (session)",
    );
  });

  it("keeps session traceability and an HTTP 409 revision conflict", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        status: "revision_conflict",
        text: "The draft has a newer revision. The patch was not applied.",
        session_id: prototypeWorkspace.session_id,
        execution_authorized: false,
        executable: false,
        changed_fields: [],
        questions: [],
        issues: [],
        draft: {
          request_id: "request_1",
          session_id: prototypeWorkspace.session_id,
          revision: 3,
          completeness: "incomplete",
          execution_authorized: false,
          executable: false,
          fields: [],
          issues: [],
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
    render(<App />);
    expect(campaignPreview.contract_version).toBe("vhh-campaign-6c1");
    expect(campaignPreview.campaigns.every((item) => item.generator_version === null)).toBe(true);
    expect(campaignPreview.campaigns.every((item) => item.candidate_count === null)).toBe(true);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "another constraint");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    const review = await screen.findByRole("region", { name: "Design draft review" });
    expect(review).toHaveTextContent("Revision conflict (HTTP 409). The draft was not revised.");
    expect(review).toHaveTextContent("request_id: request_1");
    expect(review).toHaveTextContent("The draft has a newer revision. The patch was not applied.");
    await user.click(screen.getByRole("tab", { name: "Session" }));
    const trace = screen.getByRole("region", { name: "Session trace" });
    expect(trace).toHaveTextContent(prototypeWorkspace.session_id);
    expect(trace).toHaveTextContent("cd22-operational");
    expect(trace).toHaveTextContent("request_1, revision 3");
    expect(trace).toHaveTextContent("Local actions stay in this browser");
    expect(
      vi.mocked(fetch).mock.calls.filter((call) => String(call[1]?.method ?? "GET").toUpperCase() === "POST").map((call) => String(call[0])),
    ).toEqual(["/api/design-draft"]);
  });

  it("saves a campaign and reads it back without a design request", async () => {
    const user = userEvent.setup();
    const saved = {
      campaign_id: "campaign_abc",
      name: "Binder study",
      scientific_objective: "Design a binder against the nominated site.",
      notes: "Bench note",
      created_at: "2026-10-27T00:00:00+00:00",
      updated_at: "2026-10-27T00:00:00+00:00",
      design_request_ids: [],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      const method = String(init?.method ?? "GET").toUpperCase();
      if (method === "GET" && path === "/api/campaigns") {
        return Response.json({ campaigns: [saved] });
      }
      if (method === "POST" && path === "/api/campaigns") {
        return Response.json(saved);
      }
      if (method === "GET" && path === "/api/campaigns/campaign_abc") {
        return Response.json(saved);
      }
      throw new Error(`unexpected ${method} ${path}`);
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Binder study" }));
    const detail = await screen.findByRole("region", { name: "Campaign detail" });
    expect(detail).toHaveTextContent("Design a binder against the nominated site.");
    expect(detail).toHaveTextContent("Bench note");
    expect(detail).toHaveTextContent("No design requests are linked to this campaign.");
    expect(screen.queryByRole("button", { name: "New design request" })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every((call) => String(call[1]?.method ?? "GET").toUpperCase() !== "POST")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Back to campaigns" }));
    await user.click(screen.getByRole("button", { name: "New campaign" }));
    await user.click(screen.getByRole("button", { name: "Save campaign" }));
    expect(screen.getByText("Campaign name is required.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Campaign name"), "Binder study");
    await user.click(screen.getByRole("button", { name: "Save campaign" }));
    expect(screen.getByText("Scientific objective is required.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Scientific objective"), "Design a binder against the nominated site.");
    await user.type(screen.getByLabelText("Notes"), "Bench note");
    await user.click(screen.getByRole("button", { name: "Save campaign" }));
    expect(await screen.findByRole("region", { name: "Campaign detail" })).toHaveTextContent("Binder study");
    const posts = vi.mocked(fetch).mock.calls.filter((call) => String(call[1]?.method ?? "GET").toUpperCase() === "POST");
    expect(posts.map((call) => String(call[0]))).toEqual(["/api/campaigns"]);
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toEqual({
      name: "Binder study",
      scientific_objective: "Design a binder against the nominated site.",
      notes: "Bench note",
    });
  });

  it("opens the configured demonstration by reading the journey", async () => {
    const user = userEvent.setup();
    vi.stubEnv("VITE_CELLERTZ_DEMONSTRATION_JOURNEY_ID", "journey_demo");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      const method = String(init?.method ?? "GET").toUpperCase();
      if (method === "GET" && path === "/api/campaigns") {
        return Response.json({ campaigns: [] });
      }
      if (method === "GET" && path === "/api/attempt-coordinator") {
        return Response.json({ traces: [], limitations: [] });
      }
      if (method === "GET" && path === "/api/design-journeys/journey_demo") {
        return Response.json({
          journey_id: "journey_demo",
          request_id: "request_demo",
          request_revision: 1,
          plan_id: null,
          plan_revision: null,
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
              output: { request_text: "Design a binder." },
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
        });
      }
      throw new Error(`unexpected ${method} ${path}`);
    });
    render(<App />);
    const saved = await screen.findByRole("region", { name: "Saved campaigns" });
    expect(saved).not.toHaveTextContent("PDL1 binder design");
    await user.click(screen.getByRole("button", { name: "Open demonstration" }));
    expect(await screen.findByRole("region", { name: "Saved request" })).toHaveTextContent("Design a binder.");
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === "/api/design-journeys/journey_demo")).toBe(true);
    expect(vi.mocked(fetch).mock.calls.every((call) => String(call[1]?.method ?? "GET").toUpperCase() !== "POST")).toBe(true);
  });
});
