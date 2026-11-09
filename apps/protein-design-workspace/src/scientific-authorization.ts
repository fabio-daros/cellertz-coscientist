import { observationInstant } from "./scientific-runner-readiness";
import type { ReadinessReply } from "./scientific-runner-readiness";
import type { DesignDraftReply, ExecutionBudget } from "./contracts";
import type { GateReply } from "./approval";
import type { ApprovedRunReply } from "./approved-run";

export const AUTHORIZATIONS_PATH = "/api/scientific-run-authorizations";
export const AUTHORIZED_TEXT = "Authorized — no scientific execution has started.";
export const CONSUMED_TEXT =
  "This authorization has been consumed. See the scientific run record for execution status.";
export const REVOKED_TEXT = "Authorization revoked. No scientific execution has started.";
export const SINGLE_USE_TEXT =
  "This authorization may be consumed once by a future scientific executor.";
export const VALIDATION_TEXT = "This is not scientific validation.";
const HISTORY_TEXT = "Scientific authorization history. Not scientific execution.";
const CONFLICT_TEXT = "The idempotency key does not match this authorization.";
const ADAPTER_TEXT =
  "The scientific authorization adapter failed. No scientific action was taken.";
const UNUSABLE_TEXT = "The authorization record could not be used. No scientific action was taken.";
const REJECTED_TEXT = "The authorization payload was rejected. No scientific action was taken.";
const ABSENT_TEXT = "The scientific authorization is not available.";

const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/;
const KEY = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const IMAGE = /^sha256:[0-9a-f]{64}$/;
const AUTHORIZATION_ID = /^authorization_[0-9a-f]{32}$/;
const OBSERVATION_ID = /^observation_[0-9a-f]{64}$/;
const REJECTED_VERSIONS = new Set(["latest", "current", "unknown"]);
const UNSAFE = /traceback|\/users\/|\/tmp\/|token=|sqlite|select\s/i;

const BUDGET_KEYS = [
  "action_on_limit",
  "authorizes_execution",
  "currency",
  "human_override_required",
  "model_cost_ceiling",
  "scientific_tool_cost_ceiling",
  "token_ceiling",
  "warning_threshold",
] as const;

const EVENT_KEYS = ["origin", "recorded_at", "sequence", "status", "text"] as const;

const RECORD_KEYS = [
  "approval_id",
  "authorization_id",
  "budget",
  "created_at",
  "events",
  "execution_authorized",
  "image_digest",
  "manifest_digest",
  "profile_id",
  "readiness_observation_id",
  "readiness_observed_at",
  "request_digest",
  "request_id",
  "request_revision",
  "runtime_name",
  "runtime_version",
  "scientific_candidate_generated",
  "scientific_execution",
  "scientific_validation",
  "session_id",
  "single_use",
  "status",
  "target_scope",
  "text",
  "tool_name",
  "tool_version",
  "updated_at",
  "validation_text",
] as const;

const REPLY_KEYS = [
  "authorization",
  "execution_authorized",
  "scientific_candidate_generated",
  "scientific_execution",
  "scientific_validation",
  "session_id",
  "status",
  "text",
] as const;

const LIST_KEYS = [
  "authorizations",
  "execution_authorized",
  "scientific_candidate_generated",
  "scientific_execution",
  "scientific_validation",
  "session_id",
  "status",
  "text",
] as const;

export type AuthorizationBudget = {
  action_on_limit: "warn" | "stop";
  authorizes_execution: false;
  currency: string | null;
  human_override_required: boolean;
  model_cost_ceiling: number | null;
  scientific_tool_cost_ceiling: number | null;
  token_ceiling: number | null;
  warning_threshold: number | null;
};

export type AuthorizationEvent = {
  sequence: number;
  status: "authorized" | "revoked";
  recorded_at: string;
  text: string;
  origin: "server";
};

export type AuthorizationRecord = {
  authorization_id: string;
  session_id: string;
  request_id: string;
  request_revision: number;
  approval_id: string;
  request_digest: string;
  readiness_observation_id: string;
  readiness_observed_at: string;
  profile_id: "pdl1-bindcraft-historical-v1" | "pdl1-bindcraft-docker-v1";
  tool_name: "bindcraft";
  tool_version: string;
  runtime_name: string;
  runtime_version: string;
  image_digest: string;
  manifest_digest: string;
  target_scope: "PDL1";
  budget: AuthorizationBudget;
  status: "authorized" | "revoked";
  created_at: string;
  updated_at: string;
  events: AuthorizationEvent[];
  execution_authorized: boolean;
  scientific_execution: false;
  scientific_validation: false;
  scientific_candidate_generated: false;
  single_use: typeof SINGLE_USE_TEXT;
  text: string;
  validation_text: typeof VALIDATION_TEXT;
};

export type AuthorizationReply = {
  status: string;
  text: string;
  session_id: string;
  authorization: AuthorizationRecord | null;
  execution_authorized: boolean;
  scientific_execution: false;
  scientific_validation: false;
  scientific_candidate_generated: false;
};

export type AuthorizationListReply = {
  status: string;
  text: string;
  session_id: string;
  authorizations: AuthorizationRecord[];
  execution_authorized: false;
  scientific_execution: false;
  scientific_validation: false;
  scientific_candidate_generated: false;
};

export type AuthorizationSummary = {
  target: "PDL1";
  requestId: string;
  revision: number;
  approvalId: string;
  profileId: "pdl1-bindcraft-historical-v1";
  toolName: "bindcraft";
  toolVersion: string;
  runtimeName: string;
  runtimeVersion: string;
  imageDigest: string;
  manifestDigest: string;
  observedAt: string;
  tokenCeiling: number | null;
  modelCostCeiling: number | null;
  scientificToolCostCeiling: number | null;
  budget: AuthorizationBudget;
};

export function authorizationReviewReady(input: {
  draftReply: DesignDraftReply | null;
  decisionReply: GateReply | null;
  executionReply: GateReply | null;
  runReply: ApprovedRunReply | null;
  readiness: ReadinessReply | null;
  budget: ExecutionBudget;
}): AuthorizationSummary | null {
  const draft = input.draftReply?.draft ?? null;
  const approval = input.decisionReply?.approval ?? null;
  const demonstration = input.executionReply?.demonstration ?? null;
  const run = input.runReply?.run ?? null;
  const readiness = input.readiness;
  const target = draft?.fields.find((field) => field.name === "target_id")?.value;
  const generator = draft?.fields.find((field) => field.name === "generator")?.value;
  if (
    !draft ||
    draft.completeness !== "complete" ||
    draft.execution_authorized !== false ||
    target !== "PDL1" ||
    generator !== "fixture" ||
    !approval ||
    approval.decision !== "approved" ||
    approval.request_id !== draft.request_id ||
    approval.request_revision !== draft.revision ||
    approval.execution_authorized !== false ||
    input.executionReply?.status !== "demonstration_completed" ||
    !demonstration ||
    demonstration.approval_id !== approval.approval_id ||
    demonstration.execution_profile !== "phase6d_fixture_only" ||
    demonstration.scientific_execution !== false ||
    !run ||
    run.status !== "completed" ||
    run.approval_id !== approval.approval_id ||
    run.request_id !== draft.request_id ||
    run.request_revision !== draft.revision ||
    run.execution_profile !== "phase6d_fixture_only" ||
    run.fixture !== true ||
    run.scientific_execution !== false ||
    run.scientific_validation !== false ||
    !Number.isInteger(run.attempt) ||
    run.attempt < 1 ||
    run.budget.authorizes_execution !== false ||
    input.budget.authorizes_execution !== false ||
    run.budget.token_ceiling !== input.budget.token_ceiling ||
    run.budget.model_cost_ceiling !== input.budget.model_cost_ceiling ||
    run.budget.scientific_tool_cost_ceiling !== input.budget.scientific_tool_cost_ceiling ||
    !readiness ||
    readiness.status !== "ready" ||
    readiness.execution_authorized !== false ||
    readiness.scientific_execution !== false ||
    readiness.scientific_validation !== false ||
    readiness.profile_id !== "pdl1-bindcraft-historical-v1" ||
    readiness.tool_name !== "bindcraft" ||
    readiness.target_scope !== "PDL1" ||
    readiness.tool_version === null ||
    readiness.runtime_name === null ||
    readiness.runtime_version === null ||
    readiness.image_digest === null ||
    readiness.manifest_digest === null ||
    observationInstant(readiness.observed_at) === null
  ) {
    return null;
  }
  return {
    target: "PDL1",
    requestId: draft.request_id,
    revision: draft.revision,
    approvalId: approval.approval_id,
    profileId: "pdl1-bindcraft-historical-v1",
    toolName: "bindcraft",
    toolVersion: readiness.tool_version,
    runtimeName: readiness.runtime_name,
    runtimeVersion: readiness.runtime_version,
    imageDigest: readiness.image_digest,
    manifestDigest: readiness.manifest_digest,
    observedAt: readiness.observed_at,
    tokenCeiling: input.budget.token_ceiling,
    modelCostCeiling: input.budget.model_cost_ceiling,
    scientificToolCostCeiling: input.budget.scientific_tool_cost_ceiling,
    budget: {
      action_on_limit: input.budget.action_on_limit,
      authorizes_execution: false,
      currency: input.budget.currency,
      human_override_required: input.budget.human_override_required,
      model_cost_ceiling: input.budget.model_cost_ceiling,
      scientific_tool_cost_ceiling: input.budget.scientific_tool_cost_ceiling,
      token_ceiling: input.budget.token_ceiling,
      warning_threshold: input.budget.warning_threshold,
    },
  };
}

export function recordMatchesSummary(
  record: AuthorizationRecord,
  summary: AuthorizationSummary,
): boolean {
  return (
    record.request_id === summary.requestId &&
    record.request_revision === summary.revision &&
    record.approval_id === summary.approvalId &&
    record.profile_id === summary.profileId &&
    record.tool_name === summary.toolName &&
    record.tool_version === summary.toolVersion &&
    record.runtime_name === summary.runtimeName &&
    record.runtime_version === summary.runtimeVersion &&
    record.image_digest === summary.imageDigest &&
    record.manifest_digest === summary.manifestDigest &&
    record.target_scope === summary.target &&
    record.readiness_observed_at === summary.observedAt &&
    sameBudget(record.budget, summary.budget)
  );
}

export function selectAuthorization(
  current: AuthorizationRecord | null,
  incoming: AuthorizationRecord | null,
  operation: number,
  latestOperation: number,
): AuthorizationRecord | null {
  if (operation !== latestOperation) {
    return current;
  }
  if (!incoming) {
    return current;
  }
  if (current && incoming.authorization_id === current.authorization_id) {
    const currentInstant = observationInstant(current.updated_at);
    const incomingInstant = observationInstant(incoming.updated_at);
    if (currentInstant !== null && incomingInstant !== null) {
      const older = incomingInstant < currentInstant;
      const sameMomentRegression =
        incomingInstant === currentInstant && !laterLifecycle(current, incoming);
      if (older || sameMomentRegression) {
        return current;
      }
    }
  }
  return incoming;
}

function laterLifecycle(current: AuthorizationRecord, incoming: AuthorizationRecord): boolean {
  if (incoming.events.length < current.events.length) {
    return false;
  }
  return !(current.status === "revoked" && incoming.status === "authorized");
}

export function selectAuthorizationHistory(
  current: AuthorizationRecord[],
  incoming: AuthorizationRecord[],
  operation: number,
  latestOperation: number,
): AuthorizationRecord[] {
  if (operation !== latestOperation) {
    return current;
  }
  const currentNewest = current[0] ? observationInstant(current[0].updated_at) : null;
  const incomingNewest = incoming[0] ? observationInstant(incoming[0].updated_at) : null;
  if (currentNewest !== null && incomingNewest !== null && incomingNewest < currentNewest) {
    return current;
  }
  const currentHead = current[0];
  const incomingHead = incoming[0];
  if (
    currentHead &&
    incomingHead &&
    currentHead.authorization_id === incomingHead.authorization_id &&
    currentNewest !== null &&
    currentNewest === incomingNewest &&
    !laterLifecycle(currentHead, incomingHead)
  ) {
    return current;
  }
  return incoming;
}

export function createScientificAuthorization(
  approvalId: string,
  idempotencyKey: string,
): Promise<AuthorizationReply> {
  return postAuthorization(AUTHORIZATIONS_PATH, {
    approval_id: approvalId,
    idempotency_key: idempotencyKey,
  });
}

export function revokeScientificAuthorization(
  authorizationId: string,
  idempotencyKey: string,
): Promise<AuthorizationReply> {
  if (!AUTHORIZATION_ID.test(authorizationId)) {
    return Promise.reject(new Error("authorization_rejected"));
  }
  return postAuthorization(`${AUTHORIZATIONS_PATH}/${authorizationId}/revoke`, {
    idempotency_key: idempotencyKey,
  });
}

export async function listScientificAuthorizations(): Promise<AuthorizationListReply> {
  const response = await fetch(AUTHORIZATIONS_PATH, {
    method: "GET",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  return parseList(response);
}

export async function readScientificAuthorization(
  authorizationId: string,
): Promise<AuthorizationReply> {
  if (!AUTHORIZATION_ID.test(authorizationId)) {
    throw new Error("authorization_rejected");
  }
  const response = await fetch(`${AUTHORIZATIONS_PATH}/${authorizationId}`, {
    method: "GET",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  return parseReply(response);
}

async function postAuthorization(
  path: string,
  body: { approval_id: string; idempotency_key: string } | { idempotency_key: string },
): Promise<AuthorizationReply> {
  const response = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseReply(response);
}

async function parseReply(response: Response): Promise<AuthorizationReply> {
  const payload: unknown = await response.json();
  if (!isAuthorizationReply(payload) || !replyHttpOk(response.status, payload)) {
    throw new Error("authorization_rejected");
  }
  return payload;
}

async function parseList(response: Response): Promise<AuthorizationListReply> {
  const payload: unknown = await response.json();
  if (!isAuthorizationListReply(payload) || !listHttpOk(response.status, payload)) {
    throw new Error("authorization_rejected");
  }
  return payload;
}

const EMPTY_REPLY: Record<string, { http: number; text: string }> = {
  session_absent: {
    http: 409,
    text: "The server session is not available. No scientific action was taken.",
  },
  request_incomplete: {
    http: 409,
    text: "The design request is not complete. No scientific action was taken.",
  },
  approval_absent: {
    http: 409,
    text: "The approval is not available. No scientific action was taken.",
  },
  approval_not_approved: {
    http: 409,
    text: "The approval is not approved. No scientific action was taken.",
  },
  revision_conflict: {
    http: 409,
    text: "The approval does not match the current revision. No scientific action was taken.",
  },
  digest_mismatch: {
    http: 409,
    text: "The approval does not match the current request. No scientific action was taken.",
  },
  budget_invalid: {
    http: 409,
    text: "The execution budget is not valid. No scientific action was taken.",
  },
  demonstration_absent: {
    http: 409,
    text: "The fixture demonstration is not recorded for this approval. No scientific action was taken.",
  },
  fixture_run_required: {
    http: 409,
    text: "A completed fixture run is not recorded for this approval. No scientific action was taken.",
  },
  profile_not_allowlisted: {
    http: 409,
    text: "This request is not the fixture-only profile. No scientific action was taken.",
  },
  readiness_not_ready: {
    http: 409,
    text: "Scientific runner readiness is not ready. No scientific action was taken.",
  },
  readiness_invalid: {
    http: 409,
    text: "The readiness snapshot was rejected. No scientific action was taken.",
  },
  target_blocked: {
    http: 409,
    text: "CD22 and TACI remain blocked. No scientific action was taken.",
  },
  authorization_rejected: {
    http: 409,
    text: "Scientific authorization was rejected. No scientific action was taken.",
  },
  authorization_absent: { http: 404, text: ABSENT_TEXT },
  invalid_payload: { http: 400, text: REJECTED_TEXT },
  adapter_failure: { http: 200, text: ADAPTER_TEXT },
  authorization_record_invalid: { http: 409, text: UNUSABLE_TEXT },
};

const EMPTY_LIST: Record<string, { http: number; text: string }> = {
  adapter_failure: { http: 200, text: ADAPTER_TEXT },
  invalid_payload: { http: 400, text: REJECTED_TEXT },
  authorization_record_invalid: { http: 409, text: UNUSABLE_TEXT },
};

function replyHttpOk(http: number, reply: AuthorizationReply): boolean {
  if (reply.authorization === null) {
    const expected = EMPTY_REPLY[reply.status];
    return expected !== undefined && http === expected.http && reply.text === expected.text;
  }
  if (reply.status === "authorization_created") {
    return (
      http === 201 &&
      reply.text === AUTHORIZED_TEXT &&
      reply.authorization.status === "authorized" &&
      reply.authorization.execution_authorized === true
    );
  }
  if (reply.status === "authorization_replayed") {
    const consumed = reply.authorization.execution_authorized === false;
    return (
      http === 200 &&
      reply.authorization.status === "authorized" &&
      reply.text === reply.authorization.text &&
      reply.text === (consumed ? CONSUMED_TEXT : AUTHORIZED_TEXT)
    );
  }
  if (reply.status === "authorization_revoked") {
    return (
      http === 200 &&
      reply.text === REVOKED_TEXT &&
      reply.authorization.status === "revoked" &&
      reply.authorization.execution_authorized === false
    );
  }
  if (reply.status === "authorization_observed") {
    return http === 200 && reply.text === reply.authorization.text;
  }
  return reply.status === "idempotency_conflict" && http === 409 && reply.text === CONFLICT_TEXT;
}

function listHttpOk(http: number, reply: AuthorizationListReply): boolean {
  if (reply.status === "authorizations_observed") {
    return http === 200 && reply.text === HISTORY_TEXT;
  }
  const expected = EMPTY_LIST[reply.status];
  return (
    expected !== undefined &&
    http === expected.http &&
    reply.text === expected.text &&
    reply.authorizations.length === 0
  );
}

function isAuthorizationReply(value: unknown): value is AuthorizationReply {
  if (!record(value) || !sameKeys(value, REPLY_KEYS)) {
    return false;
  }
  if (!safeText(value.text) || !token(value.session_id) || typeof value.status !== "string") {
    return false;
  }
  if (
    value.scientific_execution !== false ||
    value.scientific_validation !== false ||
    value.scientific_candidate_generated !== false ||
    typeof value.execution_authorized !== "boolean"
  ) {
    return false;
  }
  if (value.authorization === null) {
    return value.execution_authorized === false && value.status in EMPTY_REPLY;
  }
  if (!isAuthorizationRecord(value.authorization)) {
    return false;
  }
  return (
    value.execution_authorized === value.authorization.execution_authorized &&
    value.authorization.session_id === value.session_id
  );
}

function isAuthorizationListReply(value: unknown): value is AuthorizationListReply {
  if (!record(value) || !sameKeys(value, LIST_KEYS)) {
    return false;
  }
  if (
    value.execution_authorized !== false ||
    value.scientific_execution !== false ||
    value.scientific_validation !== false ||
    value.scientific_candidate_generated !== false ||
    typeof value.status !== "string" ||
    !safeText(value.text) ||
    !token(value.session_id) ||
    !Array.isArray(value.authorizations) ||
    value.authorizations.length > 50
  ) {
    return false;
  }
  return (
    (value.status === "authorizations_observed" || value.status in EMPTY_LIST) &&
    value.authorizations.every(
      (item) => isAuthorizationRecord(item) && item.session_id === value.session_id,
    )
  );
}

function isAuthorizationRecord(value: unknown): value is AuthorizationRecord {
  if (!record(value) || !sameKeys(value, RECORD_KEYS)) {
    return false;
  }
  if (
    !matches(value.authorization_id, AUTHORIZATION_ID) ||
    !token(value.session_id) ||
    !matches(value.request_id, KEY) ||
    !matches(value.approval_id, KEY) ||
    typeof value.request_revision !== "number" ||
    !Number.isInteger(value.request_revision) ||
    value.request_revision < 1 ||
    !matches(value.request_digest, DIGEST) ||
    !matches(value.readiness_observation_id, OBSERVATION_ID) ||
    instant(value.readiness_observed_at) === null ||
    instant(value.created_at) === null ||
    instant(value.updated_at) === null
  ) {
    return false;
  }
  if (
    (value.profile_id !== "pdl1-bindcraft-historical-v1" &&
      value.profile_id !== "pdl1-bindcraft-docker-v1") ||
    value.tool_name !== "bindcraft" ||
    value.target_scope !== "PDL1" ||
    !version(value.tool_version) ||
    !version(value.runtime_name) ||
    !version(value.runtime_version) ||
    !matches(value.image_digest, IMAGE) ||
    !matches(value.manifest_digest, DIGEST)
  ) {
    return false;
  }
  if (value.status !== "authorized" && value.status !== "revoked") {
    return false;
  }
  if (
    value.scientific_execution !== false ||
    value.scientific_validation !== false ||
    value.scientific_candidate_generated !== false ||
    typeof value.execution_authorized !== "boolean" ||
    value.single_use !== SINGLE_USE_TEXT ||
    value.validation_text !== VALIDATION_TEXT
  ) {
    return false;
  }
  if (value.status === "revoked") {
    if (value.execution_authorized !== false || value.text !== REVOKED_TEXT) {
      return false;
    }
  } else if (value.execution_authorized === true) {
    if (value.text !== AUTHORIZED_TEXT) {
      return false;
    }
  } else if (value.text !== CONSUMED_TEXT) {
    return false;
  }
  if (!isBudget(value.budget) || !historyOk(value)) {
    return false;
  }
  return true;
}

function isBudget(value: unknown): value is AuthorizationBudget {
  if (!record(value) || !sameKeys(value, BUDGET_KEYS)) {
    return false;
  }
  if (value.authorizes_execution !== false || typeof value.human_override_required !== "boolean") {
    return false;
  }
  if (value.action_on_limit !== "warn" && value.action_on_limit !== "stop") {
    return false;
  }
  if (!optionalCeiling(value.token_ceiling) || !optionalCeiling(value.model_cost_ceiling)) {
    return false;
  }
  if (!optionalCeiling(value.scientific_tool_cost_ceiling)) {
    return false;
  }
  if (value.warning_threshold !== null) {
    if (
      typeof value.warning_threshold !== "number" ||
      !Number.isFinite(value.warning_threshold) ||
      value.warning_threshold < 0 ||
      value.warning_threshold > 1
    ) {
      return false;
    }
  }
  return value.currency === null || (typeof value.currency === "string" && safeText(value.currency));
}

function isEvent(value: unknown): value is AuthorizationEvent {
  if (!record(value) || !sameKeys(value, EVENT_KEYS)) {
    return false;
  }
  const expected = value.status === "authorized" ? AUTHORIZED_TEXT : REVOKED_TEXT;
  return (
    typeof value.sequence === "number" &&
    Number.isInteger(value.sequence) &&
    value.sequence >= 1 &&
    (value.status === "authorized" || value.status === "revoked") &&
    instant(value.recorded_at) !== null &&
    value.origin === "server" &&
    value.text === expected
  );
}

function historyOk(value: Record<string, unknown>): boolean {
  if (!Array.isArray(value.events) || value.events.length === 0) {
    return false;
  }
  const events: AuthorizationEvent[] = [];
  for (const [index, event] of value.events.entries()) {
    if (!isEvent(event) || event.sequence !== index + 1) {
      return false;
    }
    events.push(event);
  }
  const created = instant(value.created_at);
  const updated = instant(value.updated_at);
  const first = events[0];
  const last = events[events.length - 1];
  if (created === null || updated === null || created > updated || !first || !last) {
    return false;
  }
  if (first.status !== "authorized" || first.recorded_at !== value.created_at) {
    return false;
  }
  for (let index = 1; index < events.length; index += 1) {
    const previous = instant(events[index - 1]?.recorded_at);
    const next = instant(events[index]?.recorded_at);
    if (previous === null || next === null || next <= previous) {
      return false;
    }
  }
  if (last.recorded_at !== value.updated_at || last.status !== value.status) {
    return false;
  }
  if (value.status === "authorized") {
    return events.length === 1 && value.updated_at === value.created_at;
  }
  return events.length === 2 && events[1]?.status === "revoked" && updated > created;
}

function sameBudget(left: AuthorizationBudget, right: AuthorizationBudget): boolean {
  return (
    left.action_on_limit === right.action_on_limit &&
    left.authorizes_execution === right.authorizes_execution &&
    left.currency === right.currency &&
    left.human_override_required === right.human_override_required &&
    left.model_cost_ceiling === right.model_cost_ceiling &&
    left.scientific_tool_cost_ceiling === right.scientific_tool_cost_ceiling &&
    left.token_ceiling === right.token_ceiling &&
    left.warning_threshold === right.warning_threshold
  );
}

function instant(value: unknown): number | null {
  return typeof value === "string" ? observationInstant(value) : null;
}

function matches(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.test(value) && safeText(value);
}

function optionalCeiling(value: unknown): boolean {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function version(value: unknown): boolean {
  return (
    typeof value === "string" &&
    TOKEN.test(value) &&
    !REJECTED_VERSIONS.has(value.toLowerCase()) &&
    safeText(value)
  );
}

function token(value: unknown): value is string {
  return typeof value === "string" && TOKEN.test(value) && safeText(value);
}

function safeText(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 500 &&
    !/[\n\r\u0000/\\`]/.test(value) &&
    !UNSAFE.test(value)
  );
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sameKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).sort().join() === [...keys].sort().join();
}
