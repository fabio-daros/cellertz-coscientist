import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ScientificAuthorizationPanel } from "./ScientificAuthorizationPanel";
import { prototypeWorkspace } from "./fixture";
import type { ApprovedRunReply } from "./approved-run";
import type { DesignDraftReply } from "./contracts";
import type { GateReply } from "./approval";
import type { ReadinessReply } from "./scientific-runner-readiness";
import {
  AUTHORIZED_TEXT,
  CONSUMED_TEXT,
  createScientificAuthorization,
  listScientificAuthorizations,
  readScientificAuthorization,
  revokeScientificAuthorization,
  selectAuthorization,
  selectAuthorizationHistory,
  type AuthorizationRecord,
} from "./scientific-authorization";

const STAMP = "2026-10-12T16:00:00+00:00";
const LATER = "2026-10-12T16:00:01+00:00";

function draftReply(revision = 4, target = "PDL1"): DesignDraftReply {
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
        { name: "target_id", value: target, provenance: "user" },
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

function runReply(revision = 4): ApprovedRunReply {
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
      request_revision: revision,
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
    checks: [
      { code: "tool_identity", status: "passed", text: "Recorded." },
      { code: "tool_version", status: "passed", text: "Recorded." },
      { code: "runtime_identity", status: "passed", text: "Recorded." },
      { code: "image_identity", status: "passed", text: "Recorded." },
      { code: "target_scope", status: "passed", text: "Recorded." },
      { code: "execution_profile", status: "passed", text: "Recorded." },
      { code: "argv_template", status: "passed", text: "Recorded." },
      { code: "entrypoint", status: "passed", text: "Recorded." },
      { code: "expected_inputs", status: "passed", text: "Recorded." },
      { code: "expected_outputs", status: "passed", text: "Recorded." },
      { code: "resource_envelope", status: "passed", text: "Recorded." },
      { code: "timeout_limit", status: "passed", text: "Recorded." },
      { code: "output_limit", status: "passed", text: "Recorded." },
      { code: "readiness_probe", status: "passed", text: "The readiness probe succeeded." },
      { code: "runner_disabled", status: "passed", text: "Recorded." },
    ],
    execution_authorized: false,
    scientific_execution: false,
    scientific_validation: false,
  };
}

function authorizationRecord(
  status: "authorized" | "revoked" = "authorized",
  revision = 4,
  updatedAt = STAMP,
): AuthorizationRecord {
  const authorized = status === "authorized";
  return {
    authorization_id: `authorization_${"ab".repeat(16)}`,
    session_id: prototypeWorkspace.session_id,
    request_id: "request_1",
    request_revision: revision,
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
      action_on_limit: "stop",
      authorizes_execution: false,
      currency: null,
      human_override_required: true,
      model_cost_ceiling: null,
      scientific_tool_cost_ceiling: null,
      token_ceiling: null,
      warning_threshold: null,
    },
    status,
    created_at: STAMP,
    updated_at: updatedAt,
    events: authorized
      ? [
          {
            sequence: 1,
            status: "authorized",
            recorded_at: STAMP,
            text: AUTHORIZED_TEXT,
            origin: "server",
          },
        ]
      : [
          {
            sequence: 1,
            status: "authorized",
            recorded_at: STAMP,
            text: AUTHORIZED_TEXT,
            origin: "server",
          },
          {
            sequence: 2,
            status: "revoked",
            recorded_at: updatedAt,
            text: "Authorization revoked. No scientific execution has started.",
            origin: "server",
          },
        ],
    execution_authorized: authorized,
    scientific_execution: false,
    scientific_validation: false,
    scientific_candidate_generated: false,
    single_use: "This authorization may be consumed once by a future scientific executor.",
    text: authorized
      ? AUTHORIZED_TEXT
      : "Authorization revoked. No scientific execution has started.",
    validation_text: "This is not scientific validation.",
  };
}

function authorizationReply(record: AuthorizationRecord, status = "authorization_created") {
  return {
    status,
    text: record.text,
    session_id: record.session_id,
    authorization: record,
    execution_authorized: record.execution_authorized,
    scientific_execution: false,
    scientific_validation: false,
    scientific_candidate_generated: false,
  };
}

function jsonResponse(body: unknown, status = 201): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function deferredResponse() {
  let resolve: (value: Response) => void = () => {};
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function valueFor(region: HTMLElement, label: string): string {
  const term = [...region.querySelectorAll("dt")].find((node) => node.textContent === label);
  return term?.parentElement?.querySelector("dd")?.textContent ?? "";
}

function panel(revision = 4, target = "PDL1") {
  return (
    <div className="stage-main">
      <p>Active draft revision {revision}</p>
      <ScientificAuthorizationPanel
        draftReply={draftReply(revision, target)}
        decisionReply={decisionReply()}
        executionReply={executionReply()}
        runReply={runReply(revision)}
        readiness={readiness()}
        budget={prototypeWorkspace.budget}
      />
    </div>
  );
}

describe("scientific run authorization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("confirms only after a separate review and submits identifiers only", async () => {
    const user = userEvent.setup();
    render(panel());
    const review = screen.getByRole("button", { name: "Review scientific authorization" });
    expect(review).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Confirm scientific authorization" })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    await user.click(review);
    expect(screen.getByText("pdl1-bindcraft-historical-v1")).toBeInTheDocument();
    expect(screen.getByText("This is not scientific validation.")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(authorizationReply(authorizationRecord())));
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByText(AUTHORIZED_TEXT)).toBeInTheDocument();
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/api/scientific-run-authorizations");
    expect(init?.method).toBe("POST");
    const submitted = JSON.parse(String(init?.body));
    expect(submitted).toEqual({ approval_id: "approval_1", idempotency_key: "scientific-approval_1" });
    expect(screen.queryByRole("button", { name: /BindCraft|Generate candidate|Promote|Execute/ })).not.toBeInTheDocument();
  });

  it("shows every bound budget field in the review summary", async () => {
    const user = userEvent.setup();
    render(panel());
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    const region = screen.getByRole("region", { name: "Scientific run authorization" });
    expect(valueFor(region, "Token ceiling")).toBe("Not recorded");
    expect(valueFor(region, "Model cost ceiling")).toBe("Not recorded");
    expect(valueFor(region, "Scientific tool cost ceiling")).toBe("Not recorded");
    expect(valueFor(region, "Action on limit")).toBe("stop");
    expect(valueFor(region, "Warning threshold")).toBe("Not recorded");
    expect(valueFor(region, "Currency")).toBe("Not recorded");
    expect(valueFor(region, "Human override required")).toBe("true");
    expect(valueFor(region, "Authorizes execution")).toBe("false");
  });

  it("keeps CD22 blocked and does not authorize from readiness alone", () => {
    render(panel(4, "CD22"));
    expect(screen.getByRole("button", { name: "Review scientific authorization" })).toBeDisabled();
    expect(screen.getByText("CD22 and TACI remain blocked.")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("drops a stale confirmation and keeps history inspection off the active draft", async () => {
    const pending = deferredResponse();
    const current = authorizationRecord();
    current.authorization_id = `authorization_${"cd".repeat(16)}`;
    const historical = authorizationRecord("authorized", 3);
    historical.authorization_id = `authorization_${"ef".repeat(16)}`;
    vi.mocked(fetch)
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(jsonResponse({
        status: "authorizations_observed",
        text: "Scientific authorization history. Not scientific execution.",
        session_id: prototypeWorkspace.session_id,
        authorizations: [current, historical],
        execution_authorized: false,
        scientific_execution: false,
        scientific_validation: false,
        scientific_candidate_generated: false,
      }, 200))
      .mockResolvedValueOnce(
        jsonResponse(authorizationReply(historical, "authorization_observed"), 200),
      );
    const view = render(panel());
    expect(screen.getByText("Refresh authorization history to load records.")).toBeInTheDocument();
    expect(screen.queryByText("No scientific authorization is recorded for this session.")).not.toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    const confirm = screen.getByRole("button", { name: "Confirm scientific authorization" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Show authorization history" })).toBeDisabled();
    await act(async () => {
      pending.resolve(jsonResponse(authorizationReply(current)));
    });
    expect(await screen.findByText(AUTHORIZED_TEXT)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show authorization history" }));
    await user.click(await screen.findByRole("button", { name: `Open ${historical.authorization_id}` }));
    expect(await screen.findByText("Historical revision")).toBeInTheDocument();
    expect(screen.getByLabelText("Historical authorization")).toHaveTextContent("3");
    expect(screen.getByText(AUTHORIZED_TEXT)).toBeInTheDocument();
    expect(screen.getByText("Active draft revision 4")).toBeInTheDocument();
    view.rerender(panel(5));
    expect(screen.queryByText(AUTHORIZED_TEXT)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Open ${historical.authorization_id}` })).toBeInTheDocument();
  });

  it("rejects a boolean disguised as an integer and a path in the reply", async () => {
    const user = userEvent.setup();
    render(panel());
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    const forged = authorizationReply(authorizationRecord()) as { execution_authorized: unknown; text: string };
    forged.execution_authorized = 1;
    forged.text = "see /tmp/cellertz-secret";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(forged));
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be recorded");
    expect(screen.queryByText(AUTHORIZED_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByText(/cellertz-secret/)).not.toBeInTheDocument();
  });

  it("rejects a coerced digest, a drifted binding, and a created reply on HTTP 200", async () => {
    const user = userEvent.setup();
    render(panel());
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    const coerced = authorizationReply(authorizationRecord()) as {
      authorization: { request_digest: unknown };
    };
    coerced.authorization.request_digest = ["ab".repeat(32)];
    const drifted = authorizationRecord();
    drifted.manifest_digest = "ef".repeat(32);
    const wrongHttp = authorizationReply(authorizationRecord());
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(coerced))
      .mockResolvedValueOnce(jsonResponse(authorizationReply(drifted)))
      .mockResolvedValueOnce(jsonResponse(wrongHttp, 200));
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be recorded");
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be recorded");
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be recorded");
    expect(screen.queryByText(AUTHORIZED_TEXT)).not.toBeInTheDocument();
  });

  it("shows a consumed replay as non-success and rejects revoke after consumption", async () => {
    const user = userEvent.setup();
    render(panel());
    await user.click(screen.getByRole("button", { name: "Review scientific authorization" }));
    const consumed = authorizationRecord();
    consumed.execution_authorized = false;
    consumed.text = CONSUMED_TEXT;
    const unstarted = authorizationRecord();
    unstarted.execution_authorized = false;
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(authorizationReply(unstarted, "authorization_replayed"), 200),
    );
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be recorded");
    expect(screen.queryByText(AUTHORIZED_TEXT)).not.toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(authorizationReply(consumed, "authorization_replayed"), 200),
    );
    await user.click(screen.getByRole("button", { name: "Confirm scientific authorization" }));
    expect(await screen.findByRole("status")).toHaveTextContent(CONSUMED_TEXT);
    expect(screen.queryByText(AUTHORIZED_TEXT)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke scientific authorization" })).not.toBeInTheDocument();
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          status: "authorization_rejected",
          text: "Scientific authorization was rejected. No scientific action was taken.",
          session_id: prototypeWorkspace.session_id,
          authorization: null,
          execution_authorized: false,
          scientific_execution: false,
          scientific_validation: false,
          scientific_candidate_generated: false,
        },
        409,
      ),
    );
    const refused = await revokeScientificAuthorization(
      consumed.authorization_id,
      `revoke-${consumed.authorization_id}`,
    );
    expect(refused.status).toBe("authorization_rejected");
    expect(refused.text).not.toContain("No scientific execution has started");
  });

  it("accepts approval_absent only as HTTP 409 and a consumed read or list", async () => {
    const absent = {
      status: "approval_absent",
      text: "The approval is not available. No scientific action was taken.",
      session_id: prototypeWorkspace.session_id,
      authorization: null,
      execution_authorized: false,
      scientific_execution: false,
      scientific_validation: false,
      scientific_candidate_generated: false,
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(absent, 200));
    await expect(createScientificAuthorization("approval_1", "missing")).rejects.toThrow(
      "authorization_rejected",
    );
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(absent, 409));
    await expect(createScientificAuthorization("approval_1", "missing")).resolves.toMatchObject({
      status: "approval_absent",
    });
    const consumed = authorizationRecord();
    consumed.execution_authorized = false;
    consumed.text = CONSUMED_TEXT;
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(authorizationReply(consumed, "authorization_observed"), 200),
    );
    const observed = await readScientificAuthorization(consumed.authorization_id);
    expect(observed.text).toBe(CONSUMED_TEXT);
    expect(observed.authorization?.execution_authorized).toBe(false);
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(
        {
          status: "authorizations_observed",
          text: "Scientific authorization history. Not scientific execution.",
          session_id: prototypeWorkspace.session_id,
          authorizations: [consumed],
          execution_authorized: false,
          scientific_execution: false,
          scientific_validation: false,
          scientific_candidate_generated: false,
        },
        200,
      ),
    );
    const listed = await listScientificAuthorizations();
    expect(listed.authorizations[0]?.text).toBe(CONSUMED_TEXT);
    expect(listed.authorizations[0]?.execution_authorized).toBe(false);
  });

  it("keeps the authorization section inside a 390px stage", () => {
    render(
      <div style={{ width: "390px", maxWidth: "390px", overflowX: "hidden" }}>{panel()}</div>,
    );
    const section = screen.getByRole("region", { name: "Scientific run authorization" });
    expect(section).toHaveClass("scientific-authorization");
    expect(section.scrollWidth).toBeLessThanOrEqual(390);
    expect(screen.queryByRole("button", { name: /BindCraft|Generate candidate|Promote|^Execute$/ })).not.toBeInTheDocument();
  });
});

describe("authorization reply selection", () => {
  it("keeps the newer record when an older reply arrives late", () => {
    const current = authorizationRecord("authorized", 4, LATER);
    const older = authorizationRecord("revoked", 4, STAMP);
    expect(selectAuthorization(current, older, 1, 2)).toBe(current);
    expect(selectAuthorization(current, older, 2, 2)).toBe(current);
    expect(selectAuthorizationHistory([current], [older], 1, 2)).toEqual([current]);
    const revoked = authorizationRecord("revoked", 4, STAMP);
    const authorized = authorizationRecord("authorized", 4, STAMP);
    expect(selectAuthorization(revoked, authorized, 1, 1)).toBe(revoked);
    expect(selectAuthorizationHistory([revoked], [authorized], 1, 1)).toEqual([revoked]);
  });
});
