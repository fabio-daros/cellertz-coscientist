import { observationInstant } from "./scientific-runner-readiness";

export const RUNS_PATH = "/api/scientific-runs";
export const QUEUED_TEXT = "Scientific run queued.";
export const RETRY_QUEUED_TEXT = "Scientific retry queued.";
export const RUNNING_TEXT = "Scientific run started.";
export const COMPLETED_TEXT = "Scientific run completed. Not scientific validation.";
export const FAILED_TEXT = "The scientific run failed. No scientific action was taken.";
export const INTERRUPTED_TEXT =
  "The scientific run was interrupted before completion. No scientific action was taken.";
export const DISABLED_TEXT = "Scientific execution is disabled. No scientific action was taken.";
export const HISTORY_TEXT = "Scientific runs. Not scientific validation.";
export const KEY_TEXT = "The idempotency key does not match this scientific run.";
export const RETRY_TEXT = "Retry is not available for this scientific run.";
export const UNAVAILABLE_TEXT =
  "The scientific authorization is not available for execution. No scientific action was taken.";
export const REJECTED_TEXT = "The scientific run payload was rejected. No scientific action was taken.";
export const UNUSABLE_TEXT = "The scientific run record could not be used. No scientific action was taken.";
export const ADAPTER_TEXT = "The scientific run adapter failed. No scientific action was taken.";
export const ABSENT_TEXT = "No scientific run matches this request.";
export const ARTIFACT_TEXT = "The scientific run artifact is not available.";

const RUN_KEYS = [
  "accepted",
  "approval_id",
  "artifacts",
  "attempt",
  "authorization_id",
  "budget",
  "created_at",
  "events",
  "image_digest",
  "manifest_digest",
  "profile_id",
  "ranked",
  "readiness_observation_id",
  "readiness_observed_at",
  "request_digest",
  "request_id",
  "request_revision",
  "run_id",
  "runtime_name",
  "runtime_version",
  "scientific_candidate_generated",
  "scientific_execution",
  "scientific_validation",
  "session_id",
  "status",
  "target_scope",
  "text",
  "tool_name",
  "tool_version",
  "updated_at",
] as const;

const REPLY_KEYS = [
  "accepted",
  "ranked",
  "run",
  "scientific_candidate_generated",
  "scientific_execution",
  "scientific_validation",
  "session_id",
  "status",
  "text",
] as const;

const LIST_KEYS = [
  "accepted",
  "ranked",
  "runs",
  "scientific_candidate_generated",
  "scientific_execution",
  "scientific_validation",
  "session_id",
  "status",
  "text",
] as const;

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

const EVENT_KEYS = ["attempt", "origin", "recorded_at", "sequence", "status", "text"] as const;
const ARTIFACT_KEYS = [
  "artifact_id",
  "attempt",
  "kind",
  "media_type",
  "run_id",
  "session_id",
  "sha256",
  "size",
] as const;

export const EXTERNAL_FAILED_TEXT =
  "The scientific run failed after an external launch. The outcome is not scientific validation.";
export const EXTERNAL_UNCERTAIN_TEXT =
  "The scientific run was interrupted after an external launch. The external outcome is unknown. No new launch was started.";
const PDL1_PROFILES = new Set(["pdl1-bindcraft-historical-v1", "pdl1-bindcraft-docker-v1"]);
const OBSERVED_TEXTS = new Set([
  QUEUED_TEXT,
  RETRY_QUEUED_TEXT,
  RUNNING_TEXT,
  COMPLETED_TEXT,
  FAILED_TEXT,
  INTERRUPTED_TEXT,
  DISABLED_TEXT,
  EXTERNAL_FAILED_TEXT,
  EXTERNAL_UNCERTAIN_TEXT,
]);
const REPLY_HTTP: Record<string, number> = {
  run_queued: 202,
  run_observed: 200,
  adapter_failure: 200,
  invalid_payload: 400,
  run_absent: 404,
  artifact_absent: 404,
  artifact_attempt_unavailable: 404,
  idempotency_conflict: 409,
  retry_not_allowed: 409,
  authorization_unavailable: 409,
  run_record_invalid: 409,
  artifact_digest_mismatch: 409,
  artifact_invalid: 409,
  artifact_media_type_rejected: 409,
  artifact_size_mismatch: 409,
  artifact_too_large: 409,
};
const RUNLESS_TEXT: Record<string, string> = {
  authorization_unavailable: UNAVAILABLE_TEXT,
  invalid_payload: REJECTED_TEXT,
  artifact_absent: ARTIFACT_TEXT,
  artifact_attempt_unavailable: ARTIFACT_TEXT,
  run_record_invalid: UNUSABLE_TEXT,
  adapter_failure: ADAPTER_TEXT,
  artifact_digest_mismatch: ARTIFACT_TEXT,
  artifact_invalid: ARTIFACT_TEXT,
  artifact_media_type_rejected: ARTIFACT_TEXT,
  artifact_size_mismatch: ARTIFACT_TEXT,
  artifact_too_large: ARTIFACT_TEXT,
};
const WITH_RUN = new Set(["run_queued", "run_observed", "idempotency_conflict", "retry_not_allowed"]);

const TEXTS = new Set([
  QUEUED_TEXT,
  RETRY_QUEUED_TEXT,
  RUNNING_TEXT,
  COMPLETED_TEXT,
  FAILED_TEXT,
  INTERRUPTED_TEXT,
  DISABLED_TEXT,
  EXTERNAL_FAILED_TEXT,
  EXTERNAL_UNCERTAIN_TEXT,
]);
const STATUSES = new Set(["queued", "running", "completed", "failed"]);
const KINDS = new Set(["execution_record", "execution_note", "candidate_record", "workflow_trace"]);
const MEDIA = new Set(["application/json", "text/plain", "text/markdown"]);
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/;
const REJECTED_VERSIONS = new Set(["latest", "current", "unknown"]);
const RUN_ID = /^run_[0-9a-f]{32}$/;
const AUTHORIZATION_ID = /^authorization_[0-9a-f]{32}$/;
const OBSERVATION_ID = /^observation_[0-9a-f]{64}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const IMAGE = /^sha256:[0-9a-f]{64}$/;
const KEY = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$/;
const UNSAFE = /traceback|\/users\/|\/tmp\/|token=|sqlite|select\s|shell=true/i;
const RANK: Record<ScientificRunStatus, number> = {
  queued: 0,
  running: 1,
  completed: 2,
  failed: 2,
};

export type ScientificRunStatus = "queued" | "running" | "completed" | "failed";

export type ScientificRunBudget = {
  action_on_limit: "warn" | "stop";
  authorizes_execution: false;
  currency: string | null;
  human_override_required: boolean;
  model_cost_ceiling: number | null;
  scientific_tool_cost_ceiling: number | null;
  token_ceiling: number | null;
  warning_threshold: number | null;
};

export type ScientificRunEvent = {
  attempt: number;
  origin: "server";
  recorded_at: string;
  sequence: number;
  status: ScientificRunStatus;
  text: string;
};

export type ScientificArtifact = {
  artifact_id: string;
  attempt: number;
  kind: "execution_record" | "execution_note" | "candidate_record" | "workflow_trace";
  media_type: "application/json" | "text/plain" | "text/markdown";
  run_id: string;
  session_id: string;
  sha256: string;
  size: number;
};

export type ScientificRun = {
  accepted: false;
  approval_id: string;
  artifacts: ScientificArtifact[];
  attempt: number;
  authorization_id: string;
  budget: ScientificRunBudget;
  created_at: string;
  events: ScientificRunEvent[];
  image_digest: string;
  manifest_digest: string;
  profile_id: "pdl1-bindcraft-historical-v1" | "pdl1-bindcraft-docker-v1";
  ranked: false;
  readiness_observation_id: string;
  readiness_observed_at: string;
  request_digest: string;
  request_id: string;
  request_revision: number;
  run_id: string;
  runtime_name: string;
  runtime_version: string;
  scientific_candidate_generated: boolean;
  scientific_execution: boolean;
  scientific_validation: false;
  session_id: string;
  status: ScientificRunStatus;
  target_scope: "PDL1";
  text: string;
  tool_name: "bindcraft";
  tool_version: string;
  updated_at: string;
};

export type ScientificRunReply = {
  accepted: false;
  ranked: false;
  run: ScientificRun | null;
  scientific_candidate_generated: boolean;
  scientific_execution: boolean;
  scientific_validation: false;
  session_id: string;
  status: string;
  text: string;
};

export type ScientificRunList = {
  accepted: false;
  ranked: false;
  runs: ScientificRun[];
  scientific_candidate_generated: false;
  scientific_execution: false;
  scientific_validation: false;
  session_id: string;
  status: "runs_observed";
  text: string;
};

export function createScientificRun(
  authorizationId: string,
  idempotencyKey: string,
): Promise<ScientificRunReply> {
  if (!AUTHORIZATION_ID.test(authorizationId) || !KEY.test(idempotencyKey)) {
    return Promise.reject(new Error("rejected"));
  }
  return send("POST", RUNS_PATH, {
    authorization_id: authorizationId,
    idempotency_key: idempotencyKey,
  });
}

export function readScientificRun(runId: string): Promise<ScientificRunReply> {
  if (!RUN_ID.test(runId)) {
    return Promise.reject(new Error("rejected"));
  }
  return send("GET", `${RUNS_PATH}/${runId}`);
}

export function listScientificRuns(): Promise<ScientificRunList> {
  return fetch(RUNS_PATH, { method: "GET" }).then(async (response) => {
    const payload = await response.json();
    const list = acceptList(payload);
    if (!response.ok || !list) {
      throw new Error("rejected");
    }
    return list;
  });
}

export function retryScientificRun(runId: string, idempotencyKey: string): Promise<ScientificRunReply> {
  if (!RUN_ID.test(runId) || !KEY.test(idempotencyKey)) {
    return Promise.reject(new Error("rejected"));
  }
  return send("POST", `${RUNS_PATH}/${runId}/retry`, { idempotency_key: idempotencyKey });
}

export function selectScientificRun(
  current: ScientificRun | null,
  incoming: ScientificRun | null,
  operation: number,
  latestOperation: number,
  binding: { authorizationId: string; revision: number },
): ScientificRun | null {
  if (operation !== latestOperation || !incoming) {
    return current;
  }
  if (
    incoming.authorization_id !== binding.authorizationId ||
    incoming.request_revision !== binding.revision
  ) {
    return current;
  }
  if (!current || current.run_id !== incoming.run_id) {
    return current ? current : incoming;
  }
  if (incoming.attempt < current.attempt) {
    return current;
  }
  if (incoming.attempt === current.attempt) {
    const incomingInstant = observationInstant(incoming.updated_at);
    const currentInstant = observationInstant(current.updated_at);
    if (incomingInstant === null || currentInstant === null || incomingInstant < currentInstant) {
      return current;
    }
    if (RANK[incoming.status] < RANK[current.status]) {
      return current;
    }
  }
  return incoming;
}

async function send(
  method: "GET" | "POST",
  path: string,
  body?: { authorization_id: string; idempotency_key: string } | { idempotency_key: string },
): Promise<ScientificRunReply> {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  const reply = acceptReply(payload, response.status);
  if (!reply) {
    throw new Error("rejected");
  }
  return reply;
}

function acceptReply(payload: unknown, httpStatus: number): ScientificRunReply | null {
  if (!record(payload) || !sameKeys(payload, REPLY_KEYS)) {
    return null;
  }
  if (
    payload.accepted !== false ||
    payload.ranked !== false ||
    payload.scientific_validation !== false ||
    typeof payload.scientific_execution !== "boolean" ||
    typeof payload.scientific_candidate_generated !== "boolean" ||
    typeof payload.session_id !== "string" ||
    !TOKEN.test(payload.session_id) ||
    typeof payload.status !== "string" ||
    typeof payload.text !== "string" ||
    UNSAFE.test(payload.text) ||
    REPLY_HTTP[payload.status] !== httpStatus
  ) {
    return null;
  }
  if (payload.status in RUNLESS_TEXT || payload.status === "run_absent") {
    const allowed = payload.status === "run_absent" ? payload.text === ABSENT_TEXT || payload.text === ARTIFACT_TEXT : payload.text === RUNLESS_TEXT[payload.status];
    if (
      !allowed ||
      payload.run !== null ||
      payload.scientific_execution !== false ||
      payload.scientific_candidate_generated !== false
    ) {
      return null;
    }
    return payload as ScientificRunReply;
  }
  if (!WITH_RUN.has(payload.status) || payload.run === null) {
    return null;
  }
  const run = acceptRun(payload.run);
  if (!run || run.session_id !== payload.session_id) {
    return null;
  }
  if (
    payload.scientific_execution !== run.scientific_execution ||
    payload.scientific_candidate_generated !== run.scientific_candidate_generated
  ) {
    return null;
  }
  if (payload.status === "run_queued" && (run.status !== "queued" || payload.text !== run.text || !queuedText(run))) {
    return null;
  }
  if (payload.status === "run_observed" && (payload.text !== run.text || !OBSERVED_TEXTS.has(payload.text))) {
    return null;
  }
  if (payload.status === "idempotency_conflict" && payload.text !== KEY_TEXT) {
    return null;
  }
  if (payload.status === "retry_not_allowed" && payload.text !== RETRY_TEXT) {
    return null;
  }
  return { ...(payload as ScientificRunReply), run };
}

function queuedText(run: ScientificRun): boolean {
  return run.text === (run.attempt === 1 ? QUEUED_TEXT : RETRY_QUEUED_TEXT);
}

function acceptList(payload: unknown): ScientificRunList | null {
  if (!record(payload) || !sameKeys(payload, LIST_KEYS)) {
    return null;
  }
  if (
    payload.accepted !== false ||
    payload.ranked !== false ||
    payload.scientific_candidate_generated !== false ||
    payload.scientific_execution !== false ||
    payload.scientific_validation !== false ||
    payload.status !== "runs_observed" ||
    payload.text !== HISTORY_TEXT ||
    typeof payload.session_id !== "string" ||
    !TOKEN.test(payload.session_id) ||
    !Array.isArray(payload.runs)
  ) {
    return null;
  }
  const runs: ScientificRun[] = [];
  for (const item of payload.runs) {
    const run = acceptRun(item);
    if (!run || run.session_id !== payload.session_id) {
      return null;
    }
    runs.push(run);
  }
  return { ...(payload as ScientificRunList), runs };
}

function acceptRun(value: unknown): ScientificRun | null {
  if (!record(value) || !sameKeys(value, RUN_KEYS) || typeof value.status !== "string" || !STATUSES.has(value.status)) {
    return null;
  }
  const status = value.status as ScientificRunStatus;
  if (
    value.accepted !== false ||
    value.ranked !== false ||
    value.scientific_validation !== false ||
    !PDL1_PROFILES.has(String(value.profile_id)) ||
    value.tool_name !== "bindcraft" ||
    value.target_scope !== "PDL1" ||
    !RUN_ID.test(String(value.run_id)) ||
    !AUTHORIZATION_ID.test(String(value.authorization_id)) ||
    !OBSERVATION_ID.test(String(value.readiness_observation_id)) ||
    !DIGEST.test(String(value.request_digest)) ||
    !DIGEST.test(String(value.manifest_digest)) ||
    !IMAGE.test(String(value.image_digest)) ||
    !versionToken(value.tool_version) ||
    !versionToken(value.runtime_name) ||
    !versionToken(value.runtime_version) ||
    !TOKEN.test(String(value.request_id)) ||
    !TOKEN.test(String(value.approval_id)) ||
    typeof value.session_id !== "string" ||
    !TOKEN.test(value.session_id) ||
    !whole(value.attempt, 1) ||
    !whole(value.request_revision, 1) ||
    typeof value.text !== "string" ||
    !TEXTS.has(value.text) ||
    UNSAFE.test(JSON.stringify(value)) ||
    observationInstant(String(value.created_at)) === null ||
    observationInstant(String(value.updated_at)) === null ||
    observationInstant(String(value.readiness_observed_at)) === null
  ) {
    return null;
  }
  if (value.scientific_execution !== (status === "completed")) {
    return null;
  }
  if (!budget(value.budget) || !Array.isArray(value.events) || !Array.isArray(value.artifacts)) {
    return null;
  }
  const events = value.events.map(acceptEvent).filter((item): item is ScientificRunEvent => item !== null);
  const artifacts = value.artifacts
    .map((item) => acceptArtifact(item, value))
    .filter((item): item is ScientificArtifact => item !== null);
  if (events.length !== value.events.length || artifacts.length !== value.artifacts.length || !historyOk(events, status, value.attempt, value.text)) {
    return null;
  }
  const created = observationInstant(String(value.created_at));
  const updated = observationInstant(String(value.updated_at));
  const first = events[0];
  const last = events[events.length - 1];
  if (
    !first ||
    !last ||
    value.created_at !== first.recorded_at ||
    value.updated_at !== last.recorded_at ||
    created === null ||
    updated === null ||
    created > updated
  ) {
    return null;
  }
  const candidate = artifacts.some((item) => item.kind === "candidate_record");
  if (value.scientific_candidate_generated !== (status === "completed" && candidate)) {
    return null;
  }
  if (status === "completed") {
    const kinds = artifacts.map((item) => item.kind);
    if (kinds.filter((kind) => kind === "execution_record").length !== 1 || new Set(kinds).size !== kinds.length) {
      return null;
    }
  } else if (artifacts.length !== 0) {
    return null;
  }
  return { ...(value as ScientificRun), status, events, artifacts, budget: value.budget };
}

function historyOk(events: ScientificRunEvent[], status: ScientificRunStatus, attempt: number, text: string): boolean {
  const last = events[events.length - 1];
  const first = events[0];
  if (!last || !first || last.status !== status || last.attempt !== attempt || last.text !== text) {
    return false;
  }
  if (first.sequence !== 1 || first.status !== "queued" || first.attempt !== 1 || first.text !== QUEUED_TEXT) {
    return false;
  }
  let previous = first;
  for (const event of events.slice(1)) {
    const previousInstant = observationInstant(previous.recorded_at);
    const nextInstant = observationInstant(event.recorded_at);
    if (event.sequence !== previous.sequence + 1 || previousInstant === null || nextInstant === null || nextInstant < previousInstant) {
      return false;
    }
    if (event.attempt === previous.attempt + 1) {
      if (event.status !== "queued" || event.text !== RETRY_QUEUED_TEXT || previous.status !== "failed") {
        return false;
      }
    } else if (
      event.attempt !== previous.attempt ||
      event.status === "queued" ||
      (event.status === "running" && previous.status !== "queued") ||
      ((event.status === "completed" || event.status === "failed") && previous.status !== "running")
    ) {
      return false;
    }
    if (!eventText(event)) {
      return false;
    }
    previous = event;
  }
  return eventText(first);
}

function eventText(event: ScientificRunEvent): boolean {
  if (event.status === "queued") {
    return event.text === (event.attempt === 1 ? QUEUED_TEXT : RETRY_QUEUED_TEXT);
  }
  if (event.status === "running") {
    return event.text === RUNNING_TEXT;
  }
  if (event.status === "completed") {
    return event.text === COMPLETED_TEXT;
  }
  return (
    event.text === FAILED_TEXT ||
    event.text === INTERRUPTED_TEXT ||
    event.text === DISABLED_TEXT ||
    event.text === EXTERNAL_FAILED_TEXT ||
    event.text === EXTERNAL_UNCERTAIN_TEXT
  );
}

function acceptEvent(value: unknown): ScientificRunEvent | null {
  if (!record(value) || !sameKeys(value, EVENT_KEYS) || typeof value.status !== "string" || !STATUSES.has(value.status)) {
    return null;
  }
  if (
    value.origin !== "server" ||
    !whole(value.attempt, 1) ||
    !whole(value.sequence, 1) ||
    typeof value.text !== "string" ||
    !TEXTS.has(value.text) ||
    observationInstant(String(value.recorded_at)) === null
  ) {
    return null;
  }
  return value as ScientificRunEvent;
}

function acceptArtifact(value: unknown, run: Record<string, unknown>): ScientificArtifact | null {
  if (!record(value) || !sameKeys(value, ARTIFACT_KEYS) || typeof value.kind !== "string" || !KINDS.has(value.kind)) {
    return null;
  }
  const expected = `artifact_${String(run.run_id)}_${String(run.attempt)}_${value.kind}`;
  const jsonKind =
    value.kind === "execution_record" || value.kind === "candidate_record" || value.kind === "workflow_trace";
  if (
    value.run_id !== run.run_id ||
    value.session_id !== run.session_id ||
    value.attempt !== run.attempt ||
    value.artifact_id !== expected ||
    (jsonKind && value.media_type !== "application/json") ||
    (!jsonKind && value.media_type !== "text/plain" && value.media_type !== "text/markdown") ||
    !DIGEST.test(String(value.sha256)) ||
    !whole(value.size, 1) ||
    Number(value.size) > 65536
  ) {
    return null;
  }
  return value as ScientificArtifact;
}

function versionToken(value: unknown): boolean {
  return typeof value === "string" && TOKEN.test(value) && !REJECTED_VERSIONS.has(value.toLowerCase());
}

function whole(value: unknown, minimum: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum;
}

function budget(value: unknown): value is ScientificRunBudget {
  if (!record(value) || !sameKeys(value, BUDGET_KEYS)) {
    return false;
  }
  return (
    value.authorizes_execution === false &&
    typeof value.human_override_required === "boolean" &&
    (value.action_on_limit === "warn" || value.action_on_limit === "stop") &&
    (value.currency === null || currencyText(value.currency)) &&
    optionalNumber(value.model_cost_ceiling) &&
    optionalNumber(value.scientific_tool_cost_ceiling) &&
    (value.token_ceiling === null || whole(value.token_ceiling, 0)) &&
    (value.warning_threshold === null ||
      (typeof value.warning_threshold === "number" &&
        value.warning_threshold >= 0 &&
        value.warning_threshold <= 1))
  );
}

function currencyText(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 500 &&
    !/[\n\r\u0000/\\`]/.test(value) &&
    !UNSAFE.test(value)
  );
}

function optionalNumber(value: unknown): boolean {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function sameKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const present = Object.keys(value).sort();
  return present.length === keys.length && keys.every((key) => present.includes(key));
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export const WORKFLOW_STAGE_IDS = [
  "generation",
  "scoring",
  "ranking",
  "critique",
  "prioritization",
] as const;

export const WORKFLOW_SUMMARY =
  "Deterministic agent workflow fixture. Operational orchestration evidence. Not scientific validation. No scientific candidate was generated. No scientific action was taken.";

const WORKFLOW_SCHEMA = "cellertz.governed_agent_workflow.v1";
const WORKFLOW_PROFILE = "pdl1-governed-agent-workflow";
const WORKFLOW_VERSION = "v1";
export const WORKFLOW_VERSIONS: Record<(typeof WORKFLOW_STAGE_IDS)[number], string> = {
  generation: "protein_generation_agent_v1",
  scoring: "protein_scoring_tool_v2_fixture_metrics_v1",
  ranking: "protein_ranking_agent_v1",
  critique: "protein_critic_agent_v1",
  prioritization: "protein_prioritization_agent_v1",
};
const TRACE_KEYS = [
  "accepted",
  "attempt",
  "authorization_id",
  "binding_hash",
  "profile_id",
  "ranked",
  "run_id",
  "schema",
  "scientific_candidate_generated",
  "scientific_validation",
  "session_id",
  "stages",
  "target_scope",
  "workflow_profile",
  "workflow_version",
];
const STAGE_KEYS = [
  "attempt",
  "implementation_id",
  "implementation_version",
  "input_digest",
  "observed_at",
  "output_digest",
  "role",
  "run_id",
  "sequence",
  "stage_id",
  "status",
  "summary",
  "upstream_digest",
];
const BINDING_KEYS = [
  "approval_id",
  "budget",
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
  "session_id",
  "target_scope",
  "tool_name",
  "tool_version",
] as const;

export type WorkflowStage = {
  attempt: number;
  implementation_id: string;
  implementation_version: string;
  input_digest: string;
  observed_at: string;
  output_digest: string;
  role: string;
  run_id: string;
  sequence: number;
  stage_id: (typeof WORKFLOW_STAGE_IDS)[number];
  status: "completed";
  summary: string;
  upstream_digest: string;
};

export type WorkflowTrace = {
  accepted: false;
  attempt: number;
  authorization_id: string;
  binding_hash: string;
  profile_id: string;
  ranked: false;
  run_id: string;
  schema: typeof WORKFLOW_SCHEMA;
  scientific_candidate_generated: false;
  scientific_validation: false;
  session_id: string;
  stages: WorkflowStage[];
  target_scope: "PDL1";
  workflow_profile: typeof WORKFLOW_PROFILE;
  workflow_version: typeof WORKFLOW_VERSION;
};

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value) ?? "null";
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const entries = Object.keys(value).sort();
  return `{${entries
    .map((key) => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`)
    .join(",")}}`;
}

export async function digestHex(document: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalJson(document));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map((item) => item.toString(16).padStart(2, "0")).join("");
}

export async function parseWorkflowTrace(body: unknown, run: ScientificRun): Promise<WorkflowTrace | null> {
  if (!record(body) || !sameKeys(body, TRACE_KEYS) || !Array.isArray(body.stages)) {
    return null;
  }
  const expectedHash = await bindingDigest(run);
  const created = observationInstant(run.created_at);
  const updated = observationInstant(run.updated_at);
  if (
    body.schema !== WORKFLOW_SCHEMA ||
    body.workflow_profile !== WORKFLOW_PROFILE ||
    body.workflow_version !== WORKFLOW_VERSION ||
    body.run_id !== run.run_id ||
    body.session_id !== run.session_id ||
    body.authorization_id !== run.authorization_id ||
    body.attempt !== run.attempt ||
    !whole(body.attempt, 1) ||
    body.binding_hash !== expectedHash ||
    body.profile_id !== run.profile_id ||
    body.target_scope !== "PDL1" ||
    body.target_scope !== run.target_scope ||
    body.scientific_candidate_generated !== false ||
    body.scientific_validation !== false ||
    body.ranked !== false ||
    body.accepted !== false ||
    body.stages.length !== WORKFLOW_STAGE_IDS.length ||
    created === null ||
    updated === null
  ) {
    return null;
  }
  const expectedObservation = workflowObservedAt(run);
  if (expectedObservation === null) {
    return null;
  }
  let upstream = expectedHash;
  for (let index = 0; index < WORKFLOW_STAGE_IDS.length; index += 1) {
    const stage = body.stages[index];
    const stageId = WORKFLOW_STAGE_IDS[index];
    if (!record(stage) || !sameKeys(stage, STAGE_KEYS) || stage.stage_id !== stageId) {
      return null;
    }
    const version = WORKFLOW_VERSIONS[stageId];
    const input = {
      attempt: run.attempt,
      binding_hash: expectedHash,
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
    const seenAt = typeof stage.observed_at === "string" ? observationInstant(stage.observed_at) : null;
    if (
      stage.attempt !== run.attempt ||
      stage.run_id !== run.run_id ||
      stage.sequence !== index + 1 ||
      !whole(stage.sequence, 1) ||
      stage.role !== stageId ||
      stage.status !== "completed" ||
      stage.summary !== WORKFLOW_SUMMARY ||
      UNSAFE.test(WORKFLOW_SUMMARY) ||
      stage.implementation_id !== version ||
      stage.implementation_version !== version ||
      stage.upstream_digest !== upstream ||
      stage.input_digest !== output.input_digest ||
      stage.output_digest !== outputDigest ||
      seenAt === null ||
      stage.observed_at !== expectedObservation
    ) {
      return null;
    }
    upstream = outputDigest;
  }
  return body as WorkflowTrace;
}

export async function acceptWorkflowText(
  text: string,
  run: ScientificRun,
): Promise<WorkflowTrace | null> {
  if (text.length < 1 || text.length > 65536) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    if (canonicalJson(parsed) !== text) {
      return null;
    }
    return parseWorkflowTrace(parsed, run);
  } catch {
    return null;
  }
}

const WORKFLOW_FILENAME = "scientific-agent-workflow.json";
const ARTIFACT_LENGTH = /^(0|[1-9][0-9]*)$/;
const WORKFLOW_BYTE_CAP = 65536;

function exactEventSequence(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : null;
}

export function workflowObservedAt(run: ScientificRun): string | null {
  if (run.status !== "running" && run.status !== "completed") {
    return null;
  }
  const found: ScientificRunEvent[] = [];
  const queuedText = run.attempt === 1 ? QUEUED_TEXT : RETRY_QUEUED_TEXT;
  for (let index = 0; index < run.events.length; index += 1) {
    const event = run.events[index];
    if (event.status !== "running" || event.attempt !== run.attempt) {
      continue;
    }
    const previous = run.events[index - 1];
    const queuedSequence = exactEventSequence(previous?.sequence);
    const runningSequence = exactEventSequence(event.sequence);
    const previousInstant = previous ? observationInstant(previous.recorded_at) : null;
    const runningInstant = observationInstant(event.recorded_at);
    if (
      previous === undefined ||
      previous.origin !== "server" ||
      event.origin !== "server" ||
      previous.attempt !== run.attempt ||
      previous.status !== "queued" ||
      previous.text !== queuedText ||
      queuedSequence === null ||
      runningSequence === null ||
      runningSequence !== queuedSequence + 1 ||
      event.text !== RUNNING_TEXT ||
      previousInstant === null ||
      runningInstant === null ||
      runningInstant < previousInstant
    ) {
      return null;
    }
    found.push(event);
  }
  if (found.length !== 1) {
    return null;
  }
  const running = found[0];
  const last = run.events[run.events.length - 1];
  if (run.status === "running") {
    if (last !== running || run.updated_at !== running.recorded_at) {
      return null;
    }
    return running.recorded_at;
  }
  const completedSequence = exactEventSequence(last?.sequence);
  const runningSequence = exactEventSequence(running.sequence);
  const completedInstant = last ? observationInstant(last.recorded_at) : null;
  const runningInstant = observationInstant(running.recorded_at);
  if (
    last === undefined ||
    last.origin !== "server" ||
    last.status !== "completed" ||
    last.attempt !== run.attempt ||
    last.text !== COMPLETED_TEXT ||
    completedSequence === null ||
    runningSequence === null ||
    completedSequence !== runningSequence + 1 ||
    completedInstant === null ||
    runningInstant === null ||
    run.updated_at !== last.recorded_at ||
    run.events[run.events.length - 2] !== running ||
    completedInstant < runningInstant
  ) {
    return null;
  }
  return running.recorded_at;
}

async function readBoundedBody(body: ReadableStream<Uint8Array>, expected: number): Promise<Uint8Array | null> {
  if (expected < 1 || expected > WORKFLOW_BYTE_CAP) {
    return null;
  }
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const step = await reader.read();
      if (step.done) {
        break;
      }
      const bytes = step.value ?? new Uint8Array();
      if (bytes.byteLength === 0) {
        continue;
      }
      if (total + bytes.byteLength > expected || total + bytes.byteLength > WORKFLOW_BYTE_CAP) {
        await reader.cancel().catch(() => undefined);
        await body.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(bytes);
      total += bytes.byteLength;
    }
  } catch {
    await reader.cancel().catch(() => undefined);
    await body.cancel().catch(() => undefined);
    return null;
  }
  if (total !== expected) {
    return null;
  }
  const buffer = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    buffer.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return buffer;
}

export async function readWorkflowTrace(
  run: ScientificRun,
  artifactId: string,
): Promise<WorkflowTrace | null> {
  const expected = `artifact_${run.run_id}_${run.attempt}_workflow_trace`;
  const artifact = run.artifacts.find((item) => item.artifact_id === artifactId);
  if (
    artifactId !== expected ||
    artifact === undefined ||
    artifact.kind !== "workflow_trace" ||
    artifact.attempt !== run.attempt ||
    artifact.run_id !== run.run_id ||
    artifact.session_id !== run.session_id ||
    artifact.media_type !== "application/json" ||
    !DIGEST.test(artifact.sha256) ||
    !whole(artifact.size, 1) ||
    artifact.size > 65536
  ) {
    return null;
  }
  try {
    const response = await fetch(`${RUNS_PATH}/${run.run_id}/artifacts/${artifactId}`, { method: "GET" });
    if (!response.ok || response.headers.get("content-type") !== "application/json") {
      return null;
    }
    if (response.headers.get("content-disposition") !== `attachment; filename="${WORKFLOW_FILENAME}"`) {
      return null;
    }
    if (response.headers.get("x-content-type-options") !== "nosniff") {
      return null;
    }
    if (response.headers.get("cache-control") !== "private, no-store") {
      return null;
    }
    const declared = response.headers.get("content-length") ?? "";
    if (!ARTIFACT_LENGTH.test(declared)) {
      return null;
    }
    const declaredSize = Number(declared);
    if (declaredSize !== artifact.size || declaredSize > WORKFLOW_BYTE_CAP) {
      return null;
    }
    if (response.body === null) {
      return null;
    }
    const buffer = await readBoundedBody(response.body, artifact.size);
    if (buffer === null) {
      return null;
    }
    const digest = await crypto.subtle.digest("SHA-256", buffer as unknown as BufferSource);
    const sha256 = [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, "0")).join("");
    if (sha256 !== artifact.sha256) {
      return null;
    }
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
      return null;
    }
    if (new TextEncoder().encode(text).byteLength !== artifact.size) {
      return null;
    }
    return acceptWorkflowText(text, run);
  } catch {
    return null;
  }
}

export async function bindingDigest(run: ScientificRun): Promise<string> {
  const document: Record<string, unknown> = {};
  for (const key of BINDING_KEYS) {
    document[key] = run[key];
  }
  return digestHex(document);
}
