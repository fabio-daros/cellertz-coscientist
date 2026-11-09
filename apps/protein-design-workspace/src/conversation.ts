import { type ConversationAsk, type ConversationReply } from "./contracts";

export const CONVERSATION_PATH = "/api/conversation";

export function failedReply(sessionId: string, runId: string | null): ConversationReply {
  return {
    status: "adapter_failure",
    text: "The conversational request failed. No scientific action was taken.",
    session_id: sessionId,
    run_id: runId,
    read_only: true,
    evidence: [],
    instrumentation: {
      interaction_id: "unknown",
      recorded: false,
      total_tokens: null,
      observation: "adapter_failure",
      event: null,
    },
  };
}

export async function askReadOnly(input: ConversationAsk): Promise<ConversationReply> {
  const response = await fetch(CONVERSATION_PATH, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: input.session_id,
      question: input.question,
      run_id: input.run_id,
      candidate_id: input.candidate_id,
    }),
  });
  if (!response.ok) {
    return failedReply(input.session_id, input.run_id);
  }
  try {
    const body = (await response.json()) as ConversationReply;
    if (body.read_only !== true || typeof body.text !== "string") {
      return failedReply(input.session_id, input.run_id);
    }
    return body;
  } catch {
    return failedReply(input.session_id, input.run_id);
  }
}
