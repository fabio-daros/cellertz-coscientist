import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";
import { type ApprovedRunListReply, type ApprovedRunReply, readApprovedArtifact, selectArtifactView, selectRunHistory, selectRunReply } from "./approved-run";
import { prototypeWorkspace } from "./fixture";
import { routeOperationFetches } from "./operation-fetch";

let enqueue: (...items: Array<Response | Promise<Response> | (() => Promise<Response>)>) => void = () => {
  throw new Error("fetch router is not installed");
};

function installOperationFetch() {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn());
  enqueue = routeOperationFetches(vi.mocked(fetch), jsonResponse);
}

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

function approvalBody() {
  return {
    status: "decision_recorded",
    text: "A human decision was recorded. It does not execute a campaign.",
    session_id: prototypeWorkspace.session_id,
    execution_authorized: false,
    executable: false,
    authorization_status: "not_run",
    preflight_status: "not_run",
    dispatch_status: "not_dispatched",
    approval: {
      approval_id: "approval_1",
      session_id: prototypeWorkspace.session_id,
      request_id: "request_1",
      request_revision: 4,
      revision_digest: "abc123",
      decision: "approved",
      reviewer_id: "operator_declared",
      reason: "Fixture demonstration only.",
      recorded_at: "2026-10-08T12:00:00+00:00",
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

function runBody(status: "queued" | "running" | "completed" | "failed", attempt = 1): ApprovedRunReply {
  const events = [
    {
      sequence: 1,
      status: "queued",
      recorded_at: "2026-10-08T12:00:00+00:00",
      attempt,
      text: "Fixture run queued.",
      origin: "server",
    },
  ];
  if (status !== "queued") {
    events.push({
      sequence: 2,
      status: "running",
      recorded_at: "2026-10-08T12:00:01+00:00",
      attempt,
      text: "Fixture runner started.",
      origin: "server",
    });
  }
  if (status === "completed" || status === "failed") {
    events.push({
      sequence: 3,
      status,
      recorded_at: "2026-10-08T12:00:02+00:00",
      attempt,
      text:
        status === "completed"
          ? "Fixture-only run completed. Not scientific validation."
          : "The fixture run failed. No scientific action was taken.",
      origin: "server",
    });
  }
  return {
    status: status === "failed" ? "run_failed" : status === "completed" ? "run_completed" : "run_observed",
    text:
      status === "failed"
        ? "The fixture run failed. No scientific action was taken."
        : "Fixture-only run. Not scientific validation.",
    session_id: prototypeWorkspace.session_id,
    execution_authorized: false,
    executable: false,
    scientific_validation: false,
    scientific_candidate: false,
    candidates: [],
    scientific_evidence: [],
    scientific_memory_written: false,
    semantic_promotion: false,
    run: {
      run_id: "run_00000000000000000000000000000001",
      session_id: prototypeWorkspace.session_id,
      approval_id: "approval_1",
      request_id: "request_1",
      request_revision: 4,
      request_digest: "abc123",
      execution_profile: "phase6d_fixture_only",
      status,
      attempt,
      created_at: "2026-10-08T12:00:00+00:00",
      updated_at: "2026-10-08T12:00:02+00:00",
      events,
      artifacts:
        status === "completed"
          ? [
              {
                artifact_id: "artifact_run_00000000000000000000000000000001_1_fixture_receipt",
                kind: "fixture_receipt",
                content_sha256: "aa",
                size_bytes: 12,
                media_type: "application/json",
                label: "fixture operational artifact",
                fixture: true,
                scientific_evidence: false,
                scientific_candidate: false,
              },
            ]
          : [],
      budget: {
        token_ceiling: null,
        model_cost_ceiling: null,
        scientific_tool_cost_ceiling: null,
        authorizes_execution: false,
      },
      usage: {
        estimated_model_cost: null,
        scientific_infrastructure_cost: null,
        currency: null,
        observed_model_calls: 0,
        observed_scientific_tool_calls: 0,
        observed_model_cost: 0,
        observed_scientific_tool_cost: 0,
        observed_costs_known: true,
        authorizes_execution: false,
      },
      fixture: true,
      scientific_validation: false,
      scientific_execution: false,
      label: "fixture-only run",
      text:
        status === "failed"
          ? "The fixture run failed. No scientific action was taken."
          : "Fixture-only run. Not scientific validation.",
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function deferredResponse() {
  let resolve: (value: Response) => void = () => {};
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function demonstrate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: "Design request" }));
  await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
  await user.click(screen.getByRole("button", { name: "Save design request" }));
  await user.click(screen.getByRole("tab", { name: "Campaigns" }));
  const review = screen.getByRole("region", { name: "Design request review" });
  await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
  await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
  await user.click(within(review).getByRole("button", { name: "Approve request" }));
  await user.click(within(review).getByRole("button", { name: "Run approved demonstration" }));
  expect(await screen.findByRole("region", { name: "Demonstration receipt" })).toHaveTextContent(
    /not a scientific run/i,
  );
}

describe("phase 6G asynchronous fixture run monitoring", () => {
  beforeEach(() => {
    installOperationFetch();
  });

  it("requires the current demonstration before creating a fixture run", async () => {
    const user = userEvent.setup();
    render(<App legacyConsole />);
    expect(screen.getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    expect(screen.getByText("Not scientific validation.")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(["/api/campaigns"]);
  });

  it("keeps the fixture run separate from the demonstration receipt", async () => {
    const user = userEvent.setup();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("completed")),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    expect(within(fixture).getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    expect(timeline).toHaveTextContent("run_00000000000000000000000000000001");
    expect(timeline).toHaveTextContent("Not scientific validation.");
    expect(screen.getByRole("region", { name: "Demonstration receipt" })).toHaveTextContent(/not a scientific run/i);
    expect(timeline).not.toHaveTextContent("dispatch_1");
    const createCalls = vi.mocked(fetch).mock.calls.filter((call) => call[0] === "/api/approved-runs");
    expect(createCalls).toHaveLength(1);
    expect(JSON.parse(String(createCalls[0]?.[1]?.body))).toEqual({
      session_id: prototypeWorkspace.session_id,
      approval_id: "approval_1",
      idempotency_key: "run-approval_1",
    });
  });

  it("shows queued, running, completed, and failed states without scientific claims", async () => {
    const user = userEvent.setup();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("queued")),
jsonResponse(runBody("running")),
jsonResponse(runBody("completed")),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    expect(within(timeline).getByText("queued")).toBeInTheDocument();
    expect(within(timeline).queryByRole("button", { name: "Retry fixture run" })).not.toBeInTheDocument();
    await user.click(within(timeline).getByRole("button", { name: "Refresh fixture run" }));
    expect(await within(timeline).findByText("running")).toBeInTheDocument();
    await user.click(within(timeline).getByRole("button", { name: "Refresh fixture run" }));
    expect(await within(timeline).findByText("completed")).toBeInTheDocument();
    expect(timeline).toHaveTextContent("1. queued");
    expect(timeline).toHaveTextContent("2. running");
    expect(timeline).toHaveTextContent("3. completed");
    expect(timeline).toHaveTextContent("fixture operational artifact");
    expect(timeline).toHaveTextContent("Not scientific evidence");
    expect(timeline).toHaveTextContent("Not recorded");
    expect(within(timeline).queryByText(/scientific candidate/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/validated protein/i)).not.toBeInTheDocument();
    expect(within(timeline).queryByRole("button", { name: "Retry fixture run" })).not.toBeInTheDocument();
    const refreshCalls = vi.mocked(fetch).mock.calls.filter((call) => String(call[0]).startsWith("/api/approved-runs/"));
    expect(refreshCalls).toHaveLength(2);
    expect(refreshCalls.every((call) => call[1]?.method === "GET")).toBe(true);
  });

  it("polls queued and running fixture runs until the worker reaches a terminal state", async () => {
    const user = userEvent.setup();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("queued"), 202),
jsonResponse(runBody("running")),
jsonResponse(runBody("completed")),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    expect(within(timeline).getByRole("status")).toHaveTextContent(/updates automatically/i);
    await waitFor(() => expect(within(timeline).getByText("completed")).toBeInTheDocument(), {
      timeout: 2000,
    });
    const reads = vi.mocked(fetch).mock.calls.filter((call) => call[1]?.method === "GET");
    expect(reads).toHaveLength(2);
    expect(within(timeline).queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps the visible run when a poll fails and only reads", async () => {
    const user = userEvent.setup();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("queued"), 202),
() => Promise.reject(new Error("Traceback /tmp/secret TOKEN")),
jsonResponse(runBody("completed")),
);
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    await waitFor(() => expect(within(timeline).getByText("completed")).toBeInTheDocument(), {
      timeout: 2000,
    });
    expect(timeline.textContent).not.toContain("TOKEN");
    expect(timeline.textContent).not.toContain("/tmp/secret");
    const reads = vi.mocked(fetch).mock.calls.filter((call) => call[1]?.method === "GET");
    expect(reads.length).toBeGreaterThanOrEqual(1);
    expect(reads.every((call) => call[1]?.method === "GET")).toBe(true);
  });

  it("shows a safe failure and retries once", async () => {
    const user = userEvent.setup();
    const pending = deferredResponse();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("failed")),
pending.promise,
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    expect(within(timeline).getByText("failed")).toBeInTheDocument();
    expect(within(timeline).getByRole("alert")).toHaveTextContent(
      "The fixture run failed. No scientific action was taken.",
    );
    const retry = within(timeline).getByRole("button", { name: "Retry fixture run" });
    fireEvent.click(retry);
    fireEvent.click(retry);
    await act(async () => {
      pending.resolve(jsonResponse(runBody("completed", 2)));
    });
    expect(await within(timeline).findByText("completed")).toBeInTheDocument();
    expect(within(timeline).getByText("2")).toBeInTheDocument();
    const retryCalls = vi.mocked(fetch).mock.calls.filter((call) => String(call[0]).endsWith("/retry"));
    expect(retryCalls).toHaveLength(1);
    expect(JSON.parse(String(retryCalls[0]?.[1]?.body))).toEqual({
      session_id: prototypeWorkspace.session_id,
      idempotency_key: "retry-run_00000000000000000000000000000001-1",
    });
    expect(JSON.parse(String(retryCalls[0]?.[1]?.body))).not.toHaveProperty("attempt");
    expect(JSON.parse(String(retryCalls[0]?.[1]?.body))).not.toHaveProperty("status");
  });

  it("blocks another run action while one is in flight and keeps a failed refresh", async () => {
    const user = userEvent.setup();
    const refreshPending = deferredResponse();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("failed")),
refreshPending.promise,
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    fireEvent.click(within(timeline).getByRole("button", { name: "Refresh fixture run" }));
    fireEvent.click(within(timeline).getByRole("button", { name: "Retry fixture run" }));
    fireEvent.click(screen.getByRole("button", { name: "Create fixture run" }));
    expect(within(timeline).getByRole("button", { name: "Refresh fixture run" })).toBeDisabled();
    expect(within(timeline).getByRole("button", { name: "Retry fixture run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    await act(async () => {
      refreshPending.resolve(jsonResponse(runBody("failed", 1)));
    });
    expect(within(timeline).getByText("failed")).toBeInTheDocument();
    expect(within(timeline).getByRole("alert")).toHaveTextContent(
      "The fixture run failed. No scientific action was taken.",
    );
    const retryCalls = vi.mocked(fetch).mock.calls.filter((call) => String(call[0]).endsWith("/retry"));
    const createCalls = vi.mocked(fetch).mock.calls.filter((call) => call[0] === "/api/approved-runs");
    expect(retryCalls).toHaveLength(0);
    expect(createCalls).toHaveLength(1);
  });

  it("keeps the retried attempt when an older refresh resolves later", async () => {
    const user = userEvent.setup();
    const retryPending = deferredResponse();
    const refreshPending = deferredResponse();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("failed")),
retryPending.promise,
refreshPending.promise,
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const timeline = await screen.findByRole("region", { name: "Fixture run timeline" });
    fireEvent.click(within(timeline).getByRole("button", { name: "Retry fixture run" }));
    fireEvent.click(within(timeline).getByRole("button", { name: "Refresh fixture run" }));
    await act(async () => {
      retryPending.resolve(jsonResponse(runBody("completed", 2)));
    });
    expect(within(timeline).getByText("completed")).toBeInTheDocument();
    const readsBeforeRefresh = vi
      .mocked(fetch)
      .mock.calls.filter((call) => call[1]?.method === "GET");
    expect(readsBeforeRefresh).toHaveLength(0);
    await user.click(within(timeline).getByRole("button", { name: "Refresh fixture run" }));
    await act(async () => {
      refreshPending.resolve(jsonResponse(runBody("failed", 1)));
    });
    const reads = vi.mocked(fetch).mock.calls.filter((call) => call[1]?.method === "GET");
    expect(reads).toHaveLength(1);
    expect(reads[0]?.[1]?.method).toBe("GET");
    expect(within(timeline).getByText("completed")).toBeInTheDocument();
    expect(within(timeline).getByText("2")).toBeInTheDocument();
    expect(within(timeline).queryByRole("alert")).not.toBeInTheDocument();
    const kept = selectRunReply(runBody("completed", 2), runBody("failed", 1), 1, 2);
    expect(kept?.run?.attempt).toBe(2);
    expect(kept?.run?.status).toBe("completed");
    const sameAttempt = selectRunReply(runBody("completed", 2), runBody("running", 2), 3, 3);
    expect(sameAttempt?.run?.status).toBe("completed");
  });

  it("does not create a run while navigating and drops a stale run reply", async () => {
    const user = userEvent.setup();
    const pending = deferredResponse();
    const revised = { ...draftBody, draft: { ...draftBody.draft, revision: 5 } };
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
pending.promise,
jsonResponse(revised),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    fireEvent.click(screen.getByRole("button", { name: "Create fixture run" }));
    await user.click(screen.getByRole("tab", { name: "Session" }));
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await act(async () => {
      pending.resolve(jsonResponse(runBody("completed")));
    });
    expect(screen.queryByText("run_00000000000000000000000000000001")).not.toBeInTheDocument();
    const runPosts = vi.mocked(fetch).mock.calls.filter(
      (call) => call[0] === "/api/approved-runs" && call[1]?.method === "POST",
    );
    expect(runPosts).toHaveLength(1);
  });

  it("clears the fixture run when the draft revision changes", async () => {
    const user = userEvent.setup();
    const revised = { ...draftBody, draft: { ...draftBody.draft, revision: 5 } };
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("completed")),
jsonResponse(revised),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    expect(await screen.findByText("run_00000000000000000000000000000001")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Design request" }));
    await user.click(screen.getByRole("button", { name: "Edit request" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(screen.getByRole("tab", { name: "Session" }));
    expect(await screen.findByText(/request_1, revision 5, complete/)).toBeInTheDocument();
    expect(screen.queryByText("run_00000000000000000000000000000001")).not.toBeInTheDocument();
    expect(screen.getByText(/Fixture run:/)).toHaveTextContent("none");
  });

  it("preserves blocked campaigns, read-only chat, conflicts, and keyboard focus", async () => {
    const user = userEvent.setup();
    render(<App legacyConsole />);
    await user.click(screen.getByText("Reference configurations"));
    expect(screen.getAllByText(/CD22/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/TACI/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Approve & Run" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Research campaigns" })).not.toHaveTextContent(
      "mBER version is not recorded. Execution is not authorized.",
    );
    const campaign = screen.getByRole("tab", { name: "Campaigns" });
    campaign.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Design request" })).toHaveFocus();
    enqueue(
jsonResponse(draftBody),
jsonResponse(
          {
            ...approvalBody(),
            status: "revision_conflict",
            text: "The draft has a newer revision. The decision was not recorded.",
            approval: null,
            authorization_status: "not_run",
          },
          409,
        ),
jsonResponse({
          status: "answered",
          text: "Read-only answer.",
          session_id: prototypeWorkspace.session_id,
          run_id: prototypeWorkspace.runs[0]?.run_id,
          read_only: true,
          evidence: [],
          instrumentation: {
            interaction_id: "interaction_1",
            recorded: false,
            total_tokens: null,
            observation: "no_model_usage",
            event: null,
          },
        }),
)
    await user.type(screen.getByLabelText("Design intent"), "design a fixture binder");
    await user.click(screen.getByRole("button", { name: "Save design request" }));
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    const review = screen.getByRole("region", { name: "Design request review" });
    await user.type(within(review).getByLabelText("Declared reviewer ID"), "operator_declared");
    await user.type(within(review).getByLabelText("Reason"), "Fixture demonstration only.");
    await user.click(within(review).getByRole("button", { name: "Approve request" }));
    expect(await within(review).findByRole("alert")).toHaveTextContent(/newer revision/);
    expect(screen.getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    await user.type(screen.getByLabelText("Read-only question"), "What does the fixture record?");
    await user.click(screen.getByRole("button", { name: "Ask read-only" }));
    expect(await screen.findByText("Read-only answer.")).toBeInTheDocument();
    const conversation = vi.mocked(fetch).mock.calls.find((call) => call[0] === "/api/conversation");
    expect(JSON.parse(String(conversation?.[1]?.body))).toEqual({
      session_id: prototypeWorkspace.session_id,
      question: "What does the fixture record?",
      run_id: prototypeWorkspace.runs[0]?.run_id,
      candidate_id: prototypeWorkspace.candidates[0]?.candidate_id,
    });
    expect(fetch).not.toHaveBeenCalledWith("/api/approved-runs", expect.anything());
  });
});

function historyBody(updatedAt: string, attempt = 1): ApprovedRunListReply {
  return {
    status: "runs_observed",
    text: "Fixture-only runs. Not scientific validation.",
    session_id: prototypeWorkspace.session_id,
    execution_authorized: false,
    executable: false,
    scientific_validation: false,
    scientific_candidate: false,
    candidates: [],
    scientific_evidence: [],
    scientific_memory_written: false,
    semantic_promotion: false,
    label: "fixture-only run",
    runs: [
      {
        run_id: "run_00000000000000000000000000000001",
        request_id: "request_1",
        approval_id: "approval_1",
        revision: 4,
        status: "completed",
        attempt,
        created_at: "2026-10-08T12:00:00+00:00",
        updated_at: updatedAt,
        text: "Fixture-only run completed. Not scientific validation.",
        artifact_count: 1,
        fixture: true,
        scientific_validation: false,
        label: "fixture-only run",
      },
    ],
  };
}

function artifactResponse(text: string): Response {
  const encoded = new TextEncoder().encode(text);
  const body = new ArrayBuffer(encoded.byteLength);
  new Uint8Array(body).set(encoded);
  return {
    ok: true,
    status: 200,
    headers: {
      get(name: string) {
        if (name === "content-type") {
          return "text/markdown";
        }
        if (name === "content-disposition") {
          return 'attachment; filename="fixture-fixture_summary.md"';
        }
        if (name === "content-length") {
          return String(body.byteLength);
        }
        return null;
      },
    },
    arrayBuffer: async () => body,
    text: async () => text,
    json: async () => ({}),
  } as Response;
}

describe("phase 6H fixture run history and artifacts", () => {
  beforeEach(() => {
    installOperationFetch();
  });

  it("accepts a shorter artifact only for the current selection and generation", () => {
    const older = historyBody("2026-10-08T12:00:00+00:00", 1);
    const newer = historyBody("2026-10-08T12:00:05+00:00", 2);
    expect(selectRunHistory(newer, older, 1, 1)).toBe(newer);
    expect(selectRunHistory(newer, older, 1, 2)).toBe(newer);
    expect(selectRunHistory(null, newer, 2, 2)).toBe(newer);
    const selection = {
      run_id: "run_00000000000000000000000000000001",
      artifact_id: "artifact_run_00000000000000000000000000000001_1_fixture_summary",
      attempt: 1,
    };
    const current = { ...selection, text: "fixture operational artifact\nfull text" };
    const shorter = { ...current, text: "short" };
    expect(selectArtifactView(current, shorter, 1, 1, selection)).toBe(shorter);
    expect(selectArtifactView(current, shorter, 1, 2, selection)).toBe(current);
    expect(selectArtifactView(current, { ...shorter, artifact_id: "other" }, 1, 1, selection)).toBe(current);
    expect(selectArtifactView(current, { ...shorter, attempt: 2 }, 1, 1, selection)).toBe(current);
    expect(selectArtifactView(current, { ...shorter, run_id: "run_other" }, 1, 1, selection)).toBe(current);
    expect(selectArtifactView(current, shorter, 1, 1, null)).toBe(current);
  });

  it("lists the session history and views a textual operational artifact", async () => {
    const user = userEvent.setup();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("completed")),
jsonResponse(historyBody("2026-10-08T12:00:02+00:00")),
artifactResponse(
          "# Fixture-only run\nfixture operational artifact\nnot scientific validation\nno scientific action was taken\nno candidate protein was generated\n",
        ),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const fixture = await screen.findByRole("region", { name: "Fixture run" });
    expect(within(fixture).getByRole("button", { name: "View fixture_receipt" })).toBeEnabled();
    expect(within(fixture).getByRole("button", { name: "Download fixture_receipt" })).toBeEnabled();
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    expect(await within(fixture).findByRole("list", { name: "Fixture runs" })).toHaveTextContent("completed");
    expect(fixture).toHaveTextContent("Operational artifacts 1");
    expect(fixture).toHaveTextContent("No candidate protein was generated.");
    await user.click(within(fixture).getByRole("button", { name: "View fixture_receipt" }));
    const viewer = await within(fixture).findByRole("region", { name: "Operational artifact" });
    expect(viewer).toHaveTextContent("no candidate protein was generated");
    expect(viewer).not.toHaveTextContent("sqlite");
    const historyCall = vi.mocked(fetch).mock.calls.find((call) => call[0] === "/api/approved-runs" && call[1]?.method === "GET");
    expect(historyCall?.[0]).toBe("/api/approved-runs");
    expect(String(historyCall?.[0])).not.toContain("session_id");
  });

  it("keeps a newer visible attempt when an older history read returns", async () => {
    const user = userEvent.setup();
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("completed", 2)),
jsonResponse(historyBody("2026-10-08T12:00:02+00:00", 2)),
jsonResponse(runBody("completed", 1)),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const fixture = await screen.findByRole("region", { name: "Fixture run" });
    expect(within(fixture).getByText("Attempt").parentElement).toHaveTextContent("2");
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: /Open run_/ }));
    expect(within(fixture).getByText("Attempt").parentElement).toHaveTextContent("2");
    expect(within(fixture).queryByText("Traceback")).not.toBeInTheDocument();
  });

  it("does not show an in-flight artifact after the selection changes", async () => {
    const user = userEvent.setup();
    const completed = runBody("completed");
    const receipt = completed.run?.artifacts[0];
    if (!completed.run || !receipt) {
      throw new Error("fixture run is missing");
    }
    completed.run.artifacts = [
      receipt,
      {
        ...receipt,
        artifact_id: "artifact_run_00000000000000000000000000000001_1_fixture_summary",
        kind: "fixture_summary",
        media_type: "text/markdown",
      },
    ];
    let releaseReceipt: (response: Response) => void = () => {};
    const receiptResponse = new Promise<Response>((resolve) => {
      releaseReceipt = resolve;
    });
    const createObjectURL = vi.fn(() => "blob:fixture");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(completed),
receiptResponse,
artifactResponse("CURRENT SUMMARY"),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const fixture = await screen.findByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "View fixture_receipt" }));
    await user.click(within(fixture).getByRole("button", { name: "View fixture_summary" }));
    const viewer = await within(fixture).findByRole("region", { name: "Operational artifact" });
    expect(viewer).toHaveTextContent("CURRENT SUMMARY");
    await act(async () => {
      releaseReceipt(artifactResponse("STALE RECEIPT LONGER THAN THE SUMMARY"));
    });
    expect(viewer).toHaveTextContent("CURRENT SUMMARY");
    expect(viewer).not.toHaveTextContent("STALE RECEIPT");
    let releaseDownload: (response: Response) => void = () => {};
    enqueue(
new Promise<Response>((resolve) => {
            releaseDownload = resolve;
          }),
artifactResponse("CURRENT SUMMARY"),
)
    await user.click(within(fixture).getByRole("button", { name: "Download fixture_receipt" }));
    await user.click(within(fixture).getByRole("button", { name: "View fixture_summary" }));
    await act(async () => {
      releaseDownload(artifactResponse("STALE DOWNLOAD"));
    });
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(viewer).toHaveTextContent("CURRENT SUMMARY");
    expect(viewer).not.toHaveTextContent("STALE DOWNLOAD");
  });

  it("drops an in-flight artifact when the visible run changes", async () => {
    const user = userEvent.setup();
    const runA = "run_00000000000000000000000000000001";
    const runB = "run_00000000000000000000000000000002";
    const history = historyBody("2026-10-08T12:00:02+00:00");
    history.runs.push({
      ...history.runs[0],
      run_id: runB,
      approval_id: "approval_2",
      updated_at: "2026-10-08T12:00:03+00:00",
    });
    const other = runBody("completed");
    if (!other.run) {
      throw new Error("fixture run is missing");
    }
    other.run.run_id = runB;
    other.run.approval_id = "approval_2";
    other.run.artifacts = [
      {
        ...other.run.artifacts[0],
        artifact_id: `artifact_${runB}_1_fixture_receipt`,
      },
    ];
    let releaseArtifact: (response: Response) => void = () => {};
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("completed")),
jsonResponse(history),
new Promise<Response>((resolve) => {
            releaseArtifact = resolve;
          }),
jsonResponse(other),
jsonResponse(runBody("completed")),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const fixture = await screen.findByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(within(fixture).getByRole("button", { name: "View fixture_receipt" }));
    await user.click(await within(fixture).findByRole("button", { name: `Open ${runB}` }));
    expect(await within(fixture).findByText(runB)).toBeInTheDocument();
    await act(async () => {
      releaseArtifact(artifactResponse("STALE FROM THE PREVIOUS RUN"));
    });
    await user.click(within(fixture).getByRole("button", { name: `Open ${runA}` }));
    const active = within(fixture).getByRole("region", { name: "Fixture run timeline" });
    expect(within(active).getByText(runA)).toBeInTheDocument();
    expect(within(fixture).queryByRole("region", { name: "Operational artifact" })).not.toBeInTheDocument();
    expect(within(fixture).queryByRole("region", { name: "Historical artifact" })).not.toBeInTheDocument();
    expect(fixture).not.toHaveTextContent("STALE FROM THE PREVIOUS RUN");
  });

  it("opens a historical run from another request without changing the active run", async () => {
    const user = userEvent.setup();
    const runA = "run_00000000000000000000000000000001";
    const runB = "run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const runC = "run_cccccccccccccccccccccccccccccccc";
    const history = historyBody("2026-10-08T12:00:02+00:00");
    history.runs[0] = { ...history.runs[0], status: "failed", text: "The fixture run failed. No scientific action was taken." };
    history.runs.push(
      {
        ...history.runs[0],
        run_id: runB,
        request_id: "request_b",
        approval_id: "approval_b",
        revision: 9,
        status: "completed",
        updated_at: "2026-10-08T12:00:03+00:00",
      },
      {
        ...history.runs[0],
        run_id: runC,
        request_id: "request_c",
        approval_id: "approval_c",
        revision: 3,
        status: "completed",
        updated_at: "2026-10-08T12:00:04+00:00",
      },
    );
    const foreign = (runId: string, requestId: string, revision: number) => {
      const body = runBody("completed");
      if (!body.run) {
        throw new Error("fixture run is missing");
      }
      body.run.run_id = runId;
      body.run.request_id = requestId;
      body.run.request_revision = revision;
      body.run.artifacts = [];
      return body;
    };
    let releaseB: (response: Response) => void = () => {};
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("failed")),
jsonResponse(history),
new Promise<Response>((resolve) => {
            releaseB = resolve;
          }),
jsonResponse(foreign(runC, "request_c", 3)),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const fixture = await screen.findByRole("region", { name: "Fixture run" });
    const active = within(fixture).getByRole("region", { name: "Fixture run timeline" });
    expect(within(active).getByText(runA)).toBeInTheDocument();
    expect(within(active).getByText("failed")).toBeInTheDocument();
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: `Open ${runB}` }));
    await user.click(within(fixture).getByRole("button", { name: `Open ${runC}` }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    expect(within(historical).getByText(runC)).toBeInTheDocument();
    expect(within(historical).getByText(/request_c revision 3/)).toBeInTheDocument();
    expect(within(historical).queryByRole("button", { name: "Retry fixture run" })).not.toBeInTheDocument();
    expect(within(active).getByText(runA)).toBeInTheDocument();
    expect(within(active).getByText("failed")).toBeInTheDocument();
    expect(within(fixture).getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    expect(within(fixture).getByRole("button", { name: "Retry fixture run" })).toBeEnabled();
    await act(async () => {
      releaseB(jsonResponse(foreign(runB, "request_b", 9)));
    });
    expect(within(historical).getByText(runC)).toBeInTheDocument();
    expect(within(historical).queryByText(runB)).not.toBeInTheDocument();
    expect(within(active).getByText(runA)).toBeInTheDocument();
    const callsBeforeRetry = vi.mocked(fetch).mock.calls.length;
    enqueue(
jsonResponse(runBody("queued", 2)),
)
    await user.click(within(fixture).getByRole("button", { name: "Retry fixture run" }));
    const retryCall = vi.mocked(fetch).mock.calls[callsBeforeRetry];
    expect(String(retryCall?.[0])).toContain(`/api/approved-runs/${runA}/retry`);
    expect(String(retryCall?.[0])).not.toContain(runB);
    expect(String(retryCall?.[0])).not.toContain(runC);
    expect(retryCall?.[1]).toMatchObject({ method: "POST" });
    await user.click(within(fixture).getByRole("button", { name: "Return to current run" }));
    expect(within(fixture).queryByRole("region", { name: "Historical run" })).not.toBeInTheDocument();
    expect(within(active).getByText(runA)).toBeInTheDocument();
  });

  it("rejects an artifact body that breaks the response contract", async () => {
    const runId = "run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const artifactId = `artifact_${runId}_1_operational_log`;
    const accepted = artifactResponse("fixture");
    enqueue(
{
      ...accepted,
      headers: {
        get(name: string) {
          if (name === "content-type") return "text/plain";
          if (name === "content-disposition") return 'attachment; filename="fixture-operational_log.txt"';
          return accepted.headers.get(name);
        },
      },
    } as Response,
)
    const ok = await readApprovedArtifact(runId, artifactId);
    expect(ok).toMatchObject({ ok: true, text: "fixture", filename: "fixture-operational_log.txt" });

    async function rejected(response: Response) {
      enqueue(
response,
)
      const result = await readApprovedArtifact(runId, artifactId);
      expect(result).toEqual({ ok: false, text: "The operational artifact could not be read." });
    }
    const unread = () =>
      ({
        ok: true,
        status: 200,
        headers: {
          get(name: string) {
            if (name === "content-type") return "text/plain; charset=utf-8";
            if (name === "content-disposition") return 'attachment; filename="fixture-operational_log.txt"';
            if (name === "content-length") return "65537";
            return null;
          },
        },
        arrayBuffer: async () => {
          throw new Error("rejected content was read");
        },
      }) as unknown as Response;
    await rejected(unread());
    await rejected({
      ...unread(),
      headers: {
        get(name: string) {
          if (name === "content-type") return "text/plain";
          if (name === "content-disposition") return 'attachment; filename="../secret.txt"';
          if (name === "content-length") return "4";
          return null;
        },
      },
    } as unknown as Response);
    await rejected({
      ...artifactResponse("fixture"),
      headers: {
        get(name: string) {
          if (name === "content-type") return "text/plain";
          if (name === "content-disposition") return 'attachment; filename="fixture-operational_log.txt"';
          if (name === "content-length") return "01";
          return null;
        },
      },
    } as Response);
    await rejected({
      ...artifactResponse("fixture"),
      headers: {
        get(name: string) {
          if (name === "content-type") return "text/plain";
          if (name === "content-disposition") return 'attachment; filename="fixture-operational_log.txt"';
          if (name === "content-length") return "1";
          return null;
        },
      },
    } as Response);
  });

  it("disables artifact actions until a completed run is visible", async () => {
    const user = userEvent.setup();
    render(<App legacyConsole />);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    expect(within(fixture).getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    expect(within(fixture).queryByRole("button", { name: /View / })).not.toBeInTheDocument();
    expect(fixture).toHaveTextContent("Refresh run history to load runs.");
  });

  function downloadSpies() {
    const createObjectURL = vi.fn(() => "blob:historical");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    return { createObjectURL, click };
  }

  function rejectedArtifact(): Response {
    const accepted = artifactResponse("fixture");
    return {
      ...accepted,
      headers: {
        get(name: string) {
          if (name === "content-length") {
            return "1";
          }
          return accepted.headers.get(name);
        },
      },
    } as Response;
  }

  it("views and downloads a historical artifact when no active run is loaded", async () => {
    const user = userEvent.setup();
    const spies = downloadSpies();
    const pending = deferredResponse();
    enqueue(
jsonResponse(historyBody("2026-10-08T12:00:02+00:00")),
jsonResponse(runBody("completed")),
pending.promise,
artifactResponse("HISTORICAL DOWNLOAD"),
)
    render(<App legacyConsole />);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    expect(within(fixture).queryByRole("region", { name: "Fixture run timeline" })).not.toBeInTheDocument();
    expect(within(fixture).getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    expect(within(fixture).queryByRole("button", { name: "Retry fixture run" })).not.toBeInTheDocument();
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: /Open run_/ }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    expect(within(historical).getByRole("button", { name: "Download fixture_receipt" })).toBeEnabled();
    await user.click(within(historical).getByRole("button", { name: "View fixture_receipt" }));
    expect(within(historical).getByRole("status")).toHaveTextContent("Loading operational artifact.");
    expect(within(historical).queryByRole("region", { name: "Historical artifact" })).not.toBeInTheDocument();
    await act(async () => {
      pending.resolve(artifactResponse("HISTORICAL RECEIPT"));
    });
    expect(await within(historical).findByRole("region", { name: "Historical artifact" })).toHaveTextContent(
      "HISTORICAL RECEIPT",
    );
    await user.click(within(historical).getByRole("button", { name: "Download fixture_receipt" }));
    await waitFor(() => expect(spies.createObjectURL).toHaveBeenCalledTimes(1));
    expect(spies.click).toHaveBeenCalledTimes(1);
    expect(within(historical).getByRole("region", { name: "Historical artifact" })).toHaveTextContent(
      "HISTORICAL RECEIPT",
    );
  });

  it("shows a historical artifact failure without creating a download", async () => {
    const user = userEvent.setup();
    const spies = downloadSpies();
    enqueue(
jsonResponse(historyBody("2026-10-08T12:00:02+00:00")),
jsonResponse(runBody("completed")),
rejectedArtifact(),
)
    render(<App legacyConsole />);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: /Open run_/ }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    await user.click(within(historical).getByRole("button", { name: "Download fixture_receipt" }));
    expect(await within(historical).findByRole("alert")).toHaveTextContent(
      "The operational artifact could not be read.",
    );
    expect(within(historical).queryByRole("region", { name: "Historical artifact" })).not.toBeInTheDocument();
    expect(spies.createObjectURL).not.toHaveBeenCalled();
    expect(spies.click).not.toHaveBeenCalled();
  });

  it("ignores a late historical download after returning to the current run", async () => {
    const user = userEvent.setup();
    const spies = downloadSpies();
    const pending = deferredResponse();
    enqueue(
jsonResponse(historyBody("2026-10-08T12:00:02+00:00")),
jsonResponse(runBody("completed")),
pending.promise,
)
    render(<App legacyConsole />);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: /Open run_/ }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    await user.click(within(historical).getByRole("button", { name: "Download fixture_receipt" }));
    await user.click(within(historical).getByRole("button", { name: "Return to current run" }));
    await act(async () => {
      pending.resolve(artifactResponse("LATE DOWNLOAD"));
    });
    expect(within(fixture).queryByRole("region", { name: "Historical run" })).not.toBeInTheDocument();
    expect(spies.createObjectURL).not.toHaveBeenCalled();
    expect(spies.click).not.toHaveBeenCalled();
    expect(fixture).not.toHaveTextContent("LATE DOWNLOAD");
  });

  it("ignores a late historical download after opening another run", async () => {
    const user = userEvent.setup();
    const spies = downloadSpies();
    const runB = "run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const history = historyBody("2026-10-08T12:00:02+00:00");
    history.runs.push({
      ...history.runs[0],
      run_id: runB,
      request_id: "request_b",
      approval_id: "approval_b",
      revision: 9,
      updated_at: "2026-10-08T12:00:03+00:00",
    });
    const other = runBody("completed");
    if (!other.run) {
      throw new Error("fixture run is missing");
    }
    other.run.run_id = runB;
    other.run.request_id = "request_b";
    other.run.request_revision = 9;
    other.run.artifacts = [
      {
        ...other.run.artifacts[0],
        artifact_id: `artifact_${runB}_1_fixture_receipt`,
      },
    ];
    const pending = deferredResponse();
    enqueue(
jsonResponse(history),
jsonResponse(runBody("completed")),
pending.promise,
jsonResponse(other),
)
    render(<App legacyConsole />);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: /Open run_0000/ }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    await user.click(within(historical).getByRole("button", { name: "Download fixture_receipt" }));
    await user.click(within(fixture).getByRole("button", { name: `Open ${runB}` }));
    expect(await within(historical).findByText(runB)).toBeInTheDocument();
    await act(async () => {
      pending.resolve(artifactResponse("LATE FROM THE FIRST RUN"));
    });
    expect(spies.createObjectURL).not.toHaveBeenCalled();
    expect(spies.click).not.toHaveBeenCalled();
    expect(historical).not.toHaveTextContent("LATE FROM THE FIRST RUN");
  });

  it("drops the previous historical artifact when the next view fails", async () => {
    const user = userEvent.setup();
    const completed = runBody("completed");
    const receipt = completed.run?.artifacts[0];
    if (!completed.run || !receipt) {
      throw new Error("fixture run is missing");
    }
    completed.run.artifacts = [
      receipt,
      {
        ...receipt,
        artifact_id: "artifact_run_00000000000000000000000000000001_1_fixture_summary",
        kind: "fixture_summary",
        media_type: "text/markdown",
      },
    ];
    enqueue(
jsonResponse(historyBody("2026-10-08T12:00:02+00:00")),
jsonResponse(completed),
artifactResponse("HISTORICAL RECEIPT"),
rejectedArtifact(),
)
    render(<App legacyConsole />);
    const fixture = screen.getByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: /Open run_/ }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    await user.click(within(historical).getByRole("button", { name: "View fixture_receipt" }));
    expect(await within(historical).findByRole("region", { name: "Historical artifact" })).toHaveTextContent(
      "HISTORICAL RECEIPT",
    );
    await user.click(within(historical).getByRole("button", { name: "View fixture_summary" }));
    expect(await within(historical).findByRole("alert")).toHaveTextContent(
      "The operational artifact could not be read.",
    );
    expect(within(historical).queryByRole("region", { name: "Historical artifact" })).not.toBeInTheDocument();
    expect(historical).not.toHaveTextContent("HISTORICAL RECEIPT");
  });

  it("keeps active retry pointed at the active run while a historical artifact is open", async () => {
    const user = userEvent.setup();
    downloadSpies();
    const runA = "run_00000000000000000000000000000001";
    const runB = "run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const history = historyBody("2026-10-08T12:00:02+00:00");
    history.runs[0] = {
      ...history.runs[0],
      status: "failed",
      text: "The fixture run failed. No scientific action was taken.",
    };
    history.runs.push({
      ...history.runs[0],
      run_id: runB,
      request_id: "request_b",
      approval_id: "approval_b",
      revision: 9,
      status: "completed",
      updated_at: "2026-10-08T12:00:03+00:00",
    });
    const foreign = runBody("completed");
    if (!foreign.run) {
      throw new Error("fixture run is missing");
    }
    foreign.run.run_id = runB;
    foreign.run.request_id = "request_b";
    foreign.run.request_revision = 9;
    foreign.run.artifacts = [
      {
        ...foreign.run.artifacts[0],
        artifact_id: `artifact_${runB}_1_fixture_receipt`,
      },
    ];
    enqueue(
jsonResponse(draftBody),
jsonResponse(approvalBody()),
jsonResponse(demonstrationBody()),
jsonResponse(runBody("failed")),
jsonResponse(history),
jsonResponse(foreign),
artifactResponse("FOREIGN RECEIPT"),
jsonResponse(runBody("queued", 2)),
)
    render(<App legacyConsole />);
    await demonstrate(user);
    await user.click(screen.getByRole("button", { name: "Create fixture run" }));
    const fixture = await screen.findByRole("region", { name: "Fixture run" });
    await user.click(within(fixture).getByRole("button", { name: "Refresh run history" }));
    await user.click(await within(fixture).findByRole("button", { name: `Open ${runB}` }));
    const historical = await within(fixture).findByRole("region", { name: "Historical run" });
    await user.click(within(historical).getByRole("button", { name: "Download fixture_receipt" }));
    const callsBeforeRetry = vi.mocked(fetch).mock.calls.length;
    await user.click(within(fixture).getByRole("button", { name: "Retry fixture run" }));
    const retryCall = vi.mocked(fetch).mock.calls[callsBeforeRetry];
    expect(String(retryCall?.[0])).toContain(`/api/approved-runs/${runA}/retry`);
    expect(String(retryCall?.[0])).not.toContain(runB);
    expect(within(fixture).getByRole("button", { name: "Create fixture run" })).toBeDisabled();
    expect(within(historical).queryByRole("button", { name: "Retry fixture run" })).not.toBeInTheDocument();
  });
});
