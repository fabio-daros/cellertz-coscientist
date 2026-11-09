import { type DesignDraftReply, type ExecutionBudget } from "./contracts";
import {
  type GateReply,
  draftGenerator,
  recordedBudgetLabel,
} from "./approval";

const DECISIONS = [
  ["approved", "Approve request"],
  ["rejected", "Reject"],
  ["changes_requested", "Request changes"],
  ["held", "Hold"],
] as const;

export function HumanReviewPanel({
  sessionId,
  draftReply,
  budget,
  reviewerId,
  reason,
  onReviewerId,
  onReason,
  onDecide,
  decisionLoading,
  decisionReply,
  executionReply,
  onDispatch,
  dispatchLoading,
}: {
  sessionId: string;
  draftReply: DesignDraftReply | null;
  budget: ExecutionBudget;
  reviewerId: string;
  reason: string;
  onReviewerId: (value: string) => void;
  onReason: (value: string) => void;
  onDecide: (decision: (typeof DECISIONS)[number][0]) => void;
  decisionLoading: boolean;
  decisionReply: GateReply | null;
  executionReply: GateReply | null;
  onDispatch: () => void;
  dispatchLoading: boolean;
}) {
  const draft = draftReply?.draft ?? null;
  const generator = draftGenerator(draftReply);
  const seed = draft?.fields.find((field) => field.name === "seed") ?? null;
  const filterPolicy = draft?.fields.find((field) => field.name === "filter_policy") ?? null;
  const complete = draft?.completeness === "complete";
  const approval = decisionReply?.approval ?? null;
  const currentApproval =
    approval !== null &&
    draft !== null &&
    approval.request_id === draft.request_id &&
    approval.request_revision === draft.revision
      ? approval
      : null;
  const identified = reviewerId.trim() !== "" && reason.trim() !== "";
  const demonstrationReady =
    currentApproval?.decision === "approved" && complete && generator === "fixture" && !dispatchLoading;
  const currentExecution =
    executionReply?.approval &&
    draft &&
    executionReply.approval.request_id === draft.request_id &&
    executionReply.approval.request_revision === draft.revision
      ? executionReply
      : null;
  const visibleDecision = decisionReply?.approval ? (currentApproval ? decisionReply : null) : decisionReply;
  const receipt = currentExecution?.demonstration ?? null;

  return (
    <section aria-label="Design request review">
      <h3>Design request review</h3>
      <p>
        This review belongs to session {sessionId}. It does not approve the selected VHH campaign.
      </p>
      <p>reviewer_id is operator-asserted in Phase 6D. It is not production authentication.</p>
      <dl>
        <div>
          <dt>Request ID</dt>
          <dd>{draft?.request_id ?? "none"}</dd>
        </div>
        <div>
          <dt>Revision</dt>
          <dd>{draft?.revision ?? "none"}</dd>
        </div>
        <div>
          <dt>Completeness</dt>
          <dd>{draft?.completeness ?? "none"}</dd>
        </div>
        <div>
          <dt>Server digest</dt>
          <dd>{currentApproval?.revision_digest ?? "Calculated by the server when a decision is recorded."}</dd>
        </div>
        <div>
          <dt>Budget</dt>
          <dd>{recordedBudgetLabel(budget)}</dd>
        </div>
        <div>
          <dt>Execution profile</dt>
          <dd>phase6d_fixture_only</dd>
        </div>
        <div>
          <dt>Runtime policy</dt>
          <dd>
            {seed?.value == null ? "seed absent" : `seed ${String(seed.value)}`}
            {"; "}
            {filterPolicy?.value == null
              ? "filter_policy absent"
              : `filter_policy ${String(filterPolicy.value)}`}
          </dd>
        </div>
        <div>
          <dt>Approval status</dt>
          <dd>{currentApproval ? currentApproval.decision : "none"}</dd>
        </div>
        {draft?.input_selection ? (
          <div>
            <dt>Verified input identity</dt>
            <dd>
              manifest {draft.input_selection.manifest_digest}; target{" "}
              {draft.input_selection.target_structure_sha256}; settings{" "}
              {draft.input_selection.settings_sha256}; advanced{" "}
              {draft.input_selection.advanced_profile_sha256}
            </dd>
          </div>
        ) : null}
      </dl>
      <p>Demonstration execution. No scientific tool is called. No candidate is generated. No scientific memory is written.</p>
      <ul>
        {!draft ? <li>No design request revision is recorded.</li> : null}
        {draft && !complete ? <li>The request is incomplete.</li> : null}
        {complete && generator !== "fixture" ? (
          <li>This request is not the fixture-only profile. Real generators stay blocked.</li>
        ) : null}
        {visibleDecision?.status === "revision_conflict" ? <li>Revision conflict. The decision was not recorded.</li> : null}
        {visibleDecision?.status === "request_incomplete" ? <li>The request is incomplete.</li> : null}
      </ul>
      <label htmlFor="reviewer-id">Declared reviewer ID</label>
      <input
        id="reviewer-id"
        value={reviewerId}
        onChange={(event) => onReviewerId(event.target.value)}
      />
      <label htmlFor="review-reason">Reason</label>
      <textarea id="review-reason" value={reason} onChange={(event) => onReason(event.target.value)} />
      <div className="choices">
        {DECISIONS.map(([decision, label]) => (
          <button
            key={decision}
            type="button"
            onClick={() => onDecide(decision)}
            disabled={
              draft === null ||
              decisionLoading ||
              !identified ||
              (decision === "approved" && !complete)
            }
          >
            {label}
          </button>
        ))}
      </div>
      <button type="button" onClick={onDispatch} disabled={!demonstrationReady}>
        Run approved demonstration
      </button>
      {decisionLoading || dispatchLoading ? <p role="status">Waiting for the server.</p> : null}
      {visibleDecision ? (
        <p role={visibleDecision.status === "revision_conflict" ? "alert" : undefined}>{visibleDecision.text}</p>
      ) : null}
      {currentExecution && currentExecution.status !== "demonstration_completed" ? (
        <p role="alert">{currentExecution.text}</p>
      ) : null}
      {receipt ? (
        <div role="region" aria-label="Demonstration receipt">
          <p>{receipt.text}</p>
          <p>Dispatch {receipt.dispatch_id}. Not a scientific run.</p>
          <p>Authorization {receipt.authorization_status}.</p>
          <p>Preflight {receipt.preflight_status}.</p>
          <p>Dispatch status {receipt.dispatch_status}.</p>
          <p>No candidate is generated.</p>
        </div>
      ) : null}
    </section>
  );
}
