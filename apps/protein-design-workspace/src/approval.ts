import { type DesignDraftReply, type ExecutionBudget } from "./contracts";

export const APPROVAL_PATH = "/api/design-approval";
export const EXECUTION_PATH = "/api/approved-execution";

export type ApprovalReceipt = {
  approval_id: string;
  session_id: string;
  request_id: string;
  request_revision: number;
  revision_digest: string;
  decision: "approved" | "rejected" | "changes_requested" | "held";
  reviewer_id: string;
  reason: string;
  recorded_at: string;
  mode: string;
  status: string;
  reviewer_identity: "operator_asserted";
  production_authentication: false;
  execution_authorized: false;
  executable: false;
};

export type DemonstrationReceipt = {
  dispatch_id: string;
  approval_id: string;
  idempotency_key: string;
  execution_profile: string;
  authorization_status: string;
  preflight_status: string;
  dispatch_status: string;
  request_digest: string;
  text: string;
  candidates: [];
  scientific_evidence: [];
  scientific_memory_written: false;
  scientific_execution: false;
};

export type GateReply = {
  status: string;
  text: string;
  session_id: string;
  execution_authorized: false;
  executable: false;
  authorization_status: string;
  preflight_status: string;
  dispatch_status: string;
  approval: ApprovalReceipt | null;
  demonstration: DemonstrationReceipt | null;
  candidates: [];
  scientific_evidence: [];
  scientific_memory_written: false;
};

export function failedGateReply(sessionId: string): GateReply {
  return {
    status: "adapter_failure",
    text: "The demonstration adapter failed. No scientific action was taken.",
    session_id: sessionId,
    execution_authorized: false,
    executable: false,
    authorization_status: "not_run",
    preflight_status: "not_run",
    dispatch_status: "not_dispatched",
    approval: null,
    demonstration: null,
    candidates: [],
    scientific_evidence: [],
    scientific_memory_written: false,
  };
}

async function postGate(path: string, body: object, sessionId: string): Promise<GateReply> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok && response.status !== 409) {
    return failedGateReply(sessionId);
  }
  try {
    const payload = (await response.json()) as GateReply;
    if (payload.execution_authorized !== false || payload.executable !== false || typeof payload.text !== "string") {
      return failedGateReply(sessionId);
    }
    return payload;
  } catch {
    return failedGateReply(sessionId);
  }
}

export function recordHumanDecision(input: {
  session_id: string;
  request_id: string;
  revision: number;
  decision: "approved" | "rejected" | "changes_requested" | "held";
  reviewer_id: string;
  reason: string;
  idempotency_key: string;
}): Promise<GateReply> {
  return postGate(APPROVAL_PATH, input, input.session_id);
}

export function dispatchApprovedDemonstration(input: {
  session_id: string;
  approval_id: string;
  idempotency_key: string;
}): Promise<GateReply> {
  return postGate(EXECUTION_PATH, input, input.session_id);
}

export function recordedBudgetLabel(budget: ExecutionBudget): string {
  const ceiling = budget.model_cost_ceiling === null ? "Unknown" : String(budget.model_cost_ceiling);
  return `Token ceiling ${budget.token_ceiling ?? "Unknown"}. Model cost ceiling ${ceiling}. A budget does not authorize execution.`;
}

export function draftGenerator(reply: DesignDraftReply | null): string | null {
  const field = reply?.draft?.fields.find((item) => item.name === "generator");
  return typeof field?.value === "string" ? field.value : null;
}
