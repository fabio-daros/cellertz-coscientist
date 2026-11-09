import { useState } from "react";
import { askCoordinator, type CoordinatorReply, type CoordinatorStep } from "./attempt-coordinator";

function stepsOf(reply: CoordinatorReply): CoordinatorStep[] {
  if (!Array.isArray(reply.steps)) {
    return [];
  }
  return reply.steps.filter(
    (step) =>
      step !== null &&
      typeof step === "object" &&
      typeof step.specialist === "string" &&
      typeof step.mode === "string" &&
      typeof step.summary === "string" &&
      (step.tool === null || typeof step.tool === "string"),
  );
}

export function RecordedResultsPanel({ onReply }: { onReply?: (reply: CoordinatorReply) => void }) {
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState<CoordinatorReply | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask() {
    const text = question.trim();
    if (!text || busy) {
      return;
    }
    setBusy(true);
    setNote("");
    setReply(null);
    const next = await askCoordinator(text, conversationId);
    setBusy(false);
    if (!next) {
      setNote("The coordinator request failed. No fixture answer was substituted.");
      return;
    }
    if (next.conversation_id) {
      setConversationId(next.conversation_id);
    }
    setReply(next);
    onReply?.(next);
  }

  const steps = reply ? stepsOf(reply) : [];

  return (
    <section aria-label="Ask about recorded results">
      <h3>Ask about recorded results</h3>
      <p>
        This question is sent to the professor coordinator. Recorded metrics and geometric
        contacts are calculated by deterministic tools. The language model writes the English
        answer from those tools. This does not approve a plan or start a run.
      </p>
      <label htmlFor="recorded-results-question">Question about recorded results</label>
      <textarea
        id="recorded-results-question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <button type="button" onClick={() => void ask()} disabled={busy}>
        Ask about recorded results
      </button>
      {busy ? <p role="status">Asking the coordinator.</p> : null}
      {note ? <p role="alert">{note}</p> : null}
      {reply ? (
        <div role="region" aria-label="Coordinator answer">
          <p>{reply.text}</p>
          <ol aria-label="Tool trace">
            {steps.map((step, index) => (
              <li key={`${step.tool ?? "answer"}-${index}`}>
                {step.specialist} · {step.mode}
                {step.tool ? ` · ${step.tool}` : ""}
                {step.status ? ` · ${step.status}` : ""}. {step.summary}
                {step.requested_id ? ` Requested ${step.requested_id}.` : ""}
                {step.resolved_attempt_id ? ` Resolved ${step.resolved_attempt_id}.` : ""}
                {step.arguments ? ` Arguments ${JSON.stringify(step.arguments)}.` : ""}
              </li>
            ))}
          </ol>
          {Array.isArray(reply.evidence) && reply.evidence.length > 0 ? (
            <ul aria-label="Evidence references">
              {reply.evidence.map((item, index) => (
                <li key={`${item.tool ?? "evidence"}-${index}`}>
                  {item.tool ?? "evidence"} · {item.status ?? "ok"}
                  {item.requested_id ? ` · requested ${item.requested_id}` : ""}
                  {item.resolved_attempt_id ? ` · resolved ${item.resolved_attempt_id}` : ""}
                  {item.arguments ? ` · ${JSON.stringify(item.arguments)}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
