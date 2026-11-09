import { useEffect, useRef, useState } from "react";
import { type GateReply } from "./approval";
import {
  type ApprovedRunListReply,
  type ApprovedRunRecord,
  type ApprovedRunReply,
  type ApprovedRunSummary,
  type ArtifactSelection,
  type ArtifactView,
  listApprovedRuns,
  readApprovedArtifact,
  selectArtifactView,
  selectRunHistory,
} from "./approved-run";

function money(value: number | null | undefined): string {
  return value === null || value === undefined ? "Not recorded" : String(value);
}

type ArtifactPlace = "active" | "historical";

function attemptArtifacts(record: ApprovedRunRecord | null) {
  if (!record) {
    return [];
  }
  return record.artifacts.filter(
    (artifact) => artifact.artifact_id === `artifact_${record.run_id}_${record.attempt}_${artifact.kind}`,
  );
}

export function FixtureRunPanel({
  draft,
  execution,
  runReply,
  busy,
  onCreate,
  onRefresh,
  onRetry,
  onOpenRun,
  inspectedReply,
  onCloseInspection,
}: {
  draft: { request_id: string; revision: number } | null;
  execution: GateReply | null;
  runReply: ApprovedRunReply | null;
  busy: boolean;
  onCreate: () => void;
  onRefresh: () => void;
  onRetry: () => void;
  onOpenRun: (summary: ApprovedRunSummary) => void;
  inspectedReply: ApprovedRunReply | null;
  onCloseInspection: () => void;
}) {
  const approval = execution?.approval ?? null;
  const demonstration = execution?.demonstration ?? null;
  const currentDemonstration =
    execution?.status === "demonstration_completed" &&
    approval &&
    demonstration &&
    draft &&
    approval.request_id === draft.request_id &&
    approval.request_revision === draft.revision
      ? demonstration
      : null;
  const run =
    runReply?.run &&
    draft &&
    runReply.run.request_id === draft.request_id &&
    runReply.run.request_revision === draft.revision
      ? runReply.run
      : null;
  const events = [...(run?.events ?? [])].sort((left, right) => left.sequence - right.sequence);
  const failed = run?.status === "failed";
  const inspected = inspectedReply?.run ?? null;
  const artifacts = attemptArtifacts(run);
  const historicalArtifacts = attemptArtifacts(inspected);
  const historyGeneration = useRef(0);
  const artifactGeneration = useRef(0);
  const artifactSelection = useRef<ArtifactSelection | null>(null);
  const artifactPlace = useRef<ArtifactPlace | null>(null);
  const visibleRun = useRef(run);
  const inspectedRun = useRef(inspected);
  visibleRun.current = run;
  inspectedRun.current = inspected;
  const [history, setHistory] = useState<ApprovedRunListReply | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [artifactView, setArtifactView] = useState<ArtifactView | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [artifactFeedback, setArtifactFeedback] = useState<ArtifactPlace | null>(null);
  useEffect(() => {
    artifactGeneration.current += 1;
    artifactSelection.current = null;
    artifactPlace.current = null;
    setArtifactFeedback(null);
    setArtifactLoading(false);
    setArtifactError(null);
    setArtifactView((current) => {
      if (!current) {
        return null;
      }
      const matchesActive = run && current.run_id === run.run_id && current.attempt === run.attempt;
      const matchesInspected =
        inspected && current.run_id === inspected.run_id && current.attempt === inspected.attempt;
      return matchesActive || matchesInspected ? current : null;
    });
  }, [run?.run_id, run?.attempt, inspected?.run_id, inspected?.attempt]);

  async function refreshHistory() {
    const operation = historyGeneration.current + 1;
    historyGeneration.current = operation;
    setHistoryLoading(true);
    try {
      const incoming = await listApprovedRuns();
      if (historyGeneration.current !== operation) {
        return;
      }
      if (incoming.status !== "runs_observed") {
        setHistoryError(incoming.text);
        return;
      }
      setHistoryError(null);
      setHistory((current) => selectRunHistory(current, incoming, operation, historyGeneration.current));
    } catch {
      if (historyGeneration.current === operation) {
        setHistoryError("The fixture run history could not be read.");
      }
    } finally {
      if (historyGeneration.current === operation) {
        setHistoryLoading(false);
      }
    }
  }

  function sourceStillVisible(place: ArtifactPlace, requested: ArtifactSelection): boolean {
    const current = place === "historical" ? inspectedRun.current : visibleRun.current;
    return current?.run_id === requested.run_id && current?.attempt === requested.attempt;
  }

  function operationStillCurrent(operation: number, place: ArtifactPlace, requested: ArtifactSelection): boolean {
    const selected = artifactSelection.current;
    return (
      artifactGeneration.current === operation &&
      artifactPlace.current === place &&
      selected?.run_id === requested.run_id &&
      selected?.artifact_id === requested.artifact_id &&
      selected?.attempt === requested.attempt &&
      sourceStillVisible(place, requested)
    );
  }

  async function viewArtifact(source: ApprovedRunRecord, artifactId: string, place: ArtifactPlace) {
    if (source.status !== "completed") {
      return;
    }
    const requested = { run_id: source.run_id, artifact_id: artifactId, attempt: source.attempt };
    artifactSelection.current = requested;
    artifactPlace.current = place;
    const operation = artifactGeneration.current + 1;
    artifactGeneration.current = operation;
    setArtifactFeedback(place);
    setArtifactView(null);
    setArtifactLoading(true);
    setArtifactError(null);
    try {
      const result = await readApprovedArtifact(source.run_id, artifactId);
      if (!operationStillCurrent(operation, place, requested)) {
        return;
      }
      if (!result.ok) {
        setArtifactError(result.text);
        return;
      }
      setArtifactView((current) =>
        selectArtifactView(
          current,
          { ...requested, text: result.text },
          operation,
          artifactGeneration.current,
          artifactSelection.current,
        ),
      );
    } catch {
      if (operationStillCurrent(operation, place, requested)) {
        setArtifactError("The operational artifact could not be read.");
      }
    } finally {
      if (artifactGeneration.current === operation) {
        setArtifactLoading(false);
      }
    }
  }

  async function downloadArtifact(source: ApprovedRunRecord, artifactId: string, place: ArtifactPlace) {
    if (source.status !== "completed") {
      return;
    }
    const requested = { run_id: source.run_id, artifact_id: artifactId, attempt: source.attempt };
    artifactSelection.current = requested;
    artifactPlace.current = place;
    const operation = artifactGeneration.current + 1;
    artifactGeneration.current = operation;
    setArtifactFeedback(place);
    setArtifactLoading(true);
    setArtifactError(null);
    try {
      const result = await readApprovedArtifact(source.run_id, artifactId);
      if (!operationStillCurrent(operation, place, requested)) {
        return;
      }
      if (!result.ok) {
        setArtifactError(result.text);
        return;
      }
      const url = URL.createObjectURL(new Blob([result.text], { type: result.mediaType }));
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      if (operationStillCurrent(operation, place, requested)) {
        setArtifactError("The operational artifact could not be read.");
      }
    } finally {
      if (artifactGeneration.current === operation) {
        setArtifactLoading(false);
      }
    }
  }

  return (
    <section aria-label="Fixture run">
      <h3>Fixture-only run</h3>
      <p>Not scientific validation.</p>
      <p>No candidate protein was generated.</p>
      <p>An operational fixture lifecycle is separate from the demonstration receipt.</p>
      <section aria-label="Run history">
        <h4>Run history</h4>
        <button type="button" onClick={() => void refreshHistory()} disabled={historyLoading || busy}>
          Refresh run history
        </button>
        {historyLoading ? <p role="status">Loading fixture run history.</p> : null}
        {historyError ? <p role="alert">{historyError}</p> : null}
        {history && history.runs.length === 0 ? <p>No fixture runs are recorded for this session.</p> : null}
        {history && history.runs.length > 0 ? (
          <ul aria-label="Fixture runs">
            {history.runs.map((summary) => (
              <li key={summary.run_id}>
                <button type="button" onClick={() => onOpenRun(summary)} disabled={busy || historyLoading}>
                  Open {summary.run_id}
                </button>
                <span>
                  {" "}
                  {summary.status}. Attempt {summary.attempt}. {summary.text} Operational artifacts{" "}
                  {summary.artifact_count}. Fixture-only run. Not scientific validation.
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {!history && !historyLoading && !historyError ? <p>Refresh run history to load runs.</p> : null}
      </section>
      {inspectedReply ? (
        <div role="region" aria-label="Historical run">
          <button type="button" onClick={onCloseInspection}>
            Return to current run
          </button>
          {inspected ? (
            <>
              <p>Fixture-only run. Not scientific validation.</p>
              <dl>
                <div>
                  <dt>Run ID</dt>
                  <dd>{inspected.run_id}</dd>
                </div>
                <div>
                  <dt>Request</dt>
                  <dd>
                    {inspected.request_id} revision {inspected.request_revision}
                  </dd>
                </div>
                <div>
                  <dt>State</dt>
                  <dd>{inspected.status}</dd>
                </div>
              </dl>
              <ul aria-label="Historical artifacts">
                {historicalArtifacts.map((artifact) => (
                  <li key={artifact.artifact_id}>
                    {artifact.kind}. {artifact.label}. Operational metadata. Not scientific evidence.
                    <button
                      type="button"
                      onClick={() => void viewArtifact(inspected, artifact.artifact_id, "historical")}
                      disabled={inspected.status !== "completed"}
                    >
                      View {artifact.kind}
                    </button>
                    <button
                      type="button"
                      onClick={() => void downloadArtifact(inspected, artifact.artifact_id, "historical")}
                      disabled={inspected.status !== "completed"}
                    >
                      Download {artifact.kind}
                    </button>
                  </li>
                ))}
              </ul>
              {artifactFeedback === "historical" && artifactLoading ? (
                <p role="status">Loading operational artifact.</p>
              ) : null}
              {artifactFeedback === "historical" && artifactError ? <p role="alert">{artifactError}</p> : null}
              {artifactView &&
              artifactView.run_id === inspected.run_id &&
              artifactView.attempt === inspected.attempt &&
              historicalArtifacts.some((artifact) => artifact.artifact_id === artifactView.artifact_id) ? (
                <pre role="region" aria-label="Historical artifact">
                  {artifactView.text}
                </pre>
              ) : null}
            </>
          ) : (
            <p role="alert">{inspectedReply.text}</p>
          )}
        </div>
      ) : null}
      <button
        type="button"
        onClick={onCreate}
        disabled={currentDemonstration === null || busy || run !== null}
      >
        Create fixture run
      </button>
      {busy ? <p role="status">Waiting for the server.</p> : null}
      {runReply && !run ? <p role="alert">{runReply.text}</p> : null}
      {run ? (
        <div role="region" aria-label="Fixture run timeline">
          <p>Fixture-only run. Not scientific validation.</p>
          {run.status === "queued" || run.status === "running" ? (
            <p role="status">The server worker is processing this run. Status updates automatically.</p>
          ) : null}
          <dl>
            <div>
              <dt>Run ID</dt>
              <dd>{run.run_id}</dd>
            </div>
            <div>
              <dt>Request</dt>
              <dd>
                {run.request_id} revision {run.request_revision}
              </dd>
            </div>
            <div>
              <dt>Approval ID</dt>
              <dd>{run.approval_id}</dd>
            </div>
            <div>
              <dt>Attempt</dt>
              <dd>{run.attempt}</dd>
            </div>
            <div>
              <dt>State</dt>
              <dd>{run.status}</dd>
            </div>
            <div>
              <dt>Budget</dt>
              <dd>
                Token ceiling {money(run.budget.token_ceiling)}. Model cost ceiling{" "}
                {money(run.budget.model_cost_ceiling)}. A budget does not authorize execution.
              </dd>
            </div>
            <div>
              <dt>Usage</dt>
              <dd>
                Observed model calls {run.usage.observed_model_calls}. Observed scientific tool calls{" "}
                {run.usage.observed_scientific_tool_calls}. Estimated model cost{" "}
                {money(run.usage.estimated_model_cost)}.
              </dd>
            </div>
          </dl>
          <ol aria-label="Run events">
            {events.map((event) => (
              <li key={event.sequence}>
                {event.sequence}. {event.status}. Attempt {event.attempt}. {event.text}
              </li>
            ))}
          </ol>
          <ul aria-label="Operational artifacts">
            {artifacts.map((artifact) => (
              <li key={artifact.artifact_id}>
                {artifact.kind}. {artifact.label}. Operational metadata. Not scientific evidence.
                <button
                  type="button"
                  onClick={() => void viewArtifact(run, artifact.artifact_id, "active")}
                  disabled={busy || run.status !== "completed"}
                >
                  View {artifact.kind}
                </button>
                <button
                  type="button"
                  onClick={() => void downloadArtifact(run, artifact.artifact_id, "active")}
                  disabled={busy || run.status !== "completed"}
                >
                  Download {artifact.kind}
                </button>
              </li>
            ))}
          </ul>
          {artifactFeedback === "active" && artifactLoading ? (
            <p role="status">Loading operational artifact.</p>
          ) : null}
          {artifactFeedback === "active" && artifactError ? <p role="alert">{artifactError}</p> : null}
          {artifactView &&
          artifactView.run_id === run.run_id &&
          artifactView.attempt === run.attempt &&
          artifacts.some((artifact) => artifact.artifact_id === artifactView.artifact_id) ? (
            <pre role="region" aria-label="Operational artifact">
              {artifactView.text}
            </pre>
          ) : null}
          {failed ? <p role="alert">{runReply?.text}</p> : null}
          <div className="choices">
            <button type="button" onClick={onRefresh} disabled={busy}>
              Refresh fixture run
            </button>
            {failed ? (
              <button type="button" onClick={onRetry} disabled={busy}>
                Retry fixture run
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
