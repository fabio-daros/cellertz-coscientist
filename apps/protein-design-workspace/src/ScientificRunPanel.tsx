import { useEffect, useRef, useState } from "react";
import type { ExecutionBudget, DesignDraftReply } from "./contracts";
import type { GateReply } from "./approval";
import type { ApprovedRunReply } from "./approved-run";
import type { ReadinessReply } from "./scientific-runner-readiness";
import {
  CONSUMED_TEXT,
  authorizationReviewReady,
  readScientificAuthorization,
  recordMatchesSummary,
  type AuthorizationRecord,
} from "./scientific-authorization";
import {
  ARTIFACT_TEXT,
  COMPLETED_TEXT,
  WORKFLOW_STAGE_IDS,
  createScientificRun,
  listScientificRuns,
  readScientificRun,
  readWorkflowTrace,
  retryScientificRun,
  selectScientificRun,
  type ScientificRun,
  type WorkflowTrace,
} from "./scientific-run";

const REJECTED = "The scientific run could not be recorded. No scientific action was taken.";

export function ScientificRunPanel({
  authorization,
  draftReply,
  decisionReply,
  executionReply,
  runReply,
  readiness,
  budget,
  onConsumed,
}: {
  authorization: AuthorizationRecord | null;
  draftReply: DesignDraftReply | null;
  decisionReply: GateReply | null;
  executionReply: GateReply | null;
  runReply: ApprovedRunReply | null;
  readiness: ReadinessReply | null;
  budget: ExecutionBudget;
  onConsumed?: (record: AuthorizationRecord) => void;
}) {
  const summary = authorizationReviewReady({
    draftReply,
    decisionReply,
    executionReply,
    runReply,
    readiness,
    budget,
  });
  const [active, setActive] = useState<ScientificRun | null>(null);
  const [history, setHistory] = useState<ScientificRun[]>([]);
  const [inspected, setInspected] = useState<ScientificRun | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "loading">("idle");
  const [trace, setTrace] = useState<WorkflowTrace | null>(null);
  const operation = useRef(0);
  const traceTicket = useRef(0);
  const syncTicket = useRef(0);
  const syncPending = useRef(false);
  const busy = useRef(false);
  const activeRef = useRef<ScientificRun | null>(null);
  const authorizationId = authorization?.authorization_id ?? "";
  const fingerprint = bindingFingerprint(authorization);
  const fingerprintRef = useRef(fingerprint);
  fingerprintRef.current = fingerprint;
  const bound =
    authorization !== null &&
    summary !== null &&
    recordMatchesSummary(authorization, summary) &&
    authorization.authorization_id === authorizationId;
  const owned =
    bound && active !== null && sameBinding(active, authorization) ? active : null;
  const canStart =
    bound &&
    authorization.execution_authorized === true &&
    authorization.status === "authorized" &&
    owned === null;
  const canRetry = owned?.status === "failed";

  useEffect(() => {
    const generation = operation.current + 1;
    operation.current = generation;
    traceTicket.current += 1;
    syncTicket.current += 1;
    syncPending.current = false;
    busy.current = false;
    activeRef.current = null;
    setActive(null);
    setInspected(null);
    setHistory([]);
    setErrorText(null);
    setTrace(null);
    setPhase("idle");
    const current = authorization;
    if (!current || !summary || !recordMatchesSummary(current, summary)) {
      return;
    }
    let cancelled = false;
    void (async () => {
      if (busy.current || operation.current !== generation) {
        return;
      }
      busy.current = true;
      setPhase("loading");
      try {
        const listed = await listScientificRuns();
        if (cancelled || operation.current !== generation) {
          return;
        }
        const match = listed.runs.find((run) => sameBinding(run, current));
        if (match) {
          activeRef.current = match;
          setActive(match);
          requestConsumed(current.authorization_id);
        }
      } catch {
        if (!cancelled && operation.current === generation) {
          setErrorText(REJECTED);
        }
      } finally {
        if (operation.current === generation) {
          busy.current = false;
          setPhase("idle");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fingerprint]);

  useEffect(() => {
    if (!owned || (owned.status !== "queued" && owned.status !== "running")) {
      return;
    }
    const timer = window.setInterval(() => {
      void refresh(owned.run_id, owned.authorization_id, owned.request_revision);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [owned?.run_id, owned?.status, owned?.attempt]);

  const traceArtifactId =
    owned?.status === "completed"
      ? (owned.artifacts.find((item) => item.kind === "workflow_trace" && item.attempt === owned.attempt)
          ?.artifact_id ?? "")
      : "";

  useEffect(() => {
    if (!owned || traceArtifactId === "") {
      return;
    }
    const ticket = traceTicket.current + 1;
    traceTicket.current = ticket;
    const run = owned;
    let cancelled = false;
    void readWorkflowTrace(run, traceArtifactId).then((parsed) => {
      if (cancelled || traceTicket.current !== ticket) {
        return;
      }
      if (
        !parsed ||
        parsed.run_id !== run.run_id ||
        parsed.attempt !== run.attempt ||
        parsed.session_id !== run.session_id
      ) {
        setTrace(null);
        setErrorText(ARTIFACT_TEXT);
        return;
      }
      setTrace(parsed);
    });
    return () => {
      cancelled = true;
    };
  }, [traceArtifactId, owned?.run_id, owned?.attempt, owned?.session_id]);

  function begin(): number | null {
    if (busy.current) {
      return null;
    }
    busy.current = true;
    const next = operation.current + 1;
    operation.current = next;
    setPhase("loading");
    return next;
  }

  function finish(id: number): boolean {
    if (operation.current !== id) {
      return false;
    }
    busy.current = false;
    setPhase("idle");
    return true;
  }

  async function start() {
    if (!canStart || !authorization) {
      return;
    }
    const authorizationId = authorization.authorization_id;
    const revision = authorization.request_revision;
    const id = begin();
    if (id === null) {
      return;
    }
    try {
      const reply = await createScientificRun(authorizationId, `scientific-run-${authorizationId}`);
      const record = selectScientificRun(activeRef.current, reply.run, id, operation.current, {
        authorizationId,
        revision,
      });
      if (!finish(id)) {
        return;
      }
      if (reply.status === "idempotency_conflict") {
        setErrorText(reply.text);
        return;
      }
      if (
        !record ||
        !sameBinding(record, authorization) ||
        (reply.status !== "run_queued" && reply.status !== "run_observed")
      ) {
        setErrorText(REJECTED);
        return;
      }
      activeRef.current = record;
      setActive(record);
      setErrorText(null);
      requestConsumed(authorizationId);
    } catch {
      if (finish(id)) {
        setErrorText(REJECTED);
      }
    }
  }

  async function refresh(runId: string, authorizationId: string, revision: number) {
    const id = begin();
    if (id === null) {
      return;
    }
    try {
      const reply = await readScientificRun(runId);
      const record = selectScientificRun(activeRef.current, reply.run, id, operation.current, {
        authorizationId,
        revision,
      });
      if (!finish(id)) {
        return;
      }
      if (!record || record.run_id !== runId) {
        return;
      }
      activeRef.current = record;
      setActive(record);
      requestConsumed(authorizationId);
    } catch {
      finish(id);
    }
  }

  async function retry() {
    if (!canRetry || !owned || !authorization) {
      return;
    }
    const runId = owned.run_id;
    const authorizationId = authorization.authorization_id;
    const revision = authorization.request_revision;
    const id = begin();
    if (id === null) {
      return;
    }
    try {
      const reply = await retryScientificRun(runId, `scientific-retry-${runId}-${owned.attempt}`);
      const record = selectScientificRun(activeRef.current, reply.run, id, operation.current, {
        authorizationId,
        revision,
      });
      if (!finish(id)) {
        return;
      }
      if (reply.status === "retry_not_allowed" && record && record.run_id === runId && sameBinding(record, authorization)) {
        activeRef.current = record;
        setActive(record);
        setErrorText(reply.text);
        return;
      }
      if (!record || record.run_id !== runId || record.attempt !== owned.attempt + 1) {
        setErrorText(REJECTED);
        return;
      }
      activeRef.current = record;
      setActive(record);
      setErrorText(null);
    } catch {
      if (finish(id)) {
        setErrorText(REJECTED);
      }
    }
  }

  async function loadHistory() {
    const id = begin();
    if (id === null) {
      return;
    }
    try {
      const reply = await listScientificRuns();
      if (!finish(id)) {
        return;
      }
      setHistory(reply.runs);
    } catch {
      if (finish(id)) {
        setErrorText(REJECTED);
      }
    }
  }

  function requestConsumed(id: string) {
    if (!onConsumed || syncPending.current || authorization?.execution_authorized === false) {
      return;
    }
    const ticket = syncTicket.current;
    const seen = fingerprintRef.current;
    syncPending.current = true;
    void (async () => {
      try {
        const fresh = await readScientificAuthorization(id);
        if (syncTicket.current !== ticket || fingerprintRef.current !== seen) {
          return;
        }
        const record = fresh.authorization;
        if (
          !record ||
          record.authorization_id !== id ||
          bindingFingerprint(record) !== seen ||
          record.execution_authorized !== false ||
          record.text !== CONSUMED_TEXT
        ) {
          return;
        }
        onConsumed(record);
      } catch {
        return;
      } finally {
        if (syncTicket.current === ticket) {
          syncPending.current = false;
        }
      }
    })();
  }

  function inspect(run: ScientificRun) {
    if (busy.current) {
      return;
    }
    setInspected(run);
  }

  const visible = owned;
  const loading = phase === "loading";

  return (
    <section className="scientific-run" aria-label="Scientific run" aria-busy={loading}>
      <h3>Scientific run</h3>
      <p>Authorization is not execution.</p>
      <p>A completed scientific run is not scientific validation.</p>
      <p>CD22 and TACI remain blocked.</p>
      <section aria-label="Governed agent workflow">
        <h4>Governed agent workflow</h4>
        <p>Deterministic prototype runtime.</p>
        <p>Operational orchestration evidence.</p>
        <p>This governed workflow is not scientific validation.</p>
        {visible?.scientific_candidate_generated ? null : <p>No scientific candidate generated.</p>}
        <ol className="agent-workflow">
          {WORKFLOW_STAGE_IDS.map((role, index) => {
            const stage =
              trace !== null &&
              visible !== null &&
              trace.run_id === visible.run_id &&
              trace.attempt === visible.attempt
                ? trace.stages.find((item) => item.stage_id === role)
                : undefined;
            return (
              <li key={role}>
                <p>{role}</p>
                <p>{stage ? stage.status : "Not observed"}</p>
                <p>{stage ? stage.sequence : index + 1}</p>
                {stage ? (
                  <>
                    <p>{stage.summary}</p>
                    <p>{stage.input_digest.slice(0, 12)}</p>
                    <p>{stage.output_digest.slice(0, 12)}</p>
                    <p>{stage.implementation_version}</p>
                    <p>{stage.attempt}</p>
                  </>
                ) : null}
              </li>
            );
          })}
        </ol>
      </section>
      {canStart ? (
        <button type="button" onClick={start} disabled={loading}>
          Start scientific run
        </button>
      ) : null}
      {visible ? <RunFacts run={visible} /> : null}
      {canRetry ? (
        <button type="button" onClick={retry} disabled={loading}>
          Retry failed scientific run
        </button>
      ) : null}
      {errorText ? <p>{errorText}</p> : null}
      <button type="button" onClick={loadHistory} disabled={loading}>
        Scientific run history
      </button>
      {history.length > 0 ? (
        <ul>
          {history.map((run) => (
            <li key={run.run_id}>
              <button type="button" onClick={() => inspect(run)}>
                {run.run_id}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {inspected ? (
        <section aria-label="Historical scientific run">
          <h4>Historical scientific run</h4>
          <RunFacts run={inspected} />
          <button type="button" onClick={() => setInspected(null)}>
            Close historical scientific run
          </button>
        </section>
      ) : null}
    </section>
  );
}

function bindingFingerprint(authorization: AuthorizationRecord | null): string {
  if (!authorization) {
    return "";
  }
  return [
    authorization.session_id,
    authorization.authorization_id,
    authorization.request_id,
    String(authorization.request_revision),
    authorization.approval_id,
    authorization.request_digest,
    authorization.readiness_observation_id,
    authorization.readiness_observed_at,
    authorization.profile_id,
    authorization.tool_name,
    authorization.tool_version,
    authorization.runtime_name,
    authorization.runtime_version,
    authorization.image_digest,
    authorization.manifest_digest,
    authorization.target_scope,
    authorization.budget.action_on_limit,
    String(authorization.budget.authorizes_execution),
    JSON.stringify(authorization.budget.currency),
    String(authorization.budget.human_override_required),
    JSON.stringify(authorization.budget.model_cost_ceiling),
    JSON.stringify(authorization.budget.scientific_tool_cost_ceiling),
    JSON.stringify(authorization.budget.token_ceiling),
    JSON.stringify(authorization.budget.warning_threshold),
  ].join("\n");
}

function sameBinding(run: ScientificRun, authorization: AuthorizationRecord): boolean {
  return (
    run.session_id === authorization.session_id &&
    run.authorization_id === authorization.authorization_id &&
    run.request_id === authorization.request_id &&
    run.request_revision === authorization.request_revision &&
    run.approval_id === authorization.approval_id &&
    run.request_digest === authorization.request_digest &&
    run.readiness_observation_id === authorization.readiness_observation_id &&
    run.readiness_observed_at === authorization.readiness_observed_at &&
    run.profile_id === authorization.profile_id &&
    run.tool_name === authorization.tool_name &&
    run.tool_version === authorization.tool_version &&
    run.runtime_name === authorization.runtime_name &&
    run.runtime_version === authorization.runtime_version &&
    run.image_digest === authorization.image_digest &&
    run.manifest_digest === authorization.manifest_digest &&
    run.target_scope === authorization.target_scope &&
    run.budget.action_on_limit === authorization.budget.action_on_limit &&
    run.budget.authorizes_execution === authorization.budget.authorizes_execution &&
    run.budget.currency === authorization.budget.currency &&
    run.budget.human_override_required === authorization.budget.human_override_required &&
    run.budget.model_cost_ceiling === authorization.budget.model_cost_ceiling &&
    run.budget.scientific_tool_cost_ceiling === authorization.budget.scientific_tool_cost_ceiling &&
    run.budget.token_ceiling === authorization.budget.token_ceiling &&
    run.budget.warning_threshold === authorization.budget.warning_threshold
  );
}

function RunFacts({ run }: { run: ScientificRun }) {
  return (
    <dl>
      <div>
        <dt>Status</dt>
        <dd>{run.status}</dd>
      </div>
      <div>
        <dt>Attempt</dt>
        <dd>{run.attempt}</dd>
      </div>
      <div>
        <dt>Text</dt>
        <dd>{run.text}</dd>
      </div>
      <div>
        <dt>Scientific validation</dt>
        <dd>{run.scientific_validation ? "true" : "false"}</dd>
      </div>
      {run.status === "completed" ? (
        <div>
          <dt>Completion</dt>
          <dd>{COMPLETED_TEXT}</dd>
        </div>
      ) : null}
      {run.artifacts.map((artifact) => (
        <div key={artifact.artifact_id}>
          <dt>{artifact.kind}</dt>
          <dd>
            attempt {artifact.attempt}, {artifact.size} bytes
          </dd>
        </div>
      ))}
    </dl>
  );
}
