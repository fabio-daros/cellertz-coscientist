export type ConversationMessage = {
  message_id: string;
  role: "scientist" | "system" | "assistant";
  created_at: string;
  content: string;
};

export type ProteinDesignDraft = {
  draft_id: string;
  target_id: string;
  design_goal: string;
  status: "incomplete" | "ready_for_review";
  executable: false;
};

export type ProjectContext = {
  project_id: string;
  target_id: string;
  design_goal: string;
  prototype_label: string;
};

export type PipelineStageSnapshot = {
  stage_id: string;
  label: string;
  display_status: "shown" | "future" | "not_executed";
  detail: string;
};

export type AgentSnapshot = {
  agent_id: string;
  role: string;
  display_status: "idle" | "shown";
};

export type CandidateSummary = {
  candidate_id: string;
  candidate_kind: "observed_identity" | "placeholder";
  epistemic_status: "observed" | "unknown";
  rank: number | null;
  ranking_signal: "raw_ipsae";
  ranking_score: number | null;
  score_label: string;
  is_probability: false;
  recommended_as_hard_filter: false;
  provenance: string;
};

export type EvidenceSummary = {
  evidence_id: string;
  candidate_id: string;
  kind: string;
  summary: string;
  epistemic_status: "observed" | "validated" | "unknown";
};

export type ArtifactSummary = {
  artifact_id: string;
  candidate_id: string;
  kind: string;
  uri: string;
  media_type: string;
};

export type RunSummary = {
  run_id: string;
  target_id: string;
  memory_house: "episodic";
  epistemic_status: "observed" | "unknown";
  display_status: string;
};

export type ModelUsageEvent = {
  session_id: string;
  run_id: string | null;
  agent_id: string;
  stage: string;
  provider: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  tool_call_count: number | null;
  estimated_cost: number | null;
  currency: string | null;
  started_at: string;
  completed_at: string;
  cost_kind: "model";
};

export type ExecutionBudget = {
  token_ceiling: number | null;
  model_cost_ceiling: number | null;
  scientific_tool_cost_ceiling: number | null;
  warning_threshold: number | null;
  action_on_limit: "warn" | "stop";
  human_override_required: boolean;
  currency: string | null;
  authorizes_execution: false;
};

export type UsageBudgetSnapshot = {
  session_id: string;
  estimated_model_cost: number | null;
  scientific_infrastructure_cost: number | null;
  currency: string | null;
  model_cost_known: boolean;
  infrastructure_cost_known: boolean;
};

/** Serialized `WorkspaceSession.to_dict()` from the Python application contract. */
export type WorkspaceSession = {
  session_id: string;
  project: ProjectContext;
  messages: ConversationMessage[];
  draft: ProteinDesignDraft;
  pipeline: PipelineStageSnapshot[];
  agents: AgentSnapshot[];
  candidates: CandidateSummary[];
  evidence: EvidenceSummary[];
  artifacts: ArtifactSummary[];
  runs: RunSummary[];
  usage: ModelUsageEvent[];
  budget: ExecutionBudget;
  usage_snapshot: UsageBudgetSnapshot;
  execution_authorized: false;
  prototype_only: true;
};

export type ConversationAsk = {
  session_id: string;
  question: string;
  run_id: string | null;
  candidate_id: string | null;
};

export type ReadOnlyFact = {
  capability: string;
  entity_kind: string;
  entity_id: string;
  source: string;
  attributes: Record<string, string>;
};

export type ConversationOrigin = {
  provider: string;
  model: string;
};

export type ConversationInstrumentation = {
  interaction_id: string;
  recorded: boolean;
  total_tokens: number | null;
  observation: string;
  event: ModelUsageEvent | null;
  origin?: ConversationOrigin | null;
};

export type ConversationReply = {
  status:
    | "answered"
    | "session_not_found"
    | "run_not_found"
    | "run_not_in_session"
    | "unsupported_question"
    | "forbidden_action"
    | "adapter_failure"
    | "provider_budget"
    | "insufficient_data";
  text: string;
  session_id: string;
  run_id: string | null;
  read_only: true;
  evidence: ReadOnlyFact[];
  instrumentation: ConversationInstrumentation;
};

export type RequestField = {
  name: string;
  value: string | number | null;
  provenance: "user" | "preserved" | "documented_default" | "absent" | "ambiguous" | "rejected";
};

export type DraftIssue = { field: string; code: string };
export type DraftQuestion = { field: string; question: string };

export type Pdl1InputSelectionView = {
  schema: "cellertz.pdl1_input_selection.v1";
  manifest_digest: string;
  target_structure_sha256: string;
  settings_sha256: string;
  advanced_profile_sha256: string;
};

export type DesignRequestDraft = {
  request_id: string;
  session_id: string;
  revision: number;
  completeness: "incomplete" | "complete";
  execution_authorized: false;
  executable: false;
  fields: RequestField[];
  issues: DraftIssue[];
  input_selection?: Pdl1InputSelectionView;
};

export type DesignDraftReply = {
  status: string;
  text: string;
  session_id: string;
  execution_authorized: false;
  executable: false;
  changed_fields: string[];
  questions: DraftQuestion[];
  issues: DraftIssue[];
  draft: DesignRequestDraft | null;
  request: { execution_authorized: false; executable: false } | null;
  instrumentation: ConversationInstrumentation;
};

export function formatCost(amount: number | null, currency: string | null): string {
  if (amount === null || currency === null) {
    return "Unknown";
  }
  return `${amount} ${currency}`;
}
