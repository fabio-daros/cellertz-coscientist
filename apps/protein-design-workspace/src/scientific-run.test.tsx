import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ScientificAuthorizationPanel } from "./ScientificAuthorizationPanel";
import { ScientificRunPanel } from "./ScientificRunPanel";
import { prototypeWorkspace } from "./fixture";
import type { ApprovedRunReply } from "./approved-run";
import type { DesignDraftReply } from "./contracts";
import type { GateReply } from "./approval";
import type { ReadinessReply } from "./scientific-runner-readiness";
import { AUTHORIZED_TEXT, CONSUMED_TEXT, type AuthorizationRecord } from "./scientific-authorization";
import {
  ARTIFACT_TEXT,
  COMPLETED_TEXT,
  EXTERNAL_FAILED_TEXT,
  FAILED_TEXT,
  KEY_TEXT,
  QUEUED_TEXT,
  RETRY_QUEUED_TEXT,
  RETRY_TEXT,
  RUNNING_TEXT,
  WORKFLOW_STAGE_IDS,
  WORKFLOW_SUMMARY,
  WORKFLOW_VERSIONS,
  acceptWorkflowText,
  bindingDigest,
  canonicalJson,
  digestHex,
  parseWorkflowTrace,
  readWorkflowTrace,
  workflowObservedAt,
  readScientificRun,
  selectScientificRun,
  type ScientificRun,
} from "./scientific-run";

const STAMP = "2026-10-12T16:00:00+00:00";
const LATER = "2026-10-12T16:00:01+00:00";
const RUN_ID = `run_${"ab".repeat(16)}`;
const OTHER_RUN = `run_${"cd".repeat(16)}`;
const AUTHORIZATION_ID = `authorization_${"ab".repeat(16)}`;

function draftReply(revision = 4): DesignDraftReply {
  return {
    status: "request_complete",
    text: "The request is structurally complete. It is not authorized and not executable.",
    session_id: prototypeWorkspace.session_id,
    execution_authorized: false,
    executable: false,
    changed_fields: ["target_id"],
    questions: [],
    issues: [],
    draft: {
      request_id: "request_1",
      session_id: prototypeWorkspace.session_id,
      revision,
      completeness: "complete",
      execution_authorized: false,
      executable: false,
      fields: [
        { name: "target_id", value: "PDL1", provenance: "user" },
        { name: "generator", value: "fixture", provenance: "user" },
      ],
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
}

function decisionReply(): GateReply {
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
      revision_digest: "ab".repeat(32),
      decision: "approved",
      reviewer_id: "operator_declared",
      reason: "Fixture demonstration only.",
      recorded_at: STAMP,
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

function executionReply(): GateReply {
  return {
    ...decisionReply(),
    status: "demonstration_completed",
    text: "Demonstration dispatch. Not a scientific run.",
    demonstration: {
      dispatch_id: "dispatch_1",
      approval_id: "approval_1",
      idempotency_key: "dispatch-approval_1",
      execution_profile: "phase6d_fixture_only",
      authorization_status: "authorized",
      preflight_status: "preflight_ready",
      dispatch_status: "completed",
      request_digest: "ab".repeat(32),
      text: "Demonstration dispatch. Not a scientific run.",
      candidates: [],
      scientific_evidence: [],
      scientific_memory_written: false,
      scientific_execution: false,
    },
  };
}

function fixtureRun(): ApprovedRunReply {
  return {
    status: "run_completed",
    text: "Fixture run completed. Not scientific validation.",
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
      run_id: "run_1",
      session_id: prototypeWorkspace.session_id,
      approval_id: "approval_1",
      request_id: "request_1",
      request_revision: 4,
      request_digest: "ab".repeat(32),
      execution_profile: "phase6d_fixture_only",
      status: "completed",
      attempt: 1,
      created_at: STAMP,
      updated_at: STAMP,
      events: [
        {
          sequence: 1,
          status: "completed",
          recorded_at: STAMP,
          attempt: 1,
          text: "Fixture run completed.",
          origin: "server",
        },
      ],
      artifacts: [],
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
        observed_costs_known: false,
        authorizes_execution: false,
      },
      fixture: true,
      scientific_validation: false,
      scientific_execution: false,
      label: "fixture-only run",
      text: "Fixture run completed. Not scientific validation.",
    },
  };
}

function readiness(): ReadinessReply {
  return {
    status: "ready",
    text: "Operational readiness. This is not scientific validation.",
    session_id: prototypeWorkspace.session_id,
    profile_id: "pdl1-bindcraft-historical-v1",
    tool_name: "bindcraft",
    tool_version: "recorded-tool-1",
    runtime_name: "recorded-runtime",
    runtime_version: "recorded-runtime-1",
    image_digest: `sha256:${"ab".repeat(32)}`,
    target_scope: "PDL1",
    manifest_digest: "cd".repeat(32),
    observed_at: STAMP,
    checks: [],
    execution_authorized: false,
    scientific_execution: false,
    scientific_validation: false,
  };
}

function authorization(executionAuthorized = true): AuthorizationRecord {
  return {
    authorization_id: AUTHORIZATION_ID,
    session_id: prototypeWorkspace.session_id,
    request_id: "request_1",
    request_revision: 4,
    approval_id: "approval_1",
    request_digest: "ab".repeat(32),
    readiness_observation_id: `observation_${"cd".repeat(32)}`,
    readiness_observed_at: STAMP,
    profile_id: "pdl1-bindcraft-historical-v1",
    tool_name: "bindcraft",
    tool_version: "recorded-tool-1",
    runtime_name: "recorded-runtime",
    runtime_version: "recorded-runtime-1",
    image_digest: `sha256:${"ab".repeat(32)}`,
    manifest_digest: "cd".repeat(32),
    target_scope: "PDL1",
    budget: {
      action_on_limit: prototypeWorkspace.budget.action_on_limit,
      authorizes_execution: false,
      currency: prototypeWorkspace.budget.currency,
      human_override_required: prototypeWorkspace.budget.human_override_required,
      model_cost_ceiling: prototypeWorkspace.budget.model_cost_ceiling,
      scientific_tool_cost_ceiling: prototypeWorkspace.budget.scientific_tool_cost_ceiling,
      token_ceiling: prototypeWorkspace.budget.token_ceiling,
      warning_threshold: prototypeWorkspace.budget.warning_threshold,
    },
    status: "authorized",
    created_at: STAMP,
    updated_at: STAMP,
    events: [
      {
        sequence: 1,
        status: "authorized",
        recorded_at: STAMP,
        text: AUTHORIZED_TEXT,
        origin: "server",
      },
    ],
    execution_authorized: executionAuthorized,
    scientific_execution: false,
    scientific_validation: false,
    scientific_candidate_generated: false,
    single_use: "This authorization may be consumed once by a future scientific executor.",
    text: executionAuthorized
      ? AUTHORIZED_TEXT
      : "This authorization has been consumed. See the scientific run record for execution status.",
    validation_text: "This is not scientific validation.",
  };
}

function legalEvents(
  status: ScientificRun["status"],
  attempt: number,
  text: string,
  updatedAt: string,
): ScientificRun["events"] {
  const queued = {
    attempt: 1,
    origin: "server" as const,
    recorded_at: STAMP,
    sequence: 1,
    status: "queued" as const,
    text: QUEUED_TEXT,
  };
  if (status === "queued" && attempt === 1) {
    return [queued];
  }
  const running = {
    attempt: 1,
    origin: "server" as const,
    recorded_at: LATER,
    sequence: 2,
    status: "running" as const,
    text: RUNNING_TEXT,
  };
  if (status === "running") {
    return [queued, { ...running, recorded_at: updatedAt }];
  }
  const terminal = {
    attempt,
    origin: "server" as const,
    recorded_at: updatedAt,
    sequence: status === "queued" ? 4 : 3,
    status,
    text,
  };
  if (status === "queued") {
    return [
      queued,
      running,
      { attempt: 1, origin: "server", recorded_at: LATER, sequence: 3, status: "failed", text: FAILED_TEXT },
      terminal,
    ];
  }
  return [queued, running, terminal];
}

function scientificRun(
  status: ScientificRun["status"] = "queued",
  extra: Partial<ScientificRun> = {},
): ScientificRun {
  const attempt = extra.attempt ?? 1;
  const runId = extra.run_id ?? RUN_ID;
  const updatedAt = extra.updated_at ?? (status === "queued" && attempt === 1 ? STAMP : LATER);
  const text =
    extra.text ??
    (status === "completed"
      ? COMPLETED_TEXT
      : status === "running"
        ? RUNNING_TEXT
        : status === "failed"
          ? FAILED_TEXT
          : attempt === 1
            ? QUEUED_TEXT
            : "Scientific retry queued.");
  return {
    accepted: false,
    approval_id: "approval_1",
    artifacts: status === "completed" ? [executionArtifact(runId, attempt)] : [],
    attempt,
    authorization_id: AUTHORIZATION_ID,
    budget: authorization().budget,
    created_at: STAMP,
    events: legalEvents(status, attempt, text, updatedAt),
    image_digest: `sha256:${"ab".repeat(32)}`,
    manifest_digest: "cd".repeat(32),
    profile_id: "pdl1-bindcraft-historical-v1",
    ranked: false,
    readiness_observation_id: `observation_${"cd".repeat(32)}`,
    readiness_observed_at: STAMP,
    request_digest: "ab".repeat(32),
    request_id: "request_1",
    request_revision: 4,
    run_id: runId,
    runtime_name: "recorded-runtime",
    runtime_version: "recorded-runtime-1",
    scientific_candidate_generated: false,
    scientific_execution: status === "completed",
    scientific_validation: false,
    session_id: prototypeWorkspace.session_id,
    status,
    target_scope: "PDL1",
    text,
    tool_name: "bindcraft",
    tool_version: "recorded-tool-1",
    updated_at: updatedAt,
    ...extra,
  };
}

function executionArtifact(runId: string, attempt: number) {
  return {
    artifact_id: `artifact_${runId}_${attempt}_execution_record`,
    attempt,
    kind: "execution_record" as const,
    media_type: "application/json" as const,
    run_id: runId,
    session_id: prototypeWorkspace.session_id,
    sha256: "ab".repeat(32),
    size: 32,
  };
}

function reply(run: ScientificRun | null, status = "run_queued") {
  return {
    accepted: false,
    ranked: false,
    run,
    scientific_candidate_generated: run?.scientific_candidate_generated ?? false,
    scientific_execution: run?.scientific_execution ?? false,
    scientific_validation: false,
    session_id: prototypeWorkspace.session_id,
    status,
    text: run?.text ?? "No scientific run matches this request.",
  };
}

function jsonResponse(body: unknown, status = 202): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function deferred() {
  let resolve: (value: Response) => void = () => {};
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function panel(
  record: AuthorizationRecord | null = authorization(),
  onConsumed?: (record: AuthorizationRecord) => void,
) {
  return (
    <div className="stage-main">
      <ScientificRunPanel
        authorization={record}
        draftReply={draftReply()}
        decisionReply={decisionReply()}
        executionReply={executionReply()}
        runReply={fixtureRun()}
        readiness={readiness()}
        budget={prototypeWorkspace.budget}
        onConsumed={onConsumed}
      />
    </div>
  );
}

function runList(runs: ScientificRun[]): Response {
  return jsonResponse(
    {
      accepted: false,
      ranked: false,
      runs,
      scientific_candidate_generated: false,
      scientific_execution: false,
      scientific_validation: false,
      session_id: prototypeWorkspace.session_id,
      status: "runs_observed",
      text: "Scientific runs. Not scientific validation.",
    },
    200,
  );
}

function emptyList(): Response {
  return runList([]);
}

async function settled() {
  await waitFor(() => expect(screen.getByRole("button", { name: "Scientific run history" })).toBeEnabled());
}

function authorizationEnvelope(record: AuthorizationRecord, status = "authorization_created") {
  return {
    authorization: record,
    execution_authorized: record.execution_authorized,
    scientific_candidate_generated: false,
    scientific_execution: false,
    scientific_validation: false,
    session_id: record.session_id,
    status,
    text: record.text,
  };
}

function BoundPanels() {
  const [record, setRecord] = useState<AuthorizationRecord | null>(null);
  const [consumed, setConsumed] = useState<AuthorizationRecord | null>(null);
  return (
    <div className="stage-main">
      <ScientificAuthorizationPanel
        draftReply={draftReply()}
        decisionReply={decisionReply()}
        executionReply={executionReply()}
        runReply={fixtureRun()}
        readiness={readiness()}
        budget={prototypeWorkspace.budget}
        onCurrent={setRecord}
        consumed={consumed}
      />
      <ScientificRunPanel
        authorization={record}
        draftReply={draftReply()}
        decisionReply={decisionReply()}
        executionReply={executionReply()}
        runReply={fixtureRun()}
        readiness={readiness()}
        budget={prototypeWorkspace.budget}
        onConsumed={setConsumed}
      />
    </div>
  );
}

describe("scientific run panel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(emptyList()));
  });

  it("starts only the current authorized binding and sends identifiers only", async () => {
    const user = userEvent.setup();
    const { rerender } = render(panel(authorization(false)));
    expect(screen.queryByRole("button", { name: "Start scientific run" })).not.toBeInTheDocument();
    rerender(panel({ ...authorization(), request_revision: 9 }));
    expect(screen.queryByRole("button", { name: "Start scientific run" })).not.toBeInTheDocument();
    rerender(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(scientificRun())));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    const posted = vi.mocked(fetch).mock.calls.find((call) => call[1]?.method === "POST");
    expect(posted?.[0]).toBe("/api/scientific-runs");
    expect(posted?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(posted?.[1]?.body))).toEqual({
      authorization_id: AUTHORIZATION_ID,
      idempotency_key: `scientific-run-${AUTHORIZATION_ID}`,
    });
    expect(screen.queryByRole("button", { name: "Start scientific run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Promote|Validate|Second Brain/ })).not.toBeInTheDocument();
  });

  it("shares one lock and ignores stale, cross-run, and cross-revision polls", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const user = userEvent.setup();
    render(panel());
    await settled();
    const pending = deferred();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    await user.click(screen.getByRole("button", { name: "Scientific run history" }));
    expect(vi.mocked(fetch).mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.filter((call) => String(call[0]).endsWith("/retry"))).toHaveLength(0);
    pending.resolve(jsonResponse(reply(scientificRun())));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    const running = scientificRun("running", { updated_at: LATER });
    const other = scientificRun("failed", { run_id: OTHER_RUN, updated_at: LATER });
    const revised = scientificRun("completed", { request_revision: 5, updated_at: LATER });
    const stale = scientificRun("queued");
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(other, "run_observed"), 200))
      .mockResolvedValueOnce(jsonResponse(reply(running, "run_observed"), 200))
      .mockResolvedValueOnce(jsonResponse(reply(revised, "run_observed"), 200))
      .mockResolvedValueOnce(jsonResponse(reply(stale, "run_observed"), 200));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText(QUEUED_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(FAILED_TEXT)).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(COMPLETED_TEXT)).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(`/api/scientific-runs/${RUN_ID}`, { method: "GET" });
    vi.useRealTimers();
  });

  it("retries only a failed run and keeps history from replacing the active run", async () => {
    const user = userEvent.setup();
    render(panel());
    await settled();
    const failed = scientificRun("failed", { updated_at: LATER });
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(scientificRun())));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry failed scientific run" })).not.toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(failed, "run_observed"), 200));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });
    expect(await screen.findByText(FAILED_TEXT)).toBeInTheDocument();
    const retried = scientificRun("queued", {
      attempt: 2,
      text: "Scientific retry queued.",
      updated_at: "2026-10-12T16:00:02+00:00",
    });
    const historical = scientificRun("completed", {
      run_id: OTHER_RUN,
      authorization_id: `authorization_${"ef".repeat(16)}`,
      updated_at: LATER,
    });
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(retried)));
    await user.click(screen.getByRole("button", { name: "Retry failed scientific run" }));
    expect(await screen.findByText("Scientific retry queued.")).toBeInTheDocument();
    const retryCall = vi.mocked(fetch).mock.calls.at(-1);
    expect(retryCall?.[0]).toBe(`/api/scientific-runs/${RUN_ID}/retry`);
    expect(JSON.parse(String(retryCall?.[1]?.body))).toEqual({
      idempotency_key: `scientific-retry-${RUN_ID}-1`,
    });
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          accepted: false,
          ranked: false,
          runs: [retried, historical],
          scientific_candidate_generated: false,
          scientific_execution: false,
          scientific_validation: false,
          session_id: prototypeWorkspace.session_id,
          status: "runs_observed",
          text: "Scientific runs. Not scientific validation.",
        },
        200,
      ),
    );
    await user.click(screen.getByRole("button", { name: "Scientific run history" }));
    await user.click(await screen.findByRole("button", { name: OTHER_RUN }));
    expect(screen.getByRole("region", { name: "Historical scientific run" })).toHaveTextContent(COMPLETED_TEXT);
    expect(screen.getByRole("region", { name: "Scientific run" })).toHaveTextContent("Scientific retry queued.");
    expect(screen.queryByRole("button", { name: /Promote|Validate|Second Brain/ })).not.toBeInTheDocument();
  });

  it("adopts an idempotent replay and a restored run for the same binding", async () => {
    const user = userEvent.setup();
    const observed = scientificRun("running");
    const { rerender } = render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(observed, "run_observed"), 200));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(RUNNING_TEXT)).toBeInTheDocument();
    const restored = scientificRun("failed");
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          accepted: false,
          ranked: false,
          runs: [restored],
          scientific_candidate_generated: false,
          scientific_execution: false,
          scientific_validation: false,
          session_id: prototypeWorkspace.session_id,
          status: "runs_observed",
          text: "Scientific runs. Not scientific validation.",
        },
        200,
      ),
    );
    rerender(panel(null));
    rerender(panel());
    expect(await screen.findByText(FAILED_TEXT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry failed scientific run" })).toBeEnabled();
  });

  it("shows a completed run that generated a candidate", async () => {
    const user = userEvent.setup();
    const completed = scientificRun("completed", {
      scientific_candidate_generated: true,
      artifacts: [
        executionArtifact(RUN_ID, 1),
        {
          artifact_id: `artifact_${RUN_ID}_1_candidate_record`,
          attempt: 1,
          kind: "candidate_record",
          media_type: "application/json",
          run_id: RUN_ID,
          session_id: prototypeWorkspace.session_id,
          sha256: "cd".repeat(32),
          size: 40,
        },
      ],
    });
    render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(completed, "run_observed"), 200));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText("candidate_record")).toBeInTheDocument();
    expect(screen.getByText("false")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry failed scientific run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Promote|Validate|Second Brain/ })).not.toBeInTheDocument();
  });

  it("drops a reply from the previous authorization after the binding changes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(panel());
    await settled();
    const pending = deferred();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    rerender(panel({ ...authorization(), authorization_id: `authorization_${"ef".repeat(16)}` }));
    rerender(panel());
    pending.resolve(jsonResponse(reply(scientificRun())));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
  });

  it("drops a pending run when any budget field changes", async () => {
    const user = userEvent.setup();
    const current = authorization().budget;
    const patches: Record<string, unknown>[] = [
      { action_on_limit: current.action_on_limit === "stop" ? "warn" : "stop" },
      { authorizes_execution: true },
      { currency: current.currency === null ? "USD" : null },
      { human_override_required: !current.human_override_required },
      { model_cost_ceiling: current.model_cost_ceiling === null ? 1 : null },
      { scientific_tool_cost_ceiling: current.scientific_tool_cost_ceiling === null ? 1 : null },
      { token_ceiling: current.token_ceiling === null ? 1 : null },
      { warning_threshold: current.warning_threshold === null ? 0.5 : null },
    ];
    for (const patch of patches) {
      const { rerender, unmount } = render(panel());
      await settled();
      const pending = deferred();
      vi.mocked(fetch).mockReturnValueOnce(pending.promise);
      await user.click(screen.getByRole("button", { name: "Start scientific run" }));
      rerender(
        panel({
          ...authorization(),
          budget: { ...current, ...patch } as AuthorizationRecord["budget"],
        }),
      );
      pending.resolve(jsonResponse(reply(scientificRun())));
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("drops a pending run when any other binding field changes", async () => {
    const user = userEvent.setup();
    const current = authorization();
    const patches: Array<Partial<Record<keyof AuthorizationRecord, unknown>>> = [
      { session_id: "session_other" },
      { request_id: "request_other" },
      { request_revision: 9 },
      { approval_id: "approval_other" },
      { request_digest: "12".repeat(32) },
      { readiness_observation_id: `observation_${"12".repeat(32)}` },
      { readiness_observed_at: "2026-10-12T18:00:00+00:00" },
      { profile_id: "other-profile" },
      { tool_name: "other-tool" },
      { tool_version: "other-tool-1" },
      { runtime_name: "other-runtime" },
      { runtime_version: "other-runtime-1" },
      { image_digest: `sha256:${"12".repeat(32)}` },
      { manifest_digest: "12".repeat(32) },
      { target_scope: "CD22" },
    ];
    for (const patch of patches) {
      const { rerender, unmount } = render(panel());
      await settled();
      const pending = deferred();
      vi.mocked(fetch).mockReturnValueOnce(pending.promise);
      await user.click(screen.getByRole("button", { name: "Start scientific run" }));
      rerender(panel({ ...current, ...patch } as AuthorizationRecord));
      pending.resolve(jsonResponse(reply(scientificRun())));
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("rejects a scientific result that breaks the public contract", async () => {
    const user = userEvent.setup();
    render(panel());
    await settled();
    const legal = scientificRun();
    const fractional = scientificRun();
    fractional.attempt = 1.5;
    const skipped = scientificRun("completed", {
      events: [
        {
          attempt: 1,
          origin: "server",
          recorded_at: STAMP,
          sequence: 1,
          status: "queued",
          text: QUEUED_TEXT,
        },
        {
          attempt: 1,
          origin: "server",
          recorded_at: LATER,
          sequence: 2,
          status: "completed",
          text: COMPLETED_TEXT,
        },
      ],
    });
    const mismatched = scientificRun("completed", {
      text: QUEUED_TEXT,
      events: legalEvents("completed", 1, QUEUED_TEXT, LATER),
    });
    const inconsistent = scientificRun("completed", {
      artifacts: [
        {
          ...executionArtifact(RUN_ID, 1),
          artifact_id: `artifact_${RUN_ID}_1_candidate_record`,
        },
      ],
    });
    const lateCreated = scientificRun();
    lateCreated.created_at = LATER;
    const lateUpdated = scientificRun("running");
    lateUpdated.updated_at = "2026-10-12T16:00:02+00:00";
    const badSession = scientificRun();
    badSession.session_id = "bad session";
    const slashed = scientificRun();
    slashed.budget = { ...slashed.budget, currency: "USD\\" };
    const ticked = scientificRun();
    ticked.budget = { ...ticked.budget, currency: "USD`" };
    const controlled = scientificRun();
    controlled.budget = { ...controlled.budget, currency: "USD\u0000" };
    const booleanAttempt = scientificRun();
    booleanAttempt.attempt = true as unknown as number;
    const flagged = scientificRun("completed");
    flagged.scientific_candidate_generated = "true" as unknown as boolean;
    const tokenCurrency = scientificRun();
    tokenCurrency.budget = { ...tokenCurrency.budget, currency: "token=secret" };
    const pathCurrency = scientificRun();
    pathCurrency.budget = { ...pathCurrency.budget, currency: "/tmp/hidden" };
    const reversed = scientificRun("running");
    reversed.events = [
      reversed.events[0],
      { ...reversed.events[1], recorded_at: "2026-10-12T15:59:59+00:00" },
    ];
    reversed.updated_at = "2026-10-12T15:59:59+00:00";
    const bodies = [
      reply(legal),
      { ...reply(legal), status: "exploded" },
      { ...reply(legal), session_id: "bad session" },
      reply(fractional),
      reply(skipped, "run_observed"),
      reply(mismatched, "run_observed"),
      reply(inconsistent, "run_observed"),
      reply(lateCreated),
      reply(lateUpdated, "run_observed"),
      reply(badSession),
      reply(slashed),
      reply(ticked),
      reply(controlled),
      reply(booleanAttempt),
      reply(flagged, "run_observed"),
      reply(tokenCurrency),
      reply(pathCurrency),
      reply(reversed, "run_observed"),
    ];
    for (const body of bodies) {
      const http = body === bodies[0] || body.status === "run_observed" ? 200 : 202;
      vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(body, http));
      await user.click(screen.getByRole("button", { name: "Start scientific run" }));
      expect(
        await screen.findByText("The scientific run could not be recorded. No scientific action was taken."),
      ).toBeInTheDocument();
      expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
      expect(screen.queryByText(COMPLETED_TEXT)).not.toBeInTheDocument();
    }
  });

  it("adopts a replay and marks the authorization consumed", async () => {
    const user = userEvent.setup();
    render(<BoundPanels />);
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(authorizationEnvelope(authorization()), 201));
    vi.mocked(fetch).mockResolvedValueOnce(emptyList());
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("button", { name: "Revoke scientific authorization" })).toBeInTheDocument();
    expect(screen.getByText("No scientific execution has started.")).toBeInTheDocument();
    const observed = scientificRun("running");
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(observed, "run_observed"), 200));
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
    );
    await user.click(await screen.findByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(await screen.findByText(CONSUMED_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke scientific authorization" })).not.toBeInTheDocument();
    expect(screen.queryByText("No scientific execution has started.")).not.toBeInTheDocument();
    expect(screen.queryByText("This permission is single-use. No scientific execution has started.")).not.toBeInTheDocument();
  });

  it("rejects an unknown status even when HTTP is 409", async () => {
    const user = userEvent.setup();
    render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ ...reply(null), status: "exploded", text: "synthetic boom" }, 409),
    );
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(
      await screen.findByText("The scientific run could not be recorded. No scientific action was taken."),
    ).toBeInTheDocument();
    expect(screen.queryByText("synthetic boom")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start scientific run" })).toBeInTheDocument();
  });

  it("accepts an idempotency conflict without adopting it as the active run", async () => {
    const user = userEvent.setup();
    render(panel());
    await settled();
    const queued = scientificRun();
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ ...reply(queued, "idempotency_conflict"), text: KEY_TEXT }, 409),
    );
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(KEY_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start scientific run" })).toBeInTheDocument();
  });

  it("keeps a retry refusal on the failed run without queueing another attempt", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const user = userEvent.setup();
    render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(scientificRun())));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    const failed = scientificRun("failed");
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(failed, "run_observed"), 200));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(await screen.findByText(FAILED_TEXT)).toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ ...reply(failed, "retry_not_allowed"), text: RETRY_TEXT }, 409),
    );
    await user.click(screen.getByRole("button", { name: "Retry failed scientific run" }));
    expect(await screen.findByText(RETRY_TEXT)).toBeInTheDocument();
    expect(screen.getByText(FAILED_TEXT)).toBeInTheDocument();
    expect(screen.queryByText("Scientific retry queued.")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("rejects a shifted HTTP status, text, flag, run, or internal status", async () => {
    const user = userEvent.setup();
    render(panel());
    await settled();
    const queued = scientificRun();
    const legal = reply(queued);
    const shifts = [
      jsonResponse(legal, 200),
      jsonResponse({ ...legal, text: "Scientific run started." }),
      jsonResponse({ ...legal, scientific_validation: true }),
      jsonResponse({ ...legal, run: null }),
      jsonResponse(reply(scientificRun("running"), "run_queued")),
    ];
    for (const response of shifts) {
      vi.mocked(fetch).mockResolvedValueOnce(response);
      await user.click(screen.getByRole("button", { name: "Start scientific run" }));
      expect(
        await screen.findByText("The scientific run could not be recorded. No scientific action was taken."),
      ).toBeInTheDocument();
      expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
      expect(screen.queryByText(RUNNING_TEXT)).not.toBeInTheDocument();
    }
  });

  it("applies a consumed authorization that arrives after a run poll", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const user = userEvent.setup();
    render(<BoundPanels />);
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(authorizationEnvelope(authorization()), 201));
    vi.mocked(fetch).mockResolvedValueOnce(emptyList());
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("button", { name: "Revoke scientific authorization" })).toBeInTheDocument();
    const pending = deferred();
    const running = scientificRun("running");
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(scientificRun())))
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(jsonResponse(reply(running, "run_observed"), 200));
    await user.click(await screen.findByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(await screen.findByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke scientific authorization" })).toBeInTheDocument();
    await act(async () => {
      pending.resolve(
        jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
      );
    });
    expect(await screen.findByText(CONSUMED_TEXT)).toBeInTheDocument();
    expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke scientific authorization" })).not.toBeInTheDocument();
    expect(screen.queryByText("No scientific execution has started.")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("retries authorization sync after a failed read", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const user = userEvent.setup();
    render(<BoundPanels />);
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(authorizationEnvelope(authorization()), 201));
    vi.mocked(fetch).mockResolvedValueOnce(emptyList());
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("button", { name: "Revoke scientific authorization" })).toBeInTheDocument();
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(scientificRun())))
      .mockRejectedValueOnce(new Error("authorization down"));
    await user.click(await screen.findByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke scientific authorization" })).toBeInTheDocument();
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(scientificRun("running"), "run_observed"), 200))
      .mockResolvedValueOnce(
        jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
      );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(await screen.findByText(CONSUMED_TEXT)).toBeInTheDocument();
    expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke scientific authorization" })).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("drops a consumed authorization for a binding that changed while it was loading", async () => {
    const seen = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(panel(authorization(), seen));
    await settled();
    const pending = deferred();
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(scientificRun())))
      .mockReturnValueOnce(pending.promise);
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    rerender(panel({ ...authorization(), request_id: "request_other" }, seen));
    await act(async () => {
      pending.resolve(
        jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
      );
    });
    expect(seen).not.toHaveBeenCalled();
    expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
  });

  it("syncs consumption after replay and after restoring a run on remount", async () => {
    const seen = vi.fn();
    const user = userEvent.setup();
    const observed = scientificRun("running");
    const { unmount } = render(panel(authorization(), seen));
    await settled();
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(observed, "run_observed"), 200))
      .mockResolvedValueOnce(
        jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
      );
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(RUNNING_TEXT)).toBeInTheDocument();
    await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    unmount();
    seen.mockClear();
    vi.mocked(fetch)
      .mockResolvedValueOnce(runList([observed]))
      .mockResolvedValueOnce(
        jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
      );
    render(panel(authorization(), seen));
    expect(await screen.findByText(RUNNING_TEXT)).toBeInTheDocument();
    await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    expect(seen.mock.calls[0][0].execution_authorized).toBe(false);
  });

  it("keeps a newer run and an open historical inspection while polling and syncing", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const seen = vi.fn();
    const user = userEvent.setup();
    render(panel(authorization(), seen));
    await settled();
    const pending = deferred();
    const running = scientificRun("running");
    const historical = scientificRun("completed", { run_id: OTHER_RUN, updated_at: LATER });
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(reply(scientificRun())))
      .mockReturnValueOnce(pending.promise);
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(running, "run_observed"), 200));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(await screen.findByText(RUNNING_TEXT)).toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(runList([running, historical]));
    await user.click(screen.getByRole("button", { name: "Scientific run history" }));
    await user.click(await screen.findByRole("button", { name: OTHER_RUN }));
    expect(screen.getByRole("region", { name: "Historical scientific run" })).toHaveTextContent(COMPLETED_TEXT);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(scientificRun(), "run_observed"), 200));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(QUEUED_TEXT)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Historical scientific run" })).toHaveTextContent(COMPLETED_TEXT);
    expect(seen).not.toHaveBeenCalled();
    await act(async () => {
      pending.resolve(
        jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200),
      );
    });
    await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    expect(screen.getByText(RUNNING_TEXT)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Historical scientific run" })).toHaveTextContent(COMPLETED_TEXT);
    vi.useRealTimers();
  });

  it("keeps the scientific run section inside a 390px stage", () => {
    render(<div style={{ width: "390px", maxWidth: "390px", overflowX: "hidden" }}>{panel()}</div>);
    const section = screen.getByRole("region", { name: "Scientific run" });
    expect(section).toHaveClass("scientific-run");
    expect(section.scrollWidth).toBeLessThanOrEqual(390);
  });
});

function withTrace(run: ScientificRun): ScientificRun {
  return {
    ...run,
    artifacts: [
      ...run.artifacts,
      {
        artifact_id: `artifact_${run.run_id}_${run.attempt}_workflow_trace`,
        attempt: run.attempt,
        kind: "workflow_trace",
        media_type: "application/json",
        run_id: run.run_id,
        session_id: run.session_id,
        sha256: "ef".repeat(32),
        size: 128,
      },
    ],
  };
}

async function workflowBody(run: ScientificRun) {
  const binding = await bindingDigest(run);
  let upstream = binding;
  const stages = [];
  for (let index = 0; index < WORKFLOW_STAGE_IDS.length; index += 1) {
    const stageId = WORKFLOW_STAGE_IDS[index];
    const version = WORKFLOW_VERSIONS[stageId];
    const input = {
      attempt: run.attempt,
      binding_hash: binding,
      run_id: run.run_id,
      sequence: index + 1,
      stage_id: stageId,
      upstream_digest: upstream,
    };
    const output = {
      attempt: run.attempt,
      implementation_id: version,
      implementation_version: version,
      input_digest: await digestHex(input),
      role: stageId,
      run_id: run.run_id,
      sequence: index + 1,
      stage_id: stageId,
      status: "completed",
      summary: WORKFLOW_SUMMARY,
      upstream_digest: upstream,
    };
    const outputDigest = await digestHex(output);
    const observedAt = workflowObservedAt(run);
    if (observedAt === null) {
      throw new Error("workflow observation unavailable");
    }
    stages.push({ ...output, observed_at: observedAt, output_digest: outputDigest });
    upstream = outputDigest;
  }
  return {
    accepted: false as const,
    attempt: run.attempt,
    authorization_id: run.authorization_id,
    binding_hash: binding,
    profile_id: run.profile_id,
    ranked: false as const,
    run_id: run.run_id,
    schema: "cellertz.governed_agent_workflow.v1" as const,
    scientific_candidate_generated: false as const,
    scientific_validation: false as const,
    session_id: run.session_id,
    stages,
    target_scope: "PDL1" as const,
    workflow_profile: "pdl1-governed-agent-workflow" as const,
    workflow_version: "v1" as const,
  };
}

function artifactResponse(body: unknown): Response {
  const text = typeof body === "string" ? body : canonicalJson(body);
  const size = new TextEncoder().encode(text).byteLength;
  return new Response(text, {
    status: 200,
    headers: {
      "cache-control": "private, no-store",
      "content-disposition": 'attachment; filename="scientific-agent-workflow.json"',
      "content-length": String(size),
      "content-type": "application/json",
      "x-content-type-options": "nosniff",
    },
  });
}

async function sealedTrace(base = withTrace(scientificRun("completed"))) {
  const trace = await workflowBody(base);
  const text = canonicalJson(trace);
  const sha256 = await digestHex(trace);
  const size = new TextEncoder().encode(text).byteLength;
  const run: ScientificRun = {
    ...base,
    artifacts: base.artifacts.map((item) =>
      item.kind === "workflow_trace" ? { ...item, sha256, size } : item,
    ),
  };
  return { run, trace, text };
}

const MARKER = "HOSTILE_MARKER";

function artifactHeaders(size: number, patch: Record<string, string | null> = {}): Record<string, string | null> {
  return {
    "cache-control": "private, no-store",
    "content-disposition": 'attachment; filename="scientific-agent-workflow.json"',
    "content-length": String(size),
    "content-type": "application/json",
    "x-content-type-options": "nosniff",
    ...patch,
  };
}

function streamedResponse(body: BodyInit | ReadableStream<Uint8Array> | null, headers: Record<string, string | null>): Response {
  const stream = body instanceof ReadableStream ? body : null;
  const response = new Response(stream ? null : body, { status: 200 });
  if (stream) {
    Object.defineProperty(response, "body", { configurable: true, value: stream });
  }
  const read = response.headers.get.bind(response.headers);
  vi.spyOn(response.headers, "get").mockImplementation((name: string) => {
    const key = name.toLowerCase();
    return Object.prototype.hasOwnProperty.call(headers, key) ? headers[key] : read(name);
  });
  return response;
}

function replaceTrace(run: ScientificRun, patch: Partial<ScientificRun["artifacts"][number]>): ScientificRun {
  return {
    ...run,
    artifacts: run.artifacts.map((item) => (item.kind === "workflow_trace" ? { ...item, ...patch } : item)),
  };
}

async function sha256Hex(bytes: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

const RECORD_TEXT = "The scientific run could not be recorded. No scientific action was taken.";

async function expectSafeRead(
  run: ScientificRun,
  artifactId: string,
  response: () => Response,
  panelText = ARTIFACT_TEXT,
) {
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockResolvedValueOnce(response());
  expect(await readWorkflowTrace(run, artifactId)).toBeNull();
  vi.mocked(fetch).mockReset();
  vi.mocked(fetch).mockResolvedValue(emptyList());
  const user = userEvent.setup();
  render(panel());
  await settled();
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(run, "run_observed"), 200));
  vi.mocked(fetch).mockResolvedValueOnce(response());
  await user.click(screen.getByRole("button", { name: "Start scientific run" }));
  expect(await screen.findByText(panelText)).toBeInTheDocument();
  expect(screen.queryByText(MARKER)).not.toBeInTheDocument();
  expect(screen.queryAllByText(WORKFLOW_SUMMARY)).toHaveLength(0);
}

describe("governed agent workflow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(emptyList()));
  });

  it("uses the canonical digest and rejects a tampered trace", async () => {
    expect(WORKFLOW_VERSIONS).toEqual({
      generation: "protein_generation_agent_v1",
      scoring: "protein_scoring_tool_v2_fixture_metrics_v1",
      ranking: "protein_ranking_agent_v1",
      critique: "protein_critic_agent_v1",
      prioritization: "protein_prioritization_agent_v1",
    });
    expect(await digestHex({ k: "v" })).toBe(
      "666c1aa02e8068c6d5cc1d3295009432c16790bec28ec8ce119d0d1a18d61319",
    );
    const run = withTrace(scientificRun("completed"));
    const trace = await workflowBody(run);
    expect(await parseWorkflowTrace(trace, run)).not.toBeNull();
    trace.stages[1].output_digest = "ab".repeat(32);
    expect(await parseWorkflowTrace(trace, run)).toBeNull();
    const flags = await workflowBody(run);
    (flags as { accepted: number | false }).accepted = 0;
    expect(await parseWorkflowTrace(flags, run)).toBeNull();
    const foreign = await workflowBody(run);
    foreign.run_id = OTHER_RUN;
    expect(await parseWorkflowTrace(foreign, run)).toBeNull();
    const laterRun = withTrace(scientificRun("completed", { updated_at: "2026-10-12T16:00:02+00:00" }));
    const bound = await workflowBody(laterRun);
    expect(bound.stages[0].observed_at).toBe(LATER);
    expect(await parseWorkflowTrace(bound, laterRun)).not.toBeNull();
    for (const stamp of [STAMP, laterRun.updated_at, "2026-10-12T16:00:01.500000+00:00"]) {
      const shifted = await workflowBody(laterRun);
      for (const stage of shifted.stages) {
        stage.observed_at = stamp;
      }
      expect(await parseWorkflowTrace(shifted, laterRun)).toBeNull();
    }
    const missing = { ...laterRun, events: laterRun.events.filter((event) => event.status !== "running") };
    expect(workflowObservedAt(missing)).toBeNull();
    expect(await parseWorkflowTrace(bound, missing)).toBeNull();
    const runningEvent = laterRun.events.find((event) => event.status === "running");
    const duplicate = { ...laterRun, events: [...laterRun.events, { ...runningEvent!, sequence: 9 }] };
    expect(workflowObservedAt(duplicate)).toBeNull();
    const otherAttempt = {
      ...laterRun,
      events: laterRun.events.map((event) => (event.status === "running" ? { ...event, attempt: 2 } : event)),
    };
    expect(workflowObservedAt(otherAttempt)).toBeNull();
    const retryRunning = "2026-10-12T16:00:04+00:00";
    const retryDone = "2026-10-12T16:00:05+00:00";
    const retry = withTrace(
      scientificRun("completed", {
        attempt: 2,
        updated_at: retryDone,
        events: [
          { attempt: 1, origin: "server", recorded_at: STAMP, sequence: 1, status: "queued", text: QUEUED_TEXT },
          { attempt: 1, origin: "server", recorded_at: LATER, sequence: 2, status: "running", text: RUNNING_TEXT },
          { attempt: 1, origin: "server", recorded_at: "2026-10-12T16:00:02+00:00", sequence: 3, status: "failed", text: FAILED_TEXT },
          { attempt: 2, origin: "server", recorded_at: "2026-10-12T16:00:03+00:00", sequence: 4, status: "queued", text: RETRY_QUEUED_TEXT },
          { attempt: 2, origin: "server", recorded_at: retryRunning, sequence: 5, status: "running", text: RUNNING_TEXT },
          { attempt: 2, origin: "server", recorded_at: retryDone, sequence: 6, status: "completed", text: COMPLETED_TEXT },
        ],
      }),
    );
    expect(workflowObservedAt(retry)).toBe(retryRunning);
    const retryTrace = await workflowBody(retry);
    expect(await parseWorkflowTrace(retryTrace, retry)).not.toBeNull();
    const previousAttempt = {
      ...retry,
      events: retry.events.map((event) =>
        event.recorded_at === retryRunning ? { ...event, recorded_at: LATER } : event,
      ),
    };
    expect(await parseWorkflowTrace(retryTrace, previousAttempt)).toBeNull();
    const canonical = canonicalJson(await workflowBody(run));
    expect(await acceptWorkflowText(canonical, run)).not.toBeNull();
    const duplicated = canonical.replace('"accepted":false', '"accepted":false,"accepted":true');
    expect(await acceptWorkflowText(duplicated, run)).toBeNull();
    expect(await acceptWorkflowText(` ${canonical}`, run)).toBeNull();
    const summary = await workflowBody(run);
    summary.stages[0].summary = "changed";
    expect(await parseWorkflowTrace(summary, run)).toBeNull();
  });

  it("rejects a malformed current-attempt event chain", async () => {
    const run = withTrace(scientificRun("completed", { updated_at: "2026-10-12T16:00:02+00:00" }));
    const trace = await workflowBody(run);
    const [queued, running, completed] = run.events;
    expect(workflowObservedAt(run)).toBe(LATER);

    async function reject(events: Array<Record<string, unknown>>) {
      const altered = { ...run, events: events as typeof run.events };
      expect(workflowObservedAt(altered)).toBeNull();
      expect(await parseWorkflowTrace(trace, altered)).toBeNull();
    }

    await reject([{ ...queued, origin: "client" }, running, completed]);
    await reject([queued, { ...running, origin: "operator" }, completed]);
    await reject([queued, running, { ...completed, origin: "" }]);
    await reject([queued, { ...running, sequence: queued.sequence + 2 }, completed]);
    await reject([queued, running, { ...completed, sequence: running.sequence + 2 }]);
    await reject([queued, { ...running, sequence: true }, completed]);
    await reject([{ ...queued, sequence: 1.5 }, running, completed]);
    await reject([running, queued, completed]);
    await reject([queued, running, running, completed]);
    await reject([queued, completed]);
    await reject([queued, { ...running, attempt: 2 }, completed]);
  });

  it.each([
    ["missing content-type", { "content-type": null }],
    ["wrong content-type", { "content-type": "text/plain" }],
    ["missing content-disposition", { "content-disposition": null }],
    ["wrong filename", { "content-disposition": 'attachment; filename="notes.json"' }],
    ["unsafe filename", { "content-disposition": 'attachment; filename="../scientific-agent-workflow.json"' }],
    ["malformed filename", { "content-disposition": 'attachment; filename="scientific-agent-workflow.json\r\nX: y"' }],
    ["missing content-length", { "content-length": null }],
    ["leading zero", { "content-length": "0128" }],
    ["plus sign", { "content-length": "+128" }],
    ["whitespace", { "content-length": " 128" }],
    ["fractional length", { "content-length": "128.0" }],
    ["negative length", { "content-length": "-1" }],
    ["length above 65536", { "content-length": "65537" }],
    ["missing cache-control", { "cache-control": null }],
    ["wrong cache-control", { "cache-control": "public, max-age=60" }],
    ["missing nosniff", { "x-content-type-options": null }],
    ["wrong nosniff", { "x-content-type-options": "sniff" }],
  ] as const)("rejects %s", async (_name, patch) => {
    const { run, text } = await sealedTrace();
    const artifactId = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
    const response = () => streamedResponse(text, artifactHeaders(new TextEncoder().encode(text).byteLength, patch));
    await expectSafeRead(run, artifactId, response);
  });

  it("rejects a declared length that differs from the artifact", async () => {
    const { run, text } = await sealedTrace();
    const artifactId = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
    const size = new TextEncoder().encode(text).byteLength;
    await expectSafeRead(run, artifactId, () => streamedResponse(MARKER, artifactHeaders(size, { "content-length": String(size + 1) })));
  });

  it("rejects a body shorter or longer than the declared size", async () => {
    const { run, text } = await sealedTrace();
    const artifactId = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
    const size = new TextEncoder().encode(text).byteLength;
    await expectSafeRead(run, artifactId, () => streamedResponse(MARKER, artifactHeaders(size)));
    cleanup();
    await expectSafeRead(run, artifactId, () => streamedResponse(`${text}${MARKER}`, artifactHeaders(size)));
  });

  it("cancels a stream that exceeds the cap", async () => {
    const { run } = await sealedTrace();
    const cancelReader = vi.spyOn(ReadableStreamDefaultReader.prototype, "cancel");
    const chunk = new Uint8Array(65537);
    chunk.set(new TextEncoder().encode(MARKER));
    const hostile = replaceTrace(run, { size: 65536, sha256: "ab".repeat(32) });
    const artifactId = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
    await expectSafeRead(hostile, artifactId, () =>
      streamedResponse(
        new ReadableStream<Uint8Array>({
          pull(controller) {
            controller.enqueue(chunk);
            controller.close();
          },
        }),
        artifactHeaders(65536),
      ),
    );
    expect(cancelReader).toHaveBeenCalled();
  });

  it("rejects metadata, digest, utf-8, and an empty body", async () => {
    const { run, text } = await sealedTrace();
    const artifactId = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
    const markerBytes = new TextEncoder().encode(MARKER);
    await expectSafeRead(replaceTrace(run, { size: 0 }), artifactId, () => streamedResponse(MARKER, artifactHeaders(0)), RECORD_TEXT);
    cleanup();
    await expectSafeRead(replaceTrace(run, { size: 65537 }), artifactId, () => streamedResponse(MARKER, artifactHeaders(65537)), RECORD_TEXT);
    cleanup();
    await expectSafeRead(replaceTrace(run, { sha256: "GG".repeat(32) }), artifactId, () => artifactResponse(text), RECORD_TEXT);
    cleanup();
    await expectSafeRead(replaceTrace(run, { size: markerBytes.byteLength, sha256: "ab".repeat(32) }), artifactId, () =>
      streamedResponse(MARKER, artifactHeaders(markerBytes.byteLength)),
    );
    cleanup();
    const byte = new Uint8Array([0xff]);
    await expectSafeRead(replaceTrace(run, { size: 1, sha256: await sha256Hex(byte) }), artifactId, () =>
      streamedResponse(byte, artifactHeaders(1)),
    );
    cleanup();
    const wide = new TextEncoder().encode("é");
    await expectSafeRead(replaceTrace(run, { size: "é".length, sha256: await sha256Hex(wide) }), artifactId, () =>
      streamedResponse(wide, artifactHeaders("é".length)),
    );
    cleanup();
    const size = new TextEncoder().encode(text).byteLength;
    await expectSafeRead(run, artifactId, () => {
      const response = streamedResponse(text, artifactHeaders(size));
      Object.defineProperty(response, "body", { configurable: true, get: () => null });
      return response;
    });
  });

  it("rejects a stale run, attempt, session, binding, or selection", async () => {
    const { run, text } = await sealedTrace();
    const artifactId = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
    const response = () => artifactResponse(text);
    await expectSafeRead({ ...run, run_id: OTHER_RUN }, `artifact_${OTHER_RUN}_${run.attempt}_workflow_trace`, response, RECORD_TEXT);
    cleanup();
    const retried = replaceTrace({ ...run, attempt: 2 }, {
      attempt: 2,
      artifact_id: `artifact_${run.run_id}_2_workflow_trace`,
    });
    await expectSafeRead(retried, retried.artifacts.find((item) => item.kind === "workflow_trace")?.artifact_id ?? "", response, RECORD_TEXT);
    cleanup();
    await expectSafeRead({ ...run, session_id: "session_other" }, artifactId, response, RECORD_TEXT);
    cleanup();
    await expectSafeRead({ ...run, request_revision: 9 }, artifactId, response, RECORD_TEXT);
    cleanup();
    await expectSafeRead(run, `artifact_${run.run_id}_${run.attempt}_execution_record`, () =>
      streamedResponse(MARKER, artifactHeaders(new TextEncoder().encode(MARKER).byteLength)),
    );
  });

  it("a stale artifact response does not replace a newer trace", async () => {
    const sealed = await sealedTrace();
    const bodies = [MARKER, new Uint8Array([0xff]), new TextEncoder().encode("é")];
    for (const body of bodies) {
      const late = deferred();
      let artifacts = 0;
      vi.mocked(fetch).mockImplementation(async (input) => {
        const url = String(input);
        if (url === "/api/scientific-runs") {
          return runList([sealed.run]);
        }
        if (url.includes("/artifacts/")) {
          artifacts += 1;
          return artifacts === 1 ? late.promise : artifactResponse(sealed.text);
        }
        if (url.includes("/authorizations/")) {
          return jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200);
        }
        return jsonResponse({}, 404);
      });
      const first = render(panel());
      await waitFor(() => expect(artifacts).toBe(1));
      first.unmount();
      render(panel());
      expect(await screen.findAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
      const size = body instanceof Uint8Array ? body.byteLength : new TextEncoder().encode(body).byteLength;
      late.resolve(streamedResponse(body, artifactHeaders(size)));
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
      expect(screen.queryByText(MARKER)).not.toBeInTheDocument();
      cleanup();
    }
  });

  it("renders the ordered operational timeline", async () => {
    const user = userEvent.setup();
    const { run: completed, text: body } = await sealedTrace();
    render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(completed, "run_observed"), 200));
    vi.mocked(fetch).mockResolvedValueOnce(artifactResponse(body));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    const region = await screen.findByRole("region", { name: "Governed agent workflow" });
    expect(await screen.findAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
    expect(region).toHaveTextContent("Deterministic prototype runtime");
    expect(region).toHaveTextContent("Operational orchestration evidence");
    expect(region).toHaveTextContent("This governed workflow is not scientific validation.");
    expect(region).toHaveTextContent("No scientific candidate generated");
    const text = region.textContent ?? "";
    const positions = WORKFLOW_STAGE_IDS.map((role) => text.indexOf(role));
    expect(positions.every((value, index) => index === 0 || value > positions[index - 1])).toBe(true);
    expect(screen.getByText("false")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Validate|Rank|Accept|Promote|Second Brain|BindCraft|Add agent/ }),
    ).not.toBeInTheDocument();
  });

  it("shows only the safe read failure for a malformed trace", async () => {
    const user = userEvent.setup();
    const completed = withTrace(scientificRun("completed"));
    render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(completed, "run_observed"), 200));
    vi.mocked(fetch).mockResolvedValueOnce(
      artifactResponse({ schema: "nope", summary: "traceback /tmp/token= SELECT" }),
    );
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findByText(ARTIFACT_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(/traceback|\/tmp\/|token=|SELECT/)).not.toBeInTheDocument();
  });

  it("drops a trace after any binding field changes", async () => {
    const user = userEvent.setup();
    const { run: completed, text } = await sealedTrace();
    const { rerender } = render(panel());
    await settled();
    const pending = deferred();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(completed, "run_observed"), 200));
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findAllByText(COMPLETED_TEXT)).not.toHaveLength(0);
    rerender(
      panel({
        ...authorization(),
        budget: { ...authorization().budget, token_ceiling: 99 },
      }),
    );
    pending.resolve(artifactResponse(text));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryAllByText(WORKFLOW_SUMMARY)).toHaveLength(0);
  });

  it("keeps remount restoration read-only after the setup start", async () => {
    const user = userEvent.setup();
    const { run: completed, text } = await sealedTrace();
    const { unmount } = render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(completed, "run_observed"), 200));
    vi.mocked(fetch).mockResolvedValueOnce(artifactResponse(text));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
    unmount();
    const before = vi.mocked(fetch).mock.calls.length;
    vi.mocked(fetch).mockResolvedValueOnce(runList([completed]));
    vi.mocked(fetch).mockResolvedValueOnce(artifactResponse(text));
    render(panel());
    expect(await screen.findAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
    const restoration = vi.mocked(fetch).mock.calls.slice(before);
    expect(restoration.filter((call) => call[1]?.method === "POST")).toHaveLength(0);
    expect(restoration.map((call) => call[1]?.method ?? "GET")).toEqual(["GET", "GET"]);
    expect(vi.mocked(fetch).mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);
  });

  it("restores an existing governed run without a POST", async () => {
    const seen = vi.fn();
    const { run: completed, text } = await sealedTrace();
    const stale = deferred();
    const calls: Array<{ method: string; url: string }> = [];
    let mounted = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ method, url });
      if (url === "/api/scientific-runs" && !mounted) {
        return stale.promise;
      }
      if (url === "/api/scientific-runs") {
        return runList([completed]);
      }
      if (url.startsWith("/api/scientific-run-authorizations/")) {
        return jsonResponse(authorizationEnvelope(authorization(false), "authorization_observed"), 200);
      }
      if (url.includes("/artifacts/")) {
        return artifactResponse(text);
      }
      return jsonResponse({}, 404);
    });
    const intervals = vi.spyOn(window, "setInterval");
    const first = render(panel(authorization(), seen));
    await act(async () => {
      await Promise.resolve();
    });
    first.unmount();
    mounted = true;
    const marked = calls.length;
    render(panel(authorization(), seen));
    expect(await screen.findAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
    await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    expect(seen.mock.calls[0][0].execution_authorized).toBe(false);
    const restoration = calls.slice(marked);
    expect(restoration.filter((call) => call.method === "POST")).toHaveLength(0);
    expect(restoration.map((call) => `${call.method} ${call.url}`)).toEqual([
      "GET /api/scientific-runs",
      `GET /api/scientific-run-authorizations/${AUTHORIZATION_ID}`,
      `GET /api/scientific-runs/${RUN_ID}/artifacts/artifact_${RUN_ID}_1_workflow_trace`,
    ]);
    expect(restoration.some((call) => call.url.endsWith("/retry"))).toBe(false);
    expect(intervals.mock.calls.filter((call) => call[1] === 1000)).toHaveLength(0);
    const visible = screen.getAllByText(WORKFLOW_SUMMARY).length;
    await act(async () => {
      stale.resolve(runList([scientificRun("failed", { run_id: OTHER_RUN })]));
      await Promise.resolve();
    });
    expect(screen.getAllByText(WORKFLOW_SUMMARY)).toHaveLength(visible);
    expect(screen.queryByText(FAILED_TEXT)).not.toBeInTheDocument();
    intervals.mockRestore();
  });

  it("polls a restored queued run with GET", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const queued = scientificRun("queued");
    const calls: string[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push(`${method} ${url}`);
      if (url === "/api/scientific-runs") {
        return runList([queued]);
      }
      if (url === `/api/scientific-runs/${RUN_ID}`) {
        return jsonResponse(reply(queued, "run_observed"), 200);
      }
      return jsonResponse({}, 404);
    });
    render(panel());
    expect(await screen.findByText(QUEUED_TEXT)).toBeInTheDocument();
    const before = calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(calls.slice(before)).toEqual([`GET /api/scientific-runs/${RUN_ID}`]);
    expect(calls.every((call) => call.startsWith("GET "))).toBe(true);
    vi.useRealTimers();
  });

  it("keeps historical inspection off the active workflow", async () => {
    const user = userEvent.setup();
    const { run: completed, text } = await sealedTrace();
    const historical = scientificRun("failed", { run_id: OTHER_RUN, updated_at: LATER });
    render(panel());
    await settled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply(completed, "run_observed"), 200));
    vi.mocked(fetch).mockResolvedValueOnce(artifactResponse(text));
    await user.click(screen.getByRole("button", { name: "Start scientific run" }));
    expect(await screen.findAllByText(WORKFLOW_SUMMARY)).toHaveLength(5);
    vi.mocked(fetch).mockResolvedValueOnce(runList([historical]));
    await user.click(screen.getByRole("button", { name: "Scientific run history" }));
    await user.click(await screen.findByRole("button", { name: OTHER_RUN }));
    expect(screen.getByRole("region", { name: "Governed agent workflow" })).toHaveTextContent(WORKFLOW_SUMMARY);
    expect(screen.getByRole("region", { name: "Historical scientific run" })).toHaveTextContent(FAILED_TEXT);
  });
});

describe("scientific run selection", () => {
  it("accepts a docker profile and an external failure message", async () => {
    const failed = scientificRun("failed", {
      profile_id: "pdl1-bindcraft-docker-v1",
      text: EXTERNAL_FAILED_TEXT,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => reply(failed, "run_observed"),
      }),
    );
    const observed = await readScientificRun(failed.run_id);
    expect(observed.run?.profile_id).toBe("pdl1-bindcraft-docker-v1");
    expect(observed.run?.text).toBe(EXTERNAL_FAILED_TEXT);
  });

  it("drops stale, cross-run, cross-attempt, and cross-revision records", () => {
    const current = scientificRun("running", { updated_at: LATER });
    const binding = { authorizationId: AUTHORIZATION_ID, revision: 4 };
    expect(selectScientificRun(current, scientificRun("queued"), 1, 2, binding)).toBe(current);
    expect(selectScientificRun(current, scientificRun("queued"), 2, 2, binding)).toBe(current);
    expect(selectScientificRun(current, scientificRun("failed", { run_id: OTHER_RUN }), 2, 2, binding)).toBe(current);
    expect(selectScientificRun(current, scientificRun("failed", { attempt: 1, updated_at: STAMP }), 2, 2, binding)).toBe(current);
    expect(selectScientificRun(current, scientificRun("completed", { request_revision: 9, updated_at: LATER }), 2, 2, binding)).toBe(
      current,
    );
    const next = scientificRun("failed", { updated_at: "2026-10-12T16:00:02+00:00" });
    expect(selectScientificRun(current, next, 2, 2, binding)).toBe(next);
  });
});
