import { useEffect, useRef, useState } from "react";
import type { ExecutionBudget, DesignDraftReply } from "./contracts";
import type { GateReply } from "./approval";
import type { ApprovedRunReply } from "./approved-run";
import type { ReadinessReply } from "./scientific-runner-readiness";
import {
  AUTHORIZED_TEXT,
  CONSUMED_TEXT,
  authorizationReviewReady,
  createScientificAuthorization,
  listScientificAuthorizations,
  readScientificAuthorization,
  recordMatchesSummary,
  revokeScientificAuthorization,
  selectAuthorization,
  selectAuthorizationHistory,
  type AuthorizationRecord,
  type AuthorizationSummary,
} from "./scientific-authorization";

const REJECTED = "Scientific authorization could not be recorded. No scientific action was taken.";

function ceiling(value: number | null): string {
  return value === null ? "Not recorded" : String(value);
}

export function ScientificAuthorizationPanel({
  draftReply,
  decisionReply,
  executionReply,
  runReply,
  readiness,
  budget,
  onCurrent,
  consumed,
}: {
  draftReply: DesignDraftReply | null;
  decisionReply: GateReply | null;
  executionReply: GateReply | null;
  runReply: ApprovedRunReply | null;
  readiness: ReadinessReply | null;
  budget: ExecutionBudget;
  onCurrent?: (record: AuthorizationRecord | null) => void;
  consumed?: AuthorizationRecord | null;
}) {
  const ready = authorizationReviewReady({
    draftReply,
    decisionReply,
    executionReply,
    runReply,
    readiness,
    budget,
  });
  const [summary, setSummary] = useState<AuthorizationSummary | null>(null);
  const [active, setActive] = useState<AuthorizationRecord | null>(null);
  const [history, setHistory] = useState<AuthorizationRecord[]>([]);
  const [inspected, setInspected] = useState<AuthorizationRecord | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "loading">("idle");
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const operation = useRef(0);
  const busy = useRef(false);
  const activeRef = useRef<AuthorizationRecord | null>(null);
  const historyRef = useRef<AuthorizationRecord[]>([]);
  const inspectedRef = useRef<AuthorizationRecord | null>(null);
  const summaryKey = ready ? JSON.stringify(ready) : "";

  useEffect(() => {
    setSummary(null);
  }, [summaryKey]);

  const current = active && ready && recordMatchesSummary(active, ready) ? active : null;
  const loading = phase === "loading";

  useEffect(() => {
    onCurrent?.(current);
  }, [current, onCurrent]);

  useEffect(() => {
    if (!consumed || consumed.text !== CONSUMED_TEXT || consumed.execution_authorized !== false) {
      return;
    }
    const visible = activeRef.current;
    if (!visible || visible.authorization_id !== consumed.authorization_id || visible.execution_authorized === false) {
      return;
    }
    activeRef.current = consumed;
    setActive(consumed);
  }, [consumed]);

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

  function review() {
    if (!ready || busy.current) {
      return;
    }
    setSummary(ready);
    setErrorText(null);
  }

  async function confirm() {
    if (!summary || busy.current) {
      return;
    }
    const reviewed = summary;
    const id = begin();
    if (id === null) {
      return;
    }
    const approvalId = reviewed.approvalId;
    setErrorText(null);
    try {
      const reply = await createScientificAuthorization(approvalId, `scientific-${approvalId}`);
      const record = selectAuthorization(activeRef.current, reply.authorization, id, operation.current);
      if (!finish(id)) {
        return;
      }
      const created =
        reply.status === "authorization_created" && record?.execution_authorized === true;
      const replayed =
        reply.status === "authorization_replayed" &&
        record?.status === "authorized" &&
        record.execution_authorized === true &&
        record.text === AUTHORIZED_TEXT;
      const consumed =
        reply.status === "authorization_replayed" &&
        record?.status === "authorized" &&
        record.execution_authorized === false &&
        record.text === CONSUMED_TEXT;
      if (
        !record ||
        !recordMatchesSummary(record, reviewed) ||
        record.status !== "authorized" ||
        (!created && !replayed && !consumed)
      ) {
        setErrorText(record ? REJECTED : reply.text);
        return;
      }
      activeRef.current = record;
      setActive(record);
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
      const reply = await listScientificAuthorizations();
      const next = selectAuthorizationHistory(
        historyRef.current,
        reply.authorizations,
        id,
        operation.current,
      );
      if (!finish(id)) {
        return;
      }
      historyRef.current = next;
      setHistory(next);
      setHistoryLoaded(true);
    } catch {
      if (finish(id)) {
        setErrorText(REJECTED);
      }
    }
  }

  async function openHistorical(authorizationId: string) {
    const id = begin();
    if (id === null) {
      return;
    }
    try {
      const reply = await readScientificAuthorization(authorizationId);
      const record = selectAuthorization(
        inspectedRef.current,
        reply.authorization,
        id,
        operation.current,
      );
      if (!finish(id)) {
        return;
      }
      if (!record || record.authorization_id !== authorizationId) {
        setErrorText(REJECTED);
        return;
      }
      inspectedRef.current = record;
      setInspected(record);
    } catch {
      if (finish(id)) {
        setErrorText(REJECTED);
      }
    }
  }

  async function revoke() {
    if (!current || current.status !== "authorized" || current.execution_authorized !== true) {
      return;
    }
    const authorizationId = current.authorization_id;
    const id = begin();
    if (id === null) {
      return;
    }
    try {
      const reply = await revokeScientificAuthorization(authorizationId, `revoke-${authorizationId}`);
      const record = selectAuthorization(activeRef.current, reply.authorization, id, operation.current);
      if (!finish(id)) {
        return;
      }
      if (!record || record.authorization_id !== authorizationId || record.status !== "revoked") {
        setErrorText(REJECTED);
        return;
      }
      activeRef.current = record;
      setActive(record);
    } catch {
      if (finish(id)) {
        setErrorText(REJECTED);
      }
    }
  }

  return (
    <section className="scientific-authorization" aria-label="Scientific run authorization" aria-busy={phase === "loading"}>
      <h3>Scientific run authorization</h3>
      <p>Readiness is not authorization.</p>
      <p>Authorization is not execution.</p>
      <p>Execution would not itself be scientific validation.</p>
      <p>CD22 and TACI remain blocked.</p>
      {current?.status === "authorized" && current.execution_authorized === false ? null : (
        <p>This permission is single-use. No scientific execution has started.</p>
      )}
      <button type="button" onClick={review} disabled={!ready || loading}>
        Review scientific authorization
      </button>
      {summary ? (
        <>
          <h4>Authorization summary</h4>
          <dl>
            <div>
              <dt>Target</dt>
              <dd>{summary.target}</dd>
            </div>
            <div>
              <dt>Request</dt>
              <dd>
                {summary.requestId} revision {summary.revision}
              </dd>
            </div>
            <div>
              <dt>Approval</dt>
              <dd>{summary.approvalId}</dd>
            </div>
            <div>
              <dt>Execution profile</dt>
              <dd>{summary.profileId}</dd>
            </div>
            <div>
              <dt>Tool</dt>
              <dd>
                {summary.toolName} {summary.toolVersion}
              </dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>
                {summary.runtimeName} {summary.runtimeVersion}
              </dd>
            </div>
            <div>
              <dt>Image digest</dt>
              <dd>{summary.imageDigest}</dd>
            </div>
            <div>
              <dt>Manifest digest</dt>
              <dd>{summary.manifestDigest}</dd>
            </div>
            <div>
              <dt>Readiness observed</dt>
              <dd>
                <time dateTime={summary.observedAt}>{summary.observedAt}</time>
              </dd>
            </div>
            <div>
              <dt>Token ceiling</dt>
              <dd>{ceiling(summary.tokenCeiling)}</dd>
            </div>
            <div>
              <dt>Model cost ceiling</dt>
              <dd>{ceiling(summary.modelCostCeiling)}</dd>
            </div>
            <div>
              <dt>Scientific tool cost ceiling</dt>
              <dd>{ceiling(summary.scientificToolCostCeiling)}</dd>
            </div>
            <div>
              <dt>Action on limit</dt>
              <dd>{summary.budget.action_on_limit}</dd>
            </div>
            <div>
              <dt>Warning threshold</dt>
              <dd>{ceiling(summary.budget.warning_threshold)}</dd>
            </div>
            <div>
              <dt>Currency</dt>
              <dd>{summary.budget.currency ?? "Not recorded"}</dd>
            </div>
            <div>
              <dt>Human override required</dt>
              <dd>{summary.budget.human_override_required ? "true" : "false"}</dd>
            </div>
            <div>
              <dt>Authorizes execution</dt>
              <dd>{summary.budget.authorizes_execution ? "true" : "false"}</dd>
            </div>
          </dl>
          {current?.status === "authorized" && current.execution_authorized === false ? null : (
            <>
              <p>This authorization may be consumed once by a future scientific executor.</p>
              <p>No scientific execution has started.</p>
            </>
          )}
          <p>This is not scientific validation.</p>
          <button type="button" onClick={() => void confirm()} disabled={loading}>
            Confirm scientific authorization
          </button>
        </>
      ) : null}
      {current?.status === "authorized" && current.execution_authorized ? (
        <p>{AUTHORIZED_TEXT}</p>
      ) : null}
      {current?.status === "authorized" && current.execution_authorized === false ? (
        <p role="status">{current.text}</p>
      ) : null}
      {current?.status === "revoked" ? <p>{current.text}</p> : null}
      {current?.status === "authorized" && current.execution_authorized ? (
        <button type="button" onClick={() => void revoke()} disabled={loading}>
          Revoke scientific authorization
        </button>
      ) : null}
      {errorText ? <p role="alert">{errorText}</p> : null}
      <button type="button" onClick={() => void loadHistory()} disabled={loading}>
        Show authorization history
      </button>
      {!historyLoaded ? <p>Refresh authorization history to load records.</p> : null}
      {historyLoaded && history.length === 0 ? (
        <p>No scientific authorization is recorded for this session.</p>
      ) : null}
      {history.length > 0 ? (
        <ul aria-label="Authorization history">
          {history.map((item) => (
            <li key={item.authorization_id}>
              <button
                type="button"
                onClick={() => void openHistorical(item.authorization_id)}
                disabled={loading}
              >
                Open {item.authorization_id}
              </button>
              <span>
                {" "}
                {item.status} revision {item.request_revision}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {inspected ? (
        <dl aria-label="Historical authorization">
          <div>
            <dt>Historical authorization</dt>
            <dd>{inspected.authorization_id}</dd>
          </div>
          <div>
            <dt>Historical status</dt>
            <dd>{inspected.status}</dd>
          </div>
          <div>
            <dt>Historical revision</dt>
            <dd>{inspected.request_revision}</dd>
          </div>
        </dl>
      ) : null}
    </section>
  );
}
