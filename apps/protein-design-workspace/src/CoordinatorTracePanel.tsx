import { useEffect, useState } from "react";

export type CoordinatorTraceStep = {
  tool: string | null;
  specialist_role: string;
  execution: string;
  agent_executed: boolean;
  status: string;
  summary: string | null;
  resolved_attempt_id?: string | null;
  evidence_references?: Array<{
    tool?: string;
    status?: string;
    resolved_attempt_id?: string | null;
  }>;
  rejected_output?: string | null;
};

export type CoordinatorTrace = {
  trace_id: string;
  source_query: string;
  model?: { provider?: string; name?: string } | null;
  model_calls?: number;
  usage?: {
    reported_calls?: number;
    total_tokens?: number | null;
    input_tokens?: number | null;
    output_tokens?: number | null;
  } | null;
  limits?: {
    max_model_calls?: number;
    max_output_tokens_per_call?: number;
    token_budget?: number | null;
    call_timeout_seconds?: number;
    retries?: number;
    output_token_limit_is_per_call?: boolean;
  } | null;
  validation?: { status?: string; findings?: Array<{ code?: string; value?: string }> } | null;
  termination?: { status?: string; stop?: string } | null;
  conclusion_accepted?: boolean;
  synthesis?: string | null;
  rejected_output?: string | null;
  limitations?: string[];
  steps?: CoordinatorTraceStep[];
  agent_executed?: boolean;
};

type Load =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "empty"; limitations: string[] }
  | { state: "error" }
  | { state: "ready"; traces: CoordinatorTrace[]; limitations: string[] };

export function CoordinatorTracePanel({
  requestId,
  requestRevision,
}: {
  requestId: string | null;
  requestRevision: number | null;
}) {
  const [load, setLoad] = useState<Load>({ state: "idle" });

  useEffect(() => {
    if (!requestId || requestRevision == null) {
      setLoad({ state: "idle" });
      return;
    }
    let cancelled = false;
    setLoad({ state: "loading" });
    const url =
      `/api/attempt-coordinator?request_id=${encodeURIComponent(requestId)}` +
      `&request_revision=${requestRevision}`;
    void (async () => {
      try {
        const response = await fetch(url);
        const body = (await response.json()) as { traces?: unknown; limitations?: unknown };
        if (!response.ok) {
          if (!cancelled) {
            setLoad({ state: "error" });
          }
          return;
        }
        const traces = Array.isArray(body.traces) ? body.traces.filter(isTrace) : [];
        const limitations = Array.isArray(body.limitations)
          ? body.limitations.filter((item): item is string => typeof item === "string")
          : [];
        if (!cancelled) {
          setLoad(
            traces.length === 0 ? { state: "empty", limitations } : { state: "ready", traces, limitations },
          );
        }
      } catch {
        if (!cancelled) {
          setLoad({ state: "error" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestId, requestRevision]);

  if (load.state === "idle") {
    return null;
  }

  return (
    <section aria-label="Coordinator trace">
      <h3>Coordinator trace</h3>
      <p>
        Recorded events for this request revision. Each tool is a Python function. This panel does not
        call a language model.
      </p>
      {load.state === "loading" ? <p>Loading the coordinator trace.</p> : null}
      {load.state === "error" ? <p role="alert">The coordinator trace could not be read.</p> : null}
      {load.state === "empty" ? (
        <>
          <p>No coordinator trace is recorded for this request.</p>
          <LimitationList items={load.limitations} />
        </>
      ) : null}
      {load.state === "ready"
        ? load.traces.map((trace) => (
            <TraceCard key={trace.trace_id} trace={trace} limitations={load.limitations} />
          ))
        : null}
    </section>
  );
}

function TraceCard({ trace, limitations }: { trace: CoordinatorTrace; limitations: string[] }) {
  const notes = trace.limitations && trace.limitations.length > 0 ? trace.limitations : limitations;
  const accepted = trace.conclusion_accepted === true && typeof trace.synthesis === "string";
  return (
    <article aria-label={`Coordinator trace ${trace.trace_id}`}>
      <h4>Task</h4>
      <p>{trace.source_query}</p>
      <p>
        Model: {trace.model?.provider ?? "not configured"} / {trace.model?.name ?? "not configured"}.
        Calls: {trace.model_calls ?? 0}. Termination: {trace.termination?.status ?? "not recorded"} (
        {trace.termination?.stop ?? "not recorded"}).
      </p>
      <p>
        Provider-reported tokens: {tokenText(trace.usage?.total_tokens)} total,{" "}
        {tokenText(trace.usage?.input_tokens)} input, {tokenText(trace.usage?.output_tokens)} output.
        Reported calls with usage: {trace.usage?.reported_calls ?? 0}.
      </p>
      <p>{limitText(trace.limits)}</p>
      <ol aria-label="Coordinator events">
        {(trace.steps ?? []).map((step, index) => (
          <li key={`${trace.trace_id}-${index}`}>
            {step.execution === "language_model_synthesis" ? "Coordinator synthesis" : "Python function"}{" "}
            {step.tool ?? "none"}. Role label {step.specialist_role}. Status {step.status}.
            {step.summary ? ` ${step.summary}` : ""}
            {step.resolved_attempt_id ? ` Resolved attempt ${step.resolved_attempt_id}.` : ""}
            <EvidenceList references={step.evidence_references ?? []} />
          </li>
        ))}
      </ol>
      <h4>Harness validation</h4>
      <p>
        {trace.validation?.status ?? "not recorded"}.
        {(trace.validation?.findings ?? [])
          .filter((item) => item.code)
          .map((item) => ` ${item.code}${item.value ? `: ${item.value}` : ""}.`)}
      </p>
      {accepted ? (
        <>
          <h4>Synthesis</h4>
          <p>{trace.synthesis}</p>
        </>
      ) : (
        <>
          <h4>Rejected output</h4>
          <p>{trace.rejected_output ?? "No conclusion was accepted."}</p>
        </>
      )}
      <LimitationList items={notes} />
      <p>Specialist agent executed: {String(trace.agent_executed === true)}.</p>
    </article>
  );
}

function EvidenceList({
  references,
}: {
  references: NonNullable<CoordinatorTraceStep["evidence_references"]>;
}) {
  if (references.length === 0) {
    return null;
  }
  return (
    <ul>
      {references.map((reference, index) => (
        <li key={`${reference.tool ?? "evidence"}-${index}`}>
          Evidence {reference.tool ?? "tool"} status {reference.status ?? "not recorded"}
          {reference.resolved_attempt_id ? ` for ${reference.resolved_attempt_id}` : ""}.
        </li>
      ))}
    </ul>
  );
}

function LimitationList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <>
      <h4>Validation limits</h4>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </>
  );
}

function tokenText(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "not reported";
}

function limitText(limits: CoordinatorTrace["limits"]): string {
  if (!limits) {
    return "Loop limits were not recorded.";
  }
  const budget = limits.token_budget == null ? "not configured" : String(limits.token_budget);
  return (
    `Budget: ${limits.max_model_calls ?? "not recorded"} model calls, ` +
    `${limits.max_output_tokens_per_call ?? "not recorded"} output tokens per call, ` +
    `token budget ${budget}, timeout ${limits.call_timeout_seconds ?? "not recorded"} seconds, ` +
    `${limits.retries ?? 0} retries. ` +
    "The output token limit applies to each model call. It is not a total cost ceiling. " +
    "The loop stops when provider-reported total tokens exceed the configured budget, " +
    "when the model-call limit is reached, when a call fails, or when the provider reports HTTP 402."
  );
}

function isTrace(value: unknown): value is CoordinatorTrace {
  if (!value || typeof value !== "object") {
    return false;
  }
  const trace = value as Partial<CoordinatorTrace>;
  return typeof trace.trace_id === "string" && typeof trace.source_query === "string";
}
