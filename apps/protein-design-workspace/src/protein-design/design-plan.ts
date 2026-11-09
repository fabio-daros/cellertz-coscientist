export const GENERATION_PLAN_PATH = "/api/generation-plan";
export const GENERATION_PLAN_REVISION_PATH = "/api/generation-plan/revision";
export const PLAN_SUGGESTION_PATH = "/api/generation-plan/suggestion";

export type PlanFact = {
  value: string | number | null;
  source: "researcher" | "server_configuration" | "pending";
  pending: boolean;
};

export type PlanSummary = {
  objective: PlanFact;
  target: PlanFact;
  candidatesRequested: PlanFact;
  generationMethod: PlanFact;
};

export type PlanQuestion = {
  field: string;
  question: string;
};

export type PlanParameter = {
  field: string;
  label: string;
  value: string;
  source: string;
};

export type PlanIntegration = {
  field: string;
  support: string;
};

export type PlanApproval = {
  reviewerId: string;
  reason: string;
  revision: number;
  digest: string;
};

export type DesignPlanRecord = {
  planId: string;
  planRevision: number;
  requestId: string;
  requestRevision: number;
  status: string;
  digest: string;
  approved: boolean;
  approval: PlanApproval | null;
  parameters: PlanParameter[];
  questions: PlanQuestion[];
  hotspotStrategy: "residues" | "none" | null;
  filterMode: "technical_demonstration" | null;
  integrations: PlanIntegration[];
};

export type PlanRevisionEdits = {
  scientific: Record<string, unknown>;
  technical: Record<string, unknown>;
  adoptedOrigins?: Record<string, { source: string; suggestionId: string }>;
};

export type PlanRevisionResult =
  | { kind: "recorded"; catalog: DesignPlanCatalog; changed: boolean }
  | { kind: "conflict" }
  | { kind: "invalid" }
  | { kind: "failed" };

export type DesignPlanCatalog = {
  requestId: string;
  revision: number;
  summary: PlanSummary;
  plans: DesignPlanRecord[];
  proposedWorkflow: string[];
  planningOrigin: "deterministic_generation_plan";
  modelDecided: false;
  agentsExecuted: false;
  executionPermitted: boolean;
};

const PARAMETER_LABELS: Record<string, string> = {
  target_identity: "Target",
  binder_length: "Binder length",
  hotspot_assumption: "Hotspot assumption",
  filter_policy: "Filter policy",
  seed: "Seed",
  cpu_count: "CPU count",
  memory_mib: "Memory (MiB)",
  gpu: "GPU",
  execution_deadline_seconds: "Execution deadline (seconds)",
};

export async function readDesignPlan(requestId: string): Promise<DesignPlanCatalog | null> {
  return requestPlan(`${GENERATION_PLAN_PATH}?request_id=${encodeURIComponent(requestId)}`, {
    method: "GET",
  }, requestId);
}

export async function reviseDesignPlan(
  requestId: string,
  requestRevision: number,
  planId: string,
  expectedPlanRevision: number,
  edits: PlanRevisionEdits,
): Promise<PlanRevisionResult> {
  let response: Response;
  try {
    response = await fetch(GENERATION_PLAN_REVISION_PATH, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        request_id: requestId,
        request_revision: requestRevision,
        plan_id: planId,
        expected_plan_revision: expectedPlanRevision,
        scientific: edits.scientific,
        technical: edits.technical,
        ...(edits.adoptedOrigins ? { adopted_origins: Object.fromEntries(
          Object.entries(edits.adoptedOrigins).map(([field, origin]) => [field, { source: origin.source, suggestion_id: origin.suggestionId }]),
        ) } : {}),
      }),
    });
  } catch {
    return { kind: "failed" };
  }
  if (response.status === 409) {
    return { kind: "conflict" };
  }
  if (response.status === 400) {
    return { kind: "invalid" };
  }
  if (!response.ok) {
    return { kind: "failed" };
  }
  try {
    const body = (await response.json()) as Record<string, unknown>;
    const catalog = parseCatalog(body, requestId);
    if (!catalog) {
      return { kind: "failed" };
    }
    return { kind: "recorded", catalog, changed: body.plan_revision_changed === true };
  } catch {
    return { kind: "failed" };
  }
}

export type SuggestionChoice = {
  field: string;
  label: string;
  current: string | number | null;
  suggested: string | number;
  source: string;
  kind: string;
  applicable: boolean;
  justification?: string;
  note?: string;
};

export type LiteratureRecord = {
  id: string;
  title: string;
  url: string;
  excerpt: string;
  textLevel: string;
  fullTextRetrieved: boolean;
  retrievedAt: string;
  relevance: "direct" | "context";
  publicationKind: "preprint" | "article";
};

export type PlanSuggestion = {
  suggestionId: string;
  status: string;
  applicable: boolean;
  stale: boolean;
  requestId: string;
  planId: string;
  planRevision: number;
  digest: string;
  summary: string;
  suggestions: SuggestionChoice[];
  uncertainties: string[];
  agent: string;
  model: string;
  tokens: string;
  tools: string[];
  sources: string[];
  limitations: string[];
  structureSha256: string;
  literature: LiteratureRecord[];
  literatureFromCache: boolean;
  literatureRetrievedAt: string;
  region: string;
  estimate: string;
  specialistSummary: string;
  specialistLines: string[];
  specialistState: "text" | "not_stored" | "refused" | "empty";
  systemMessages: string[];
  residueMapping: string;
  recordComplete: boolean;
};

export async function readPlanSuggestions(requestId: string, planId: string): Promise<PlanSuggestion[]> {
  try {
    const response = await fetch(
      `${PLAN_SUGGESTION_PATH}?request_id=${encodeURIComponent(requestId)}&plan_id=${encodeURIComponent(planId)}`,
    );
    if (!response.ok) {
      return [];
    }
    return parseSuggestions(await response.json(), requestId, planId);
  } catch {
    return [];
  }
}

export async function requestPlanSuggestion(
  requestId: string,
  requestRevision: number,
  planId: string,
  planRevision: number,
  digest: string,
): Promise<PlanSuggestion | null> {
  const response = await fetch(PLAN_SUGGESTION_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      request_id: requestId,
      request_revision: requestRevision,
      plan_id: planId,
      plan_revision: planRevision,
      digest,
    }),
  });
  if (!response.ok) {
    return null;
  }
  const parsed = parseSuggestions(await response.json(), requestId, planId);
  return parsed[0] ?? null;
}

function parseSuggestions(body: unknown, requestId: string, planId: string): PlanSuggestion[] {
  const record = body && typeof body === "object" ? body as Record<string, unknown> : {};
  const rows = typeof record.suggestion_id === "string"
    ? [record]
    : Array.isArray(record.suggestions) ? record.suggestions : [];
  return rows.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const row = item as Record<string, unknown>;
    if (row.request_id !== requestId || row.plan_id !== planId || typeof row.suggestion_id !== "string") {
      return [];
    }
    const delegation = row.delegation && typeof row.delegation === "object" ? row.delegation as Record<string, unknown> : {};
    const call = delegation.model_call && typeof delegation.model_call === "object" ? delegation.model_call as Record<string, unknown> : {};
    const supervisorCall = delegation.supervisor_model_call && typeof delegation.supervisor_model_call === "object"
      ? delegation.supervisor_model_call as Record<string, unknown>
      : {};
    const tools = row.tools && typeof row.tools === "object" ? row.tools as Record<string, unknown> : {};
    const sources = new Set<string>();
    if (Array.isArray(row.suggestions)) {
      for (const item of row.suggestions) {
        if (!item || typeof item !== "object") {
          continue;
        }
        const evidence = (item as Record<string, unknown>).evidence;
        if (!Array.isArray(evidence)) {
          continue;
        }
        for (const note of evidence) {
          if (!note || typeof note !== "object" || !Array.isArray((note as Record<string, unknown>).references)) {
            continue;
          }
          for (const reference of (note as Record<string, unknown>).references as unknown[]) {
            if (typeof reference === "string" && reference.trim()) {
              sources.add(reference);
            }
          }
        }
      }
    }
    const literatureTool = tools["literature.search"] && typeof tools["literature.search"] === "object"
      ? tools["literature.search"] as Record<string, unknown>
      : {};
    const literature = literatureRecords(literatureTool);
    for (const record of literature) {
      sources.add(record.id);
    }
    const region = row.region_of_interest && typeof row.region_of_interest === "object"
      ? (row.region_of_interest as Record<string, unknown>).statement
      : "";
    const limitations = Object.values(tools).flatMap((tool) => {
      if (!tool || typeof tool !== "object") {
        return [];
      }
      const limitation = (tool as Record<string, unknown>).limitation;
      return typeof limitation === "string" && limitation.trim() ? [limitation] : [];
    });
    const layers = readLayers(row, limitations);
    const response = row.specialist_response && typeof row.specialist_response === "object"
      ? row.specialist_response as Record<string, unknown>
      : null;
    const validation = row.validation_result && typeof row.validation_result === "object"
      ? row.validation_result as Record<string, unknown>
      : {};
    return [{
      suggestionId: row.suggestion_id,
      status: typeof row.status === "string" ? row.status : "",
      applicable: row.applicable === true && row.stale !== true,
      stale: row.stale === true,
      requestId,
      planId,
      planRevision: typeof row.plan_revision === "number" ? row.plan_revision : 0,
      digest: typeof row.digest === "string" ? row.digest : "",
      summary: typeof row.summary === "string" ? row.summary : "",
      suggestions: Array.isArray(row.suggestions) ? row.suggestions.flatMap(parseChoice) : [],
      uncertainties: Array.isArray(row.uncertainties) ? row.uncertainties.filter((line): line is string => typeof line === "string") : [],
      agent: typeof delegation.agent === "string" ? delegation.agent : "",
      model: typeof call.model === "string" ? call.model : "",
      tokens: tokenLine(supervisorCall, call),
      tools: Object.keys(tools),
      sources: [...sources],
      limitations,
      structureSha256: typeof row.structure_sha256 === "string" ? row.structure_sha256 : "",
      literature,
      literatureFromCache: literatureTool.cache_hit === true,
      literatureRetrievedAt: typeof literatureTool.retrieved_at === "string" ? literatureTool.retrieved_at : "",
      region: typeof region === "string" ? region : "",
      estimate: estimateNote(row.token_estimate),
      specialistSummary: layers.summary,
      specialistLines: layers.lines,
      specialistState: layers.state,
      systemMessages: layers.system,
      residueMapping: validation.residue_mapping === "mapped" || validation.residue_mapping === "not_mapped"
        ? validation.residue_mapping
        : "",
      recordComplete: response?.complete === true,
    }];
  });
}

const SYSTEM_TEXT = new Set([
  "Insufficient target-specific evidence to recommend hotspots.",
  "The agent marked hotspots as undefined. Absence of evidence is not a recommendation to leave hotspots undefined.",
  "Earlier generation results were not consulted as evidence for this suggestion.",
  "Retrieved literature does not provide a verified residue map for the selected structure.",
  "The shared token budget was exhausted.",
  "The estimated prompt does not fit the shared token budget. The provider was not called.",
  "The Hotspot Identification Agent call failed. It was not repeated.",
]);

function classifyStoredLiterature(title: string, excerpt: string, id: string): {
  relevance: "direct" | "context";
  publicationKind: "preprint" | "article";
} {
  const blob = `${title}\n${excerpt}`.toLowerCase();
  const direct = ["binding interface", "hotspot", "interface residue", "protein-protein"].some((term) => blob.includes(term))
    && !["small molecule", "drug pocket", "rna-binding", "post-transcriptional"].some((term) => blob.includes(term));
  return {
    relevance: direct ? "direct" : "context",
    publicationKind: id.startsWith("PPR:") ? "preprint" : "article",
  };
}

function completeText(value: string): boolean {
  const text = value.trim();
  return text.includes(" ") && text.length >= 8;
}

function systemText(value: string, limitations: string[]): boolean {
  const text = value.trim();
  return SYSTEM_TEXT.has(text)
    || limitations.some((line) => line.trim() === text)
    || text.toLowerCase().includes("not a recommendation")
    || text.includes("was rejected because")
    || text.includes("were refused because")
    || text.includes("was refused because")
    || text.includes("is not a residue in the verified structure.")
    || text.includes("Those items were refused.");
}

function readLayers(row: Record<string, unknown>, limitations: string[]): {
  summary: string;
  lines: string[];
  state: "text" | "not_stored" | "refused" | "empty";
  system: string[];
} {
  const stored = Array.isArray(row.uncertainties)
    ? row.uncertainties.filter((line): line is string => typeof line === "string")
    : [];
  const recordedSystem = Array.isArray(row.system_messages)
    ? row.system_messages.filter((line): line is string => typeof line === "string" && completeText(line))
    : [];
  const response = row.specialist_response && typeof row.specialist_response === "object"
    ? row.specialist_response as Record<string, unknown>
    : null;
  if (response) {
    const summary = typeof response.summary === "string" && completeText(response.summary) ? response.summary.trim() : "";
    const lines = Array.isArray(response.uncertainties)
      ? response.uncertainties.filter((line): line is string => typeof line === "string" && completeText(line))
      : [];
    const normalization = typeof response.normalization === "string" ? response.normalization : "";
    const state = normalization.startsWith("refused") ? "refused" : summary || lines.length > 0 ? "text" : "empty";
    return { summary, lines, state, system: recordedSystem };
  }
  const fragments = stored.some((line) => !completeText(line));
  const lines = stored.filter((line) => completeText(line) && !systemText(line, limitations));
  const system = [
    ...stored.filter((line) => completeText(line) && systemText(line, limitations)),
    ...recordedSystem,
  ];
  if (typeof row.summary === "string" && completeText(row.summary) && systemText(row.summary, limitations) && !system.includes(row.summary.trim())) {
    system.unshift(row.summary.trim());
  }
  const summary = typeof row.summary === "string" && completeText(row.summary) && !systemText(row.summary, limitations)
    ? row.summary.trim()
    : "";
  if (fragments) {
    return { summary: "", lines, state: "not_stored", system };
  }
  return { summary, lines, state: summary || lines.length > 0 ? "text" : "empty", system };
}

function estimateNote(value: unknown): string {
  if (!value || typeof value !== "object") {
    return "";
  }
  const note = (value as Record<string, unknown>).note;
  return typeof note === "string" ? note : "";
}

function literatureRecords(tool: unknown): LiteratureRecord[] {
  if (!tool || typeof tool !== "object" || !Array.isArray((tool as Record<string, unknown>).results)) {
    return [];
  }
  return ((tool as Record<string, unknown>).results as unknown[]).flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const row = item as Record<string, unknown>;
    if (typeof row.id !== "string" || typeof row.title !== "string" || typeof row.url !== "string") {
      return [];
    }
    const excerpt = typeof row.excerpt === "string" ? row.excerpt : "";
    const labels = classifyStoredLiterature(row.title, excerpt, row.id);
    return [{
      id: row.id,
      title: row.title,
      url: row.url,
      excerpt,
      textLevel: row.text_level === "abstract" ? "abstract" : "unavailable",
      fullTextRetrieved: row.full_text_retrieved === true,
      retrievedAt: typeof row.retrieved_at === "string" ? row.retrieved_at : "",
      relevance: row.relevance === "direct" || row.relevance === "context" ? row.relevance : labels.relevance,
      publicationKind: row.publication_kind === "preprint" || row.publication_kind === "article"
        ? row.publication_kind
        : labels.publicationKind,
    }];
  });
}

function tokenLine(supervisor: unknown, specialist: unknown): string {
  const shown = (value: unknown) => value === undefined || value === null || value === "" ? "Not reported" : String(value);
  const part = (label: string, source: unknown) => {
    if (!source || typeof source !== "object") {
      return `${label} not run`;
    }
    const row = source as Record<string, unknown>;
    if (row.executed === false) {
      return `${label} not run`;
    }
    return `${label} in ${shown(row.input_tokens)} out ${shown(row.output_tokens)} total ${shown(row.total_tokens)}`;
  };
  return `${part("Supervisor", supervisor)}; ${part("specialist", specialist)}`;
}

function parseChoice(value: unknown): SuggestionChoice[] {
  if (!value || typeof value !== "object") {
    return [];
  }
  const row = value as Record<string, unknown>;
  if (typeof row.field !== "string" || (typeof row.suggested !== "string" && typeof row.suggested !== "number")) {
    return [];
  }
  return [{
    field: row.field,
    label: typeof row.label === "string" ? row.label : row.field,
    current: typeof row.current === "string" || typeof row.current === "number" ? row.current : null,
    suggested: row.suggested,
    source: typeof row.source === "string" ? row.source : "",
    kind: typeof row.kind === "string" ? row.kind : "",
    applicable: row.applicable === true,
    justification: typeof row.justification === "string" ? row.justification : undefined,
    note: typeof row.note === "string" ? row.note : undefined,
  }];
}

export async function createDesignPlan(requestId: string, revision: number): Promise<DesignPlanCatalog | null> {
  return requestPlan(GENERATION_PLAN_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ request_id: requestId, revision }),
  }, requestId);
}

async function requestPlan(url: string, init: RequestInit, requestId: string): Promise<DesignPlanCatalog | null> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    return null;
  }
  if (!response.ok) {
    return null;
  }
  try {
    const body = (await response.json()) as Record<string, unknown>;
    return parseCatalog(body, requestId);
  } catch {
    return null;
  }
}

function parseCatalog(body: Record<string, unknown>, requestId: string): DesignPlanCatalog | null {
  if (
    body.request_id !== requestId ||
    body.planning_origin !== "deterministic_generation_plan" ||
    body.model_decided !== false ||
    body.agents_executed !== false ||
    body.approved !== false ||
    body.execution_authorized !== false ||
    body.executable !== false ||
    body.scientific_execution !== "disabled" ||
    (body.status !== "plans_recorded" && body.status !== "plan_recorded") ||
    typeof body.revision !== "number" ||
    !Number.isInteger(body.revision) ||
    body.revision < 1 ||
    !Array.isArray(body.plans) ||
    !Array.isArray(body.proposed_workflow)
  ) {
    return null;
  }
  const summary = parseSummary(body.summary);
  if (!summary) {
    return null;
  }
  const plans = body.plans.flatMap((item) => {
    const parsed = parsePlan(item, requestId);
    return parsed ? [parsed] : [];
  });
  if (plans.length !== body.plans.length) {
    return null;
  }
  const workflow = body.proposed_workflow.filter((item): item is string => typeof item === "string" && item.trim() !== "");
  if (workflow.length !== body.proposed_workflow.length) {
    return null;
  }
  return {
    requestId,
    revision: body.revision,
    summary,
    plans,
    proposedWorkflow: workflow,
    planningOrigin: "deterministic_generation_plan",
    modelDecided: false,
    agentsExecuted: false,
    executionPermitted: currentServerAllowsExecution(body),
  };
}

function currentServerAllowsExecution(body: Record<string, unknown>): boolean {
  const server = body.server_execution;
  if (server && typeof server === "object") {
    const row = server as Record<string, unknown>;
    if ("demonstration_configured" in row || "mode" in row) {
      return row.mode === "apptainer-demonstration" && row.demonstration_configured === true;
    }
  }
  return body.execution_permitted === true;
}

function parseSummary(value: unknown): PlanSummary | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const objective = parseFact(record.objective);
  const target = parseFact(record.target);
  const candidatesRequested = parseFact(record.candidates_requested);
  const generationMethod = parseFact(record.generation_method);
  if (!objective || !target || !candidatesRequested || !generationMethod) {
    return null;
  }
  return { objective, target, candidatesRequested, generationMethod };
}

function parseFact(value: unknown): PlanFact | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  const source = record.source;
  if (source !== "researcher" && source !== "server_configuration" && source !== "pending") {
    return null;
  }
  if (typeof record.pending !== "boolean") {
    return null;
  }
  const factValue = record.value;
  if (factValue !== null && typeof factValue !== "string" && typeof factValue !== "number") {
    return null;
  }
  if (record.pending !== (factValue === null)) {
    return null;
  }
  return { value: factValue, source, pending: record.pending };
}

function parsePlanApproval(value: unknown, revision: number, digest: string): PlanApproval | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (
    record.revision !== revision ||
    record.digest !== digest ||
    typeof record.reviewer_id !== "string" ||
    record.reviewer_id.trim() === "" ||
    typeof record.reason !== "string" ||
    record.reason.trim() === "" ||
    record.execution_authorized !== false ||
    record.launches_compute !== false
  ) {
    return null;
  }
  return {
    reviewerId: record.reviewer_id,
    reason: record.reason,
    revision,
    digest,
  };
}

function parsePlan(value: unknown, requestId: string): DesignPlanRecord | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (
    record.design_request_id !== requestId ||
    typeof record.plan_id !== "string" ||
    record.plan_id.trim() === "" ||
    typeof record.revision !== "number" ||
    !Number.isInteger(record.revision) ||
    record.revision < 1 ||
    typeof record.design_request_revision !== "number" ||
    !Number.isInteger(record.design_request_revision) ||
    record.design_request_revision < 1 ||
    typeof record.status !== "string" ||
    typeof record.digest !== "string" ||
    record.digest.trim() === "" ||
    typeof record.approved !== "boolean" ||
    record.execution_authorized !== false ||
    record.execution_connected !== false ||
    record.scientific_execution !== "disabled" ||
    !record.fields ||
    typeof record.fields !== "object" ||
    !Array.isArray(record.questions)
  ) {
    return null;
  }
  const questions = record.questions.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const question = item as Record<string, unknown>;
    if (typeof question.field !== "string" || typeof question.question !== "string" || question.question.trim() === "") {
      return [];
    }
    return [{ field: question.field, question: question.question }];
  });
  if (questions.length !== record.questions.length) {
    return null;
  }
  const approval = parsePlanApproval(record.approval, record.revision, record.digest);
  if (record.approved === true && approval === null) {
    return null;
  }
  if (record.approved === false && record.approval != null) {
    return null;
  }
  const fields = record.fields as Record<string, unknown>;
  return {
    planId: record.plan_id,
    planRevision: record.revision,
    requestId,
    requestRevision: record.design_request_revision,
    status: record.status,
    digest: record.digest,
    approved: record.approved,
    approval,
    parameters: parametersOf(fields),
    questions,
    hotspotStrategy: fields.hotspot_strategy === "residues" || fields.hotspot_strategy === "none" ? fields.hotspot_strategy : null,
    filterMode: fields.filter_policy_mode === "technical_demonstration" ? "technical_demonstration" : null,
    integrations: integrationsOf(fields),
  };
}

const INTEGRATION_FIELDS = [
  "binder_length",
  "hotspot_assumption",
  "filter_policy",
  "seed",
  "gpu",
  "cpu_count",
  "memory_mib",
  "execution_deadline_seconds",
] as const;

function integrationsOf(fields: Record<string, unknown>): PlanIntegration[] {
  return INTEGRATION_FIELDS.flatMap((field) => {
    if (fields[`${field}_execution_integration`] !== "pending") {
      return [];
    }
    const support = fields[`${field}_execution_support`];
    if (typeof support !== "string" || support.trim() === "") {
      return [];
    }
    return [{ field, support }];
  });
}

function parametersOf(fields: Record<string, unknown>): PlanParameter[] {
  return Object.entries(PARAMETER_LABELS).flatMap(([field, label]) => {
    const value = fields[field];
    if (typeof value !== "string" && typeof value !== "number") {
      return [];
    }
    const source = fields[`${field}_source`];
    const provenance = fields[`${field}_provenance`];
    const shown =
      provenance === "adopted_suggestion" && source === "hotspot_identification_agent"
        ? "Adopted from Hotspot Identification Agent"
        : provenance === "adopted_suggestion" && source === "technical_demonstration_profile"
          ? "Adopted from server configuration"
        : source === "professor" || provenance === "user"
          ? "Researcher"
          : source === "verified_input" || source === "technical_demonstration_profile"
            ? "Server configuration"
            : "Recorded";
    return [{ field, label, value: String(value), source: shown }];
  });
}
