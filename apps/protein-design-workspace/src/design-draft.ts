import { type DesignDraftReply } from "./contracts";

export const DESIGN_DRAFT_PATH = "/api/design-draft";

export function failedDesignReply(sessionId: string): DesignDraftReply {
  return {
    status: "adapter_failure",
    text: "The conversational adapter failed. No scientific action was taken.",
    session_id: sessionId,
    execution_authorized: false,
    executable: false,
    changed_fields: [],
    questions: [],
    issues: [],
    draft: null,
    request: null,
    instrumentation: {
      interaction_id: "unknown",
      recorded: false,
      total_tokens: null,
      observation: "adapter_failure",
      event: null,
    },
  };
}

export type ListedDesignDraft = {
  message: string;
  requestId: string;
  revision: number;
};

/** Latest saved draft of each request. A missing route is an empty list, not a failure. */
export async function listDesignDrafts(): Promise<ListedDesignDraft[] | null> {
  let response: Response;
  try {
    response = await fetch(DESIGN_DRAFT_PATH, { method: "GET" });
  } catch {
    return null;
  }
  if (response.status === 404) {
    return [];
  }
  if (!response.ok) {
    return null;
  }
  try {
    const body = (await response.json()) as {
      execution_authorized?: unknown;
      executable?: unknown;
      drafts?: unknown;
    };
    if (body.execution_authorized !== false || body.executable !== false || !Array.isArray(body.drafts)) {
      return null;
    }
    const order: string[] = [];
    const byId = new Map<string, ListedDesignDraft>();
    for (const item of body.drafts) {
      const parsed = listedDraft(item);
      if (!parsed) {
        return null;
      }
      const existing = byId.get(parsed.requestId);
      if (!existing) {
        order.push(parsed.requestId);
        byId.set(parsed.requestId, parsed);
      } else if (parsed.revision > existing.revision) {
        byId.set(parsed.requestId, parsed);
      }
    }
    return order.flatMap((requestId) => {
      const item = byId.get(requestId);
      return item ? [item] : [];
    });
  } catch {
    return null;
  }
}

function listedDraft(item: unknown): ListedDesignDraft | null {
  if (!item || typeof item !== "object") {
    return null;
  }
  const record = item as {
    message?: unknown;
    execution_authorized?: unknown;
    executable?: unknown;
    draft?: { request_id?: unknown; revision?: unknown } | null;
  };
  const draft = record.draft;
  if (
    record.execution_authorized !== false ||
    record.executable !== false ||
    typeof record.message !== "string" ||
    record.message.trim() === "" ||
    !draft ||
    typeof draft.request_id !== "string" ||
    draft.request_id.trim() === "" ||
    typeof draft.revision !== "number" ||
    !Number.isInteger(draft.revision) ||
    draft.revision < 1
  ) {
    return null;
  }
  return { message: record.message, requestId: draft.request_id, revision: draft.revision };
}

export type ClarificationAnswer = {
  field: string;
  value: string;
};

export async function updateDesignDraft(input: {
  session_id: string;
  message: string;
  request_id: string | null;
  revision: number | null;
  clarifications?: ClarificationAnswer[];
}): Promise<DesignDraftReply> {
  const response = await fetch(DESIGN_DRAFT_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: input.session_id,
      message: input.message,
      request_id: input.request_id,
      revision: input.revision,
      ...(input.clarifications && input.clarifications.length > 0
        ? { clarifications: input.clarifications }
        : {}),
    }),
  });
  if (!response.ok && response.status !== 409) {
    return failedDesignReply(input.session_id);
  }
  try {
    const body = (await response.json()) as DesignDraftReply;
    if (body.execution_authorized !== false || body.executable !== false || typeof body.text !== "string") {
      return failedDesignReply(input.session_id);
    }
    return body;
  } catch {
    return failedDesignReply(input.session_id);
  }
}

export const DESIGN_SUPERVISOR_PATH = "/api/design-supervisor";

export type RecordedGap = {
  field: string;
  question: string;
};

export type SupervisorQuestion = {
  question: string;
  field: string | null;
};

const FIELD_CUES: [string, string[]][] = [
  ["n_candidates", ["n_candidates", "candidate"]],
  ["length_min", ["length_min"]],
  ["length_max", ["length_max"]],
  ["filter_policy", ["filter_policy", "filter policy"]],
  ["generator", ["generator", "generation method"]],
  ["objective", ["design objective", "objective"]],
  ["seed", ["integer seed", "seed"]],
  ["target_id", ["target_id", "scientific target", "which target"]],
];

function cueMatches(normalized: string, cue: string): boolean {
  if (cue.includes(" ") || cue.includes("_") || cue === "candidate") {
    return normalized.includes(cue);
  }
  return ` ${normalized} `.includes(` ${cue} `);
}

export function knownQuestionField(question: string, explicit?: string | null): string | null {
  const named = explicit?.trim();
  if (named) {
    return named;
  }
  const stripped = question.trim();
  const colon = stripped.indexOf(":");
  if (colon > 0) {
    const prefix = stripped.slice(0, colon).trim().toLowerCase();
    if (FIELD_CUES.some(([field]) => field === prefix) && stripped.slice(colon + 1).trim()) {
      return prefix;
    }
  }
  const normalized = stripped.toLowerCase().split(/\s+/).join(" ");
  const matched = FIELD_CUES.filter(([, cues]) => cues.some((cue) => cueMatches(normalized, cue))).map(
    ([field]) => field,
  );
  return matched.length === 1 ? matched[0] : null;
}

export type ConfiguredChoice = {
  field: string;
  value: string;
  text: string;
  source: "server_configuration";
};

export type SupervisorModelCall = {
  executed: boolean;
  provider: string;
  model: string;
  inputTokens: string;
  outputTokens: string;
  totalTokens: string;
};

export const MODEL_NOT_EXECUTED = "Model not run";
const NOT_INFORMED = "Not reported";

export const modelNotExecuted: SupervisorModelCall = {
  executed: false,
  provider: "",
  model: MODEL_NOT_EXECUTED,
  inputTokens: MODEL_NOT_EXECUTED,
  outputTokens: MODEL_NOT_EXECUTED,
  totalTokens: MODEL_NOT_EXECUTED,
};

export type SupervisorAnalysis = {
  status: "analyzed" | "analysis_rejected" | "supervisor_unavailable" | "draft_not_found" | "revision_conflict";
  current: boolean;
  analysisId: string;
  analyzedAt: string | null;
  requestId: string;
  revision: number;
  message: string;
  summary: string;
  modelCall: SupervisorModelCall;
  extractedQuestions: RecordedGap[];
  supervisorQuestions: SupervisorQuestion[];
  configuredChoices: ConfiguredChoice[];
  generatorLimitation: string | null;
};

function modelCallFrom(value: unknown): SupervisorModelCall {
  if (!value || typeof value !== "object") {
    return {
      executed: false,
      provider: "",
      model: NOT_INFORMED,
      inputTokens: NOT_INFORMED,
      outputTokens: NOT_INFORMED,
      totalTokens: NOT_INFORMED,
    };
  }
  if (!("executed" in value) || value.executed !== true) {
    return modelNotExecuted;
  }
  const model = "model" in value && typeof value.model === "string" && value.model.trim() ? value.model.trim() : NOT_INFORMED;
  return {
    executed: true,
    provider: "",
    model,
    inputTokens: tokenLabel("input_tokens" in value ? value.input_tokens : undefined),
    outputTokens: tokenLabel("output_tokens" in value ? value.output_tokens : undefined),
    totalTokens: tokenLabel("total_tokens" in value ? value.total_tokens : undefined),
  };
}

function configuredChoicesFrom(value: unknown): ConfiguredChoice[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const field = "field" in item ? item.field : undefined;
    const choiceValue = "value" in item ? item.value : undefined;
    const text = "text" in item ? item.text : undefined;
    const source = "source" in item ? item.source : undefined;
    if (
      field !== "generator" ||
      source !== "server_configuration" ||
      typeof choiceValue !== "string" ||
      choiceValue.trim() === "" ||
      typeof text !== "string" ||
      text.trim() === ""
    ) {
      return [];
    }
    return [{ field, value: choiceValue.trim(), text: text.trim(), source }];
  });
}

function providerFrom(instrumentation: unknown, modelCall: unknown): string {
  if (!modelCall || typeof modelCall !== "object" || !("executed" in modelCall) || modelCall.executed !== true) {
    return "";
  }
  if (!instrumentation || typeof instrumentation !== "object" || !("provider" in instrumentation)) {
    return "";
  }
  const provider = instrumentation.provider;
  return typeof provider === "string" ? provider.trim() : "";
}

function tokenLabel(value: unknown): string {
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) {
    return String(value);
  }
  if (value === NOT_INFORMED || value === "Não informado") {
    return NOT_INFORMED;
  }
  return NOT_INFORMED;
}

function recordedGaps(value: unknown): RecordedGap[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || !("source" in item) || item.source !== "recorded") {
      return [];
    }
    const field = "field" in item ? item.field : undefined;
    const question = "question" in item ? item.question : undefined;
    if (typeof field !== "string" || field.trim() === "" || typeof question !== "string" || question.trim() === "") {
      return [];
    }
    return [{ field, question }];
  });
}

function supervisorGaps(value: unknown, extracted: unknown): SupervisorQuestion[] {
  const recorded = new Set(recordedGaps(extracted).map((item) => item.question.trim().toLowerCase()));
  const recordedFields = new Set(recordedGaps(extracted).map((item) => item.field));
  if (!Array.isArray(value)) {
    return [];
  }
  const seenFields = new Set<string>();
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || !("source" in item) || item.source !== "supervisor") {
      return [];
    }
    const question = "question" in item ? item.question : undefined;
    if (typeof question !== "string" || question.trim() === "" || recorded.has(question.trim().toLowerCase())) {
      return [];
    }
    const explicit = "field" in item && typeof item.field === "string" ? item.field : null;
    const field = knownQuestionField(question, explicit);
    if (field && (recordedFields.has(field) || seenFields.has(field))) {
      return [];
    }
    if (field) {
      seenFields.add(field);
    }
    return [{ question, field }];
  });
}

function textValue(body: object, key: string): string {
  const value = (body as Record<string, unknown>)[key];
  return typeof value === "string" ? value.trim() : "";
}

/** Analyses already stored for one request. An unrecognized body is an empty history. */
export async function listSupervisorAnalyses(requestId: string): Promise<SupervisorAnalysis[] | null> {
  let response: Response;
  try {
    response = await fetch(`${DESIGN_SUPERVISOR_PATH}?request_id=${encodeURIComponent(requestId)}`, {
      method: "GET",
    });
  } catch {
    return null;
  }
  if (response.status === 404) {
    return [];
  }
  if (!response.ok) {
    return null;
  }
  try {
    const body = (await response.json()) as {
      status?: unknown;
      request_id?: unknown;
      analyses?: unknown;
      execution_authorized?: unknown;
      executable?: unknown;
    };
    if (body.execution_authorized !== false || body.executable !== false) {
      return null;
    }
    if (body.status !== "analyses_recorded" || body.request_id !== requestId || !Array.isArray(body.analyses)) {
      return [];
    }
    return body.analyses.flatMap((item, index) => {
      if (!item || typeof item !== "object") {
        return [];
      }
      const record = item as {
        request_id?: unknown;
        revision?: unknown;
        message?: unknown;
        analysis_id?: unknown;
        analyzed_at?: unknown;
      };
      if (record.request_id !== requestId || typeof record.message !== "string" || !Number.isInteger(record.revision)) {
        return [];
      }
      const revision = record.revision as number;
      if (revision < 1) {
        return [];
      }
      return storedAnalysis(record, requestId, revision, record.message, index);
    });
  } catch {
    return null;
  }
}

function storedAnalysis(
  record: object,
  requestId: string,
  revision: number,
  message: string,
  index: number,
): SupervisorAnalysis[] {
  const body = record as {
    status?: unknown;
    summary?: unknown;
    current?: unknown;
    analysis_id?: unknown;
    analyzed_at?: unknown;
    extracted_questions?: unknown;
    supervisor_questions?: unknown;
    model_call?: unknown;
    instrumentation?: unknown;
    configured_choices?: unknown;
    generator_configuration_limitation?: unknown;
    execution_authorized?: unknown;
    executable?: unknown;
  };
  if (body.execution_authorized !== false || body.executable !== false) {
    return [];
  }
  if ((body.status !== "analyzed" && body.status !== "analysis_rejected") || typeof body.summary !== "string") {
    return [];
  }
  const parsed = {
    status: body.status,
    current: body.current !== false,
    analysisId:
      typeof body.analysis_id === "string" && body.analysis_id.trim()
        ? body.analysis_id.trim()
        : `stored-${requestId}-${revision}-${index}`,
    analyzedAt: typeof body.analyzed_at === "string" && body.analyzed_at.trim() ? body.analyzed_at.trim() : null,
    requestId,
    revision,
    message,
    summary: body.summary.trim(),
    modelCall: { ...modelCallFrom(body.model_call), provider: providerFrom(body.instrumentation, body.model_call) },
    extractedQuestions: recordedGaps(body.extracted_questions),
    supervisorQuestions: supervisorGaps(body.supervisor_questions, body.extracted_questions),
    configuredChoices: configuredChoicesFrom(body.configured_choices),
    generatorLimitation:
      typeof body.generator_configuration_limitation === "string" && body.generator_configuration_limitation.trim()
        ? body.generator_configuration_limitation.trim()
        : null,
  } satisfies SupervisorAnalysis;
  return parsed.summary ? [parsed] : [];
}

/** One Supervisor reading of a saved revision. Saving stays a separate action. */
export async function analyzeDesignRequest(input: {
  session_id: string;
  request_id: string;
  revision: number;
  message: string;
}): Promise<SupervisorAnalysis | null> {
  let response: Response;
  try {
    response = await fetch(DESIGN_SUPERVISOR_PATH, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        session_id: input.session_id,
        request_id: input.request_id,
        revision: input.revision,
        message: input.message,
      }),
    });
  } catch {
    return null;
  }
  if (!response.ok && response.status !== 404 && response.status !== 409) {
    return null;
  }
  try {
    const body = (await response.json()) as {
      status?: unknown;
      agent?: unknown;
      request_id?: unknown;
      revision?: unknown;
      message?: unknown;
      summary?: unknown;
      extracted_questions?: unknown;
      supervisor_questions?: unknown;
      validation?: unknown;
      execution_authorized?: unknown;
      executable?: unknown;
      model_call?: unknown;
      instrumentation?: unknown;
      configured_choices?: unknown;
      generator_configuration_limitation?: unknown;
      current?: unknown;
    };
    if (body.execution_authorized !== false || body.executable !== false || body.agent !== "Protein Design Supervisor") {
      return null;
    }
    if (
      body.status !== "analyzed" &&
      body.status !== "analysis_rejected" &&
      body.status !== "supervisor_unavailable" &&
      body.status !== "draft_not_found" &&
      body.status !== "revision_conflict"
    ) {
      return null;
    }
    if (typeof body.summary !== "string" || body.summary.trim() === "") {
      return null;
    }
    if (body.request_id !== input.request_id || body.revision !== input.revision || body.message !== input.message) {
      return null;
    }
    const validation = body.validation;
    const accepted =
      body.status === "analyzed" &&
      !!validation &&
      typeof validation === "object" &&
      "status" in validation &&
      validation.status === "accepted";
    if (body.status === "analyzed" && !accepted) {
      return null;
    }
    const extractedQuestions =
      body.status === "analyzed" || body.status === "analysis_rejected"
        ? recordedGaps(body.extracted_questions)
        : [];
    const supervisorQuestions =
      body.status === "analyzed" ? supervisorGaps(body.supervisor_questions, body.extracted_questions) : [];
    return {
      status: body.status,
      current: body.current !== false,
      analysisId: textValue(body, "analysis_id"),
      analyzedAt: textValue(body, "analyzed_at") || null,
      requestId: input.request_id,
      revision: input.revision,
      message: input.message,
      summary: body.summary.trim(),
      modelCall: { ...modelCallFrom(body.model_call), provider: providerFrom(body.instrumentation, body.model_call) },
      extractedQuestions,
      supervisorQuestions,
      configuredChoices: configuredChoicesFrom(body.configured_choices),
      generatorLimitation:
        typeof body.generator_configuration_limitation === "string" && body.generator_configuration_limitation.trim()
          ? body.generator_configuration_limitation.trim()
          : null,
    };
  } catch {
    return null;
  }
}
