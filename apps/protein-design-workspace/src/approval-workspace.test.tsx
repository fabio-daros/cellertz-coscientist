import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";
import { prototypeWorkspace } from "./fixture";
import { recordedJourney, routeOperationFetches } from "./operation-fetch";

let enqueue: (...items: Array<Response | Promise<Response> | (() => Promise<Response>)>) => void = () => {
  throw new Error("fetch router is not installed");
};

const draftBody = {
  status: "request_complete",
  text: "The request is structurally complete. It is not authorized and not executable.",
  session_id: prototypeWorkspace.session_id,
  execution_authorized: false,
  executable: false,
  changed_fields: ["generator"],
  questions: [],
  issues: [],
  draft: {
    request_id: "request_1",
    session_id: prototypeWorkspace.session_id,
    revision: 4,
    completeness: "complete",
    execution_authorized: false,
    executable: false,
    fields: [{ name: "generator", value: "fixture", provenance: "user" }],
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
};

function approvalBody(
  decision: "approved" | "rejected" | "changes_requested" | "held" = "approved",
) {
  return {
    status: decision === "approved" ? "decision_recorded" : "decision_recorded",
    text:
      decision === "approved"
        ? "A human decision was recorded. It does not execute a campaign."
        : "The decision is not an approval. Nothing was dispatched.",
    session_id: prototypeWorkspace.session_id,
    execution_authorized: false,
    executable: false,
    authorization_status: decision === "approved" ? "not_run" : "authorization_denied",
    preflight_status: "not_run",
    dispatch_status: "not_dispatched",
    approval: {
      approval_id: "approval_1",
      session_id: prototypeWorkspace.session_id,
      request_id: "request_1",
      request_revision: 4,
      revision_digest: "abc123",
      decision,
      reviewer_id: "operator_declared",
      reason: "Fixture demonstration only.",
      recorded_at: "2026-10-05T12:00:00+00:00",
      mode: "phase6d_fixture_only",
      status: "recorded",
      reviewer_identity: "operator_asserted",
      production_authentication: false,
      execution_authorized: false,
      executable: false,
    },
    demonstration: null,
    candidates: [],
    scientific_evidence: [],
    scientific_memory_written: false,
  };
}

function demonstrationBody() {
  return {
    ...approvalBody(),
    status: "demonstration_completed",
    text: "Demonstration dispatch. Not a scientific run. Not scientific evidence. Not biological validation.",
    authorization_status: "authorized",
    preflight_status: "preflight_ready",
    dispatch_status: "completed",
    demonstration: {
      dispatch_id: "dispatch_1",
      approval_id: "approval_1",
      idempotency_key: "dispatch-approval_1",
      execution_profile: "phase6d_fixture_only",
      authorization_status: "authorized",
      preflight_status: "preflight_ready",
      dispatch_status: "completed",
      request_digest: "abc123",
      text: "Demonstration dispatch. Not a scientific run. Not scientific evidence. Not biological validation.",
      candidates: [],
      scientific_evidence: [],
      scientific_memory_written: false,
      scientific_execution: false,
    },
  };
}

function deferredResponse() {
  let resolve: (value: Response) => void = () => {};
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

async function openReview(user: ReturnType<typeof userEvent.setup>) {
  render(<App legacyConsole />);
}

describe("phase 6D design-request review", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn());
    enqueue = routeOperationFetches(vi.mocked(fetch), jsonResponse);
  });

  it("shows the draft, labels, and keeps an incomplete request blocked", async () => {
    const user = userEvent.setup();
    await openReview(user);
    expect(screen.getByLabelText("Declared reviewer ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Reason")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve request" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run approved demonstration" })).toBeDisabled();
    expect(screen.getByText("phase6d_fixture_only")).toBeInTheDocument();
    expect(screen.getByText(/No scientific tool is called/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(["/api/campaigns"]);
    expect(screen.getByText("seed absent; filter_policy absent")).toBeInTheDocument();
  });

  it("shows a recorded seed and filter policy to the reviewer", async () => {
    const user = userEvent.setup();
    const recorded = {
      ...draftBody,
      draft: {
        ...draftBody.draft,
        fields: [
          { name: "seed", value: 7, provenance: "user" },
          { name: "filter_policy", value: "no_filters", provenance: "user" },
        ],
      },
    };
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => recorded } as Response);
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    expect(screen.getByText("seed: 7 (user)")).toBeInTheDocument();
    expect(screen.getByText("filter_policy: no_filters (user)")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    expect(within(review).getByText("seed 7; filter_policy no_filters")).toBeInTheDocument();
  });

  it("records negative decisions on an incomplete draft and keeps dispatch blocked", async () => {
    const user = userEvent.setup();
    const incomplete = {
      ...draftBody,
      status: "request_incomplete",
      draft: { ...draftBody.draft, completeness: "incomplete" as const, revision: 1 },
    };
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).startsWith("/api/design-journeys")) {
        return jsonResponse(recordedJourney);
      }
      const body = JSON.parse(String(init?.body)) as { decision?: "rejected" | "changes_requested" | "held" };
      if (body.decision) {
        return { ok: true, json: async () => approvalBody(body.decision) } as Response;
      }
      return { ok: true, json: async () => incomplete } as Response;
    });
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    expect(within(review).getByRole("button", { name: "Approve request" })).toBeDisabled();
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "The request is missing fields.");
    expect(within(review).getByRole("button", { name: "Approve request" })).toBeDisabled();
    for (const name of ["Reject", "Request changes", "Hold"]) {
      await user.click(within(review).getByRole("button", { name }));
    }
    expect(within(review).getByRole("button", { name: "Run approved demonstration" })).toBeDisabled();
    const approvalCalls = vi.mocked(fetch).mock.calls.filter((call) => call[0] === "/api/design-approval");
    expect(approvalCalls.map((call) => JSON.parse(String(call[1]?.body)).decision)).toEqual([
      "rejected",
      "changes_requested",
      "held",
    ]);
    expect(fetch).not.toHaveBeenCalledWith("/api/approved-execution", expect.anything());
  });

  it("clears a receipt when the draft revision changes", async () => {
    const user = userEvent.setup();
    const revised = {
      ...draftBody,
      draft: { ...draftBody.draft, revision: 5 },
    };
    enqueue(
{ ok: true, json: async () => draftBody } as Response,
{ ok: true, json: async () => approvalBody() } as Response,
{ ok: true, json: async () => revised } as Response,
)
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
    await user.click(within(review).getByRole("button", { name: "Approve request" }));
    await user.click(screen.getByRole("tab", { name: "Session" }));
    expect(await screen.findByText(/approval_1 for request_1 revision 4/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(screen.getByRole("tab", { name: "Session" }));
    expect(screen.getByText(/request_1, revision 5, complete/)).toBeInTheDocument();
    expect(screen.queryByText(/approval_1/)).not.toBeInTheDocument();
  });

  it("discards approval and dispatch replies that resolve after the draft moves", async () => {
    const user = userEvent.setup();
    const revision5 = { ...draftBody, draft: { ...draftBody.draft, revision: 5 } };
    const revision6 = { ...draftBody, draft: { ...draftBody.draft, revision: 6 } };
    const approvalFor4 = deferredResponse();
    const updateTo5 = deferredResponse();
    const dispatchFor5 = deferredResponse();
    const updateTo6 = deferredResponse();
    let draftCalls = 0;
    let approvalCalls = 0;
    vi.mocked(fetch).mockImplementation((url) => {
      if (String(url).startsWith("/api/design-journeys")) {
        return Promise.resolve(jsonResponse(recordedJourney));
      }
      if (url === "/api/design-draft") {
        draftCalls += 1;
        if (draftCalls === 1) return Promise.resolve(jsonResponse(draftBody));
        if (draftCalls === 2) return updateTo5.promise;
        return updateTo6.promise;
      }
      if (url === "/api/design-approval") {
        approvalCalls += 1;
        if (approvalCalls === 1) return approvalFor4.promise;
        const recorded = approvalBody();
        return Promise.resolve(
          jsonResponse({
            ...recorded,
            approval: { ...recorded.approval, request_revision: 5 },
          }),
        );
      }
      return dispatchFor5.promise;
    });
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
    await user.click(within(review).getByRole("button", { name: "Approve request" }));
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(screen.getByRole("tab", { name: "Session" }));
    updateTo5.resolve(jsonResponse(revision5));
    expect(await screen.findByText(/request_1, revision 5, complete/)).toBeInTheDocument();
    await act(async () => {
      approvalFor4.resolve(jsonResponse(approvalBody()));
    });
    expect(screen.queryByText(/approval_1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/A human decision was recorded/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const currentReview = screen.getByRole("region", { name: "Design request review" });
    await user.click(within(currentReview).getByRole("button", { name: "Approve request" }));
    const run = await within(currentReview).findByRole("button", { name: "Run approved demonstration" });
    await user.click(run);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(screen.getByRole("tab", { name: "Session" }));
    updateTo6.resolve(jsonResponse(revision6));
    expect(await screen.findByText(/request_1, revision 6, complete/)).toBeInTheDocument();
    await act(async () => {
      dispatchFor5.resolve(jsonResponse(demonstrationBody()));
    });
    expect(screen.queryByText(/dispatch_1/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Demonstration receipt" })).not.toBeInTheDocument();
    expect(screen.queryByText(/not a scientific run/i)).not.toBeInTheDocument();
  });

  it("separates approval from dispatch and blocks a rejected decision", async () => {
    const user = userEvent.setup();
    enqueue(
{ ok: true, json: async () => draftBody } as Response,
{ ok: true, json: async () => approvalBody("rejected") } as Response,
)
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    expect(within(review).getByText("request_1")).toBeInTheDocument();
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
    await user.click(within(review).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText(/Nothing was dispatched/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run approved demonstration" })).toBeDisabled();
    const calls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(calls.filter((url) => url === "/api/design-draft")).toHaveLength(1);
    expect(
      vi.mocked(fetch).mock.calls.filter(
        (call) =>
          String(call[0]) === "/api/design-journeys" &&
          String(call[1]?.method ?? "GET").toUpperCase() === "POST",
      ),
    ).toHaveLength(1);
    expect(calls.filter((url) => url === "/api/design-approval")).toEqual(["/api/design-approval"]);
    expect(calls).not.toContain("/api/approved-execution");
  });

  it("dispatches once through the approved-execution route and shows the receipt", async () => {
    const user = userEvent.setup();
    enqueue(
{ ok: true, json: async () => draftBody } as Response,
{ ok: true, json: async () => approvalBody() } as Response,
{ ok: true, json: async () => demonstrationBody() } as Response,
)
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
    await user.click(within(review).getByRole("button", { name: "Approve request" }));
    const run = await within(review).findByRole("button", { name: "Run approved demonstration" });
    expect(run).toBeEnabled();
    fireEvent.click(run);
    fireEvent.click(run);
    expect(await screen.findByRole("region", { name: "Demonstration receipt" })).toHaveTextContent(
      /not a scientific run/i,
    );
    expect(screen.getAllByText("No candidate is generated.").length).toBeGreaterThan(0);
    expect(screen.queryByText(/successful scientific run/i)).not.toBeInTheDocument();
    const executionCalls = vi.mocked(fetch).mock.calls.filter((call) => call[0] === "/api/approved-execution");
    expect(executionCalls).toHaveLength(1);
    expect(fetch).not.toHaveBeenCalledWith("/api/conversation", expect.anything());
  });

  it("shows a revision conflict and keeps CD22 and TACI blocked", async () => {
    const user = userEvent.setup();
    enqueue(
{ ok: true, json: async () => draftBody } as Response,
{
        ok: false,
        status: 409,
        json: async () => ({
          ...approvalBody(),
          status: "revision_conflict",
          text: "The draft has a newer revision. The decision was not recorded.",
          approval: null,
          authorization_status: "not_run",
        }),
      } as Response,
)
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
    await user.click(within(review).getByRole("button", { name: "Approve request" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("newer revision");
    await user.click(screen.getByText("Reference configurations"));
    await user.click(screen.getByRole("button", { name: "TACI / PDB 1XU1" }));
    expect(screen.getByText(/source target R → prepared target A/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalledWith("/api/approved-execution", expect.anything());
    const urls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(
      vi.mocked(fetch).mock.calls.filter(
        (call) =>
          String(call[0]) === "/api/design-journeys" &&
          String(call[1]?.method ?? "GET").toUpperCase() === "POST",
      ),
    ).toHaveLength(1);
    expect(urls.filter((url) => url === "/api/design-approval")).toEqual(["/api/design-approval"]);
  });

  it("records one journey after a saved draft and does not repeat it on remount", async () => {
    const user = userEvent.setup();
    enqueue({ ok: true, json: async () => draftBody } as Response);
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    expect(await screen.findByText("No launch recorded.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    const journeyCalls = vi.mocked(fetch).mock.calls.filter(
      (call) =>
        String(call[0]) === "/api/design-journeys" &&
        String(call[1]?.method ?? "GET").toUpperCase() === "POST",
    );
    expect(journeyCalls).toHaveLength(1);
    expect(journeyCalls[0]?.[1]).toMatchObject({ method: "POST" });
  });

  it("does not record a journey when the draft update is a revision conflict", async () => {
    const user = userEvent.setup();
    enqueue({
      ok: false,
      status: 409,
      json: async () => ({
        ...draftBody,
        status: "revision_conflict",
        text: "The draft has a newer revision. The patch was not applied.",
        draft: draftBody.draft,
      }),
    } as Response);
    render(<App legacyConsole />);
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Revision conflict (HTTP 409)");
    expect(
      vi.mocked(fetch).mock.calls
        .filter((call) => String(call[1]?.method ?? "GET").toUpperCase() === "POST")
        .map((call) => String(call[0])),
    ).toEqual(["/api/design-draft"]);
  });

  it("keeps keyboard stage movement and the read-only conversation", async () => {
    const user = userEvent.setup();
    render(<App legacyConsole />);
    screen.getByRole("tab", { name: "Campaigns" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Design request", selected: true })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    expect(screen.getByText(/This chat consults session session_pdl1_prototype/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
      "/api/campaigns",
      "/api/campaigns",
      "/api/design-journeys",
    ]);
  });
});
