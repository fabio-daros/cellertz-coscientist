export const APPROVED_RUNS_PATH = "/api/approved-runs";

export type OperationalArtifact = {
  artifact_id: string;
  kind: string;
  content_sha256: string;
  size_bytes: number;
  media_type: string;
  label: string;
  fixture: true;
  scientific_evidence: false;
  scientific_candidate: false;
};

export type RunEvent = {
  sequence: number;
  status: string;
  recorded_at: string;
  attempt: number;
  text: string;
  origin: string;
};

export type ApprovedRunRecord = {
  run_id: string;
  session_id: string;
  approval_id: string;
  request_id: string;
  request_revision: number;
  request_digest: string;
  execution_profile: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  attempt: number;
  created_at: string;
  updated_at: string;
  events: RunEvent[];
  artifacts: OperationalArtifact[];
  budget: {
    token_ceiling: number | null;
    model_cost_ceiling: number | null;
    scientific_tool_cost_ceiling: number | null;
    authorizes_execution: false;
  };
  usage: {
    estimated_model_cost: number | null;
    scientific_infrastructure_cost: number | null;
    currency: string | null;
    observed_model_calls: number;
    observed_scientific_tool_calls: number;
    observed_model_cost: number;
    observed_scientific_tool_cost: number;
    observed_costs_known: boolean;
    authorizes_execution: false;
  };
  fixture: true;
  scientific_validation: false;
  scientific_execution: false;
  label: string;
  text: string;
};

export type ApprovedRunReply = {
  status: string;
  text: string;
  session_id: string;
  execution_authorized: false;
  executable: false;
  scientific_validation: false;
  scientific_candidate: false;
  candidates: [];
  scientific_evidence: [];
  scientific_memory_written: false;
  semantic_promotion: false;
  run: ApprovedRunRecord | null;
};

export type ApprovedRunSummary = {
  run_id: string;
  request_id: string;
  approval_id: string;
  revision: number;
  status: string;
  attempt: number;
  created_at: string;
  updated_at: string;
  text: string;
  artifact_count: number;
  fixture: true;
  scientific_validation: false;
  label: string;
};

export type ApprovedRunListReply = {
  status: string;
  text: string;
  session_id: string;
  execution_authorized: false;
  executable: false;
  scientific_validation: false;
  scientific_candidate: false;
  candidates: [];
  scientific_evidence: [];
  scientific_memory_written: false;
  semantic_promotion: false;
  label: string;
  runs: ApprovedRunSummary[];
};

export type ArtifactSelection = {
  run_id: string;
  artifact_id: string;
  attempt: number;
};

export type ArtifactView = ArtifactSelection & {
  text: string;
};

const ARTIFACT_MEDIA_TYPES = ["application/json", "text/plain", "text/markdown"];
const ARTIFACT_FILENAME = /^fixture-[a-z_]+\.(?:json|txt|md)$/;

export function selectRunHistory(
  current: ApprovedRunListReply | null,
  incoming: ApprovedRunListReply,
  operation: number,
  latestOperation: number,
): ApprovedRunListReply | null {
  if (operation !== latestOperation) {
    return current;
  }
  if (current?.runs.length) {
    const newest = current.runs[0]?.updated_at ?? "";
    const incomingNewest = incoming.runs[0]?.updated_at ?? "";
    if (incomingNewest < newest) {
      return current;
    }
    for (const run of incoming.runs) {
      const visible = current.runs.find((item) => item.run_id === run.run_id);
      if (visible && (run.attempt < visible.attempt || run.updated_at < visible.updated_at)) {
        return current;
      }
    }
  }
  return incoming;
}

export function selectArtifactView(
  current: ArtifactView | null,
  incoming: ArtifactView,
  operation: number,
  latestOperation: number,
  selection: ArtifactSelection | null,
): ArtifactView | null {
  if (operation !== latestOperation || selection === null) {
    return current;
  }
  if (
    incoming.run_id !== selection.run_id ||
    incoming.artifact_id !== selection.artifact_id ||
    incoming.attempt !== selection.attempt
  ) {
    return current;
  }
  return incoming;
}

export function selectRunReply(
  current: ApprovedRunReply | null,
  incoming: ApprovedRunReply,
  operation: number,
  latestOperation: number,
): ApprovedRunReply | null {
  if (operation !== latestOperation) {
    return current;
  }
  if (
    current?.run &&
    incoming.run &&
    incoming.run.run_id === current.run.run_id &&
    incoming.run.attempt < current.run.attempt
  ) {
    return current;
  }
  if (
    current?.run &&
    incoming.run &&
    incoming.run.run_id === current.run.run_id &&
    incoming.run.attempt === current.run.attempt &&
    incoming.run.events.length < current.run.events.length
  ) {
    return current;
  }
  return incoming;
}

export function failedRunReply(sessionId: string): ApprovedRunReply {
  return {
    status: "adapter_failure",
    text: "The fixture run adapter failed. No scientific action was taken.",
    session_id: sessionId,
    execution_authorized: false,
    executable: false,
    scientific_validation: false,
    scientific_candidate: false,
    candidates: [],
    scientific_evidence: [],
    scientific_memory_written: false,
    semantic_promotion: false,
    run: null,
  };
}

async function parseRun(response: Response, sessionId: string): Promise<ApprovedRunReply> {
  if (!response.ok && response.status !== 409) {
    return failedRunReply(sessionId);
  }
  const payload = (await response.json()) as ApprovedRunReply;
  if (!payload || typeof payload.status !== "string" || typeof payload.text !== "string") {
    return failedRunReply(sessionId);
  }
  return payload;
}

export function createApprovedRun(input: {
  session_id: string;
  approval_id: string;
  idempotency_key: string;
}): Promise<ApprovedRunReply> {
  return fetch(APPROVED_RUNS_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: input.session_id,
      approval_id: input.approval_id,
      idempotency_key: input.idempotency_key,
    }),
  }).then((response) => parseRun(response, input.session_id));
}

export function readApprovedRun(sessionId: string, runId: string): Promise<ApprovedRunReply> {
  const query = new URLSearchParams({ session_id: sessionId });
  return fetch(`${APPROVED_RUNS_PATH}/${runId}?${query.toString()}`, { method: "GET" }).then((response) =>
    parseRun(response, sessionId),
  );
}

function failedHistory(sessionId = "unknown"): ApprovedRunListReply {
  return {
    status: "adapter_failure",
    text: "The fixture run history could not be read.",
    session_id: sessionId,
    execution_authorized: false,
    executable: false,
    scientific_validation: false,
    scientific_candidate: false,
    candidates: [],
    scientific_evidence: [],
    scientific_memory_written: false,
    semantic_promotion: false,
    label: "fixture-only run",
    runs: [],
  };
}

export function listApprovedRuns(): Promise<ApprovedRunListReply> {
  return fetch(APPROVED_RUNS_PATH, { method: "GET" }).then(async (response) => {
    if (!response.ok) {
      return failedHistory();
    }
    const payload = (await response.json()) as ApprovedRunListReply;
    if (!payload || typeof payload.text !== "string" || !Array.isArray(payload.runs)) {
      return failedHistory();
    }
    return payload;
  });
}

const ARTIFACT_BYTE_LIMIT = 65536;
const ARTIFACT_LENGTH = /^(0|[1-9][0-9]*)$/;
const ARTIFACT_UNREADABLE = "The operational artifact could not be read.";

export async function readApprovedArtifact(
  runId: string,
  artifactId: string,
): Promise<{ ok: true; text: string; mediaType: string; filename: string } | { ok: false; text: string }> {
  const response = await fetch(`${APPROVED_RUNS_PATH}/${runId}/artifacts/${artifactId}`, { method: "GET" });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { text?: string } | null;
    return { ok: false, text: payload?.text || ARTIFACT_UNREADABLE };
  }
  const mediaType = response.headers.get("content-type") ?? "";
  if (!ARTIFACT_MEDIA_TYPES.some((allowed) => mediaType === allowed)) {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]*)"/.exec(disposition)?.[1] ?? "";
  if (!ARTIFACT_FILENAME.test(filename)) {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  const declared = response.headers.get("content-length") ?? "";
  if (!ARTIFACT_LENGTH.test(declared)) {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  const size = Number(declared);
  if (!Number.isSafeInteger(size) || size > ARTIFACT_BYTE_LIMIT) {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  const buffer = new Uint8Array(await response.arrayBuffer());
  if (buffer.byteLength !== size) {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  if (new TextEncoder().encode(text).byteLength !== size) {
    return { ok: false, text: ARTIFACT_UNREADABLE };
  }
  return { ok: true, text, mediaType, filename };
}

export function retryApprovedRun(input: {
  session_id: string;
  run_id: string;
  idempotency_key: string;
}): Promise<ApprovedRunReply> {
  return fetch(`${APPROVED_RUNS_PATH}/${input.run_id}/retry`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: input.session_id,
      idempotency_key: input.idempotency_key,
    }),
  }).then((response) => parseRun(response, input.session_id));
}
