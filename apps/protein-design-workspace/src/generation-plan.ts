export const GENERATION_DECISION_PATH = "/api/generation-plan/decision";
export const GENERATION_AUTHORIZATION_PATH = "/api/generation-plan/authorization";
export const GENERATION_DISPATCH_PATH = "/api/generation-plan/dispatch";

export type PlanArtifact = { role?: string; sha256?: string };

export type GenerationPlanFields = {
  request_text?: string;
  target_identity?: string;
  hotspot_assumption?: string;
  filter_policy?: string;
  binder_length?: number;
  seed?: number;
  cpu_count?: number;
  memory_mib?: number;
  gpu?: string;
  execution_deadline_seconds?: number;
  input_hashes_verified_against_files?: boolean;
  input_artifacts?: PlanArtifact[];
  stated_input_artifacts?: PlanArtifact[];
};

export type GenerationPlan = {
  plan_id: string;
  revision: number;
  digest: string;
  approved: boolean;
  status?: string;
  conversation_id?: string;
  authorization_bound?: boolean;
  plan_approval_grants_execution?: boolean;
  questions?: { field: string; question: string }[];
  blockers?: string[];
  execution_connected: false;
  historical_manual_attempt?: string;
  fields?: GenerationPlanFields;
};

export function readStructuredPlan(value: unknown): GenerationPlan | null {
  if (value === null || typeof value !== "object") {
    return null;
  }
  const plan = value as Partial<GenerationPlan>;
  if (typeof plan.plan_id !== "string" || plan.plan_id.length === 0) {
    return null;
  }
  if (typeof plan.revision !== "number") {
    return null;
  }
  if (typeof plan.approved !== "boolean" || plan.execution_connected !== false) {
    return null;
  }
  return plan as GenerationPlan;
}

async function postPlan(path: string, body: Record<string, unknown>): Promise<GenerationPlan | null> {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return readStructuredPlan(await response.json());
  } catch {
    return null;
  }
}

export function recordPlanApproval(
  plan: GenerationPlan,
  reviewerId: string,
  reason: string,
): Promise<GenerationPlan | null> {
  return postPlan(GENERATION_DECISION_PATH, {
    plan_id: plan.plan_id,
    revision: plan.revision,
    reviewer_id: reviewerId,
    reason,
    idempotency_key: `approval_${plan.plan_id}_${plan.revision}`,
  });
}

export function bindPlanAuthorization(
  plan: GenerationPlan,
  authorizationId: string,
  reviewerId: string,
  reason: string,
): Promise<GenerationPlan | null> {
  return postPlan(GENERATION_AUTHORIZATION_PATH, {
    plan_id: plan.plan_id,
    revision: plan.revision,
    authorization_id: authorizationId,
    reviewer_id: reviewerId,
    reason,
    idempotency_key: `authorization_${plan.plan_id}_${plan.revision}_${authorizationId}`,
  });
}

export function dispatchPlan(plan: GenerationPlan, attempt: number): Promise<GenerationPlan | null> {
  return postPlan(GENERATION_DISPATCH_PATH, {
    plan_id: plan.plan_id,
    revision: plan.revision,
    idempotency_key: `dispatch_${plan.plan_id}_${plan.revision}_${attempt}`,
  });
}
