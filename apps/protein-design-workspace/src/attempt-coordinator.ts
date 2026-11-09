import type { GenerationPlan } from "./generation-plan";

export const ATTEMPT_COORDINATOR_PATH = "/api/attempt-coordinator";

export type CoordinatorStep = {
  specialist: string;
  mode: string;
  tool: string | null;
  summary: string;
  status?: string;
  arguments?: Record<string, unknown>;
  requested_id?: string | null;
  resolved_attempt_id?: string | null;
  evidence_references?: unknown[];
};

export type CoordinatorEvidence = {
  tool?: string;
  status?: string;
  arguments?: Record<string, unknown>;
  requested_id?: string | null;
  resolved_attempt_id?: string | null;
  references?: unknown[];
};

export type CoordinatorReply = {
  status?: string;
  text: string;
  attempt_id?: string | null;
  steps?: CoordinatorStep[];
  evidence?: CoordinatorEvidence[];
  plan?: GenerationPlan | null;
  conversation_id?: string;
  execution_connected?: boolean;
};

export async function askCoordinator(
  text: string,
  conversationId?: string | null,
): Promise<CoordinatorReply | null> {
  const body: { question: string; conversation_id?: string } = { question: text };
  if (conversationId) {
    body.conversation_id = conversationId;
  }
  try {
    const response = await fetch(ATTEMPT_COORDINATOR_PATH, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = (await response.json()) as CoordinatorReply;
    if (typeof payload.text !== "string") {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}
