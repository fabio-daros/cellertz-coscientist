import { useEffect, useState } from "react";
import { askCoordinator, type CoordinatorReply } from "./attempt-coordinator";
import {
  bindPlanAuthorization,
  dispatchPlan,
  readStructuredPlan,
  recordPlanApproval,
  type GenerationPlan,
  type GenerationPlanFields,
  type PlanArtifact,
} from "./generation-plan";

const SETTING_FIELDS = [
  "request_text",
  "target_identity",
  "hotspot_assumption",
  "filter_policy",
  "binder_length",
  "seed",
  "cpu_count",
  "memory_mib",
  "gpu",
  "execution_deadline_seconds",
] as const;

type NoteRole = "status" | "alert";

export function GenerationPlanReview({ incomingReply = null }: { incomingReply?: CoordinatorReply | null }) {
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState<CoordinatorReply | null>(null);
  const [plan, setPlan] = useState<GenerationPlan | null>(null);
  const [draftedQuestion, setDraftedQuestion] = useState<string | null>(null);
  const [reviewerId, setReviewerId] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [authorizationId, setAuthorizationId] = useState("");
  const [note, setNote] = useState("");
  const [noteRole, setNoteRole] = useState<NoteRole>("status");
  const [attempt, setAttempt] = useState(1);
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState<"approval" | "authorization" | "dispatch" | null>(null);

  useEffect(() => {
    if (!incomingReply) {
      return;
    }
    const found = readStructuredPlan(incomingReply.plan);
    const proposed = (incomingReply.steps ?? []).some(
      (step) => step?.tool === "propose_generation_plan" || step?.tool === "propose_refinement",
    );
    if (found) {
      setPlan(found);
      setNote("");
      return;
    }
    if (proposed) {
      setNote(
        "The coordinator did not return a structured plan. The answer text was not used as a plan.",
      );
      setNoteRole("alert");
    }
  }, [incomingReply]);

  function report(message: string, role: NoteRole) {
    setNote(message);
    setNoteRole(role);
  }

  async function ask() {
    const text = question.trim();
    if (busy || action) {
      return;
    }
    if (!text) {
      report("Enter a generation request before drafting a plan. The existing plan was kept.", "alert");
      return;
    }
    if (plan && draftedQuestion === text) {
      report("This request already has a plan. The existing plan was kept.", "alert");
      return;
    }
    setBusy(true);
    report("Drafting a plan.", "status");
    const next = await askCoordinator(text, plan?.conversation_id);
    setBusy(false);
    if (!next) {
      report(
        "The coordinator request failed. The existing plan was kept. No plan was approved and no run was started.",
        "alert",
      );
      return;
    }
    setReply(next);
    const found = readStructuredPlan(next.plan);
    if (!found) {
      report(
        "The coordinator did not return a structured plan. The existing plan was kept. The answer text was not used as a plan.",
        "alert",
      );
      return;
    }
    setPlan(found);
    setDraftedQuestion(text);
    setNote("");
  }

  async function approve() {
    if (!plan || busy || action) {
      return;
    }
    setAction("approval");
    const next = await recordPlanApproval(plan, reviewerId, reviewReason);
    setAction(null);
    if (!next) {
      report("The approval request failed. This revision stays unapproved.", "status");
      return;
    }
    setPlan(next);
    report(next.approved ? "This revision is approved." : "This revision stays unapproved.", "status");
  }

  return (
    <section aria-label="Generation plan review">
      <h2>Generation plan review</h2>
      <p>
        Plan approval records that a person reviewed one revision. Execution authorization is a
        separate permission already recorded by the scientific authorization service. Binding that
        identifier does not grant it, and a chat answer grants neither.
      </p>
      <p>attempt.2LUTUOX4 stays a historical manual attempt.</p>
      <label htmlFor="generation-question">Generation request</label>
      <textarea
        id="generation-question"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <button type="button" onClick={() => void ask()} disabled={busy || action !== null}>
        Draft a plan
      </button>
      {reply ? <p>{reply.text}</p> : null}
      {plan ? <PlanCard plan={plan} /> : null}
      {plan ? (
        <div>
          <label htmlFor="plan-reviewer">Reviewer</label>
          <input
            id="plan-reviewer"
            value={reviewerId}
            onChange={(event) => setReviewerId(event.target.value)}
          />
          <label htmlFor="plan-reason">Reason for this revision</label>
          <textarea
            id="plan-reason"
            rows={2}
            value={reviewReason}
            onChange={(event) => setReviewReason(event.target.value)}
          />
          <button type="button" onClick={() => void approve()} disabled={busy || action !== null}>
            Record approval
          </button>
          <label htmlFor="plan-authorization">Existing authorization id</label>
          <input
            id="plan-authorization"
            value={authorizationId}
            onChange={(event) => setAuthorizationId(event.target.value)}
          />
          <button
            type="button"
            disabled={busy || action !== null}
            onClick={async () => {
              if (!plan || action) {
                return;
              }
              setAction("authorization");
              const next = await bindPlanAuthorization(plan, authorizationId, reviewerId, reviewReason);
              setAction(null);
              if (!next) {
                report("The authorization binding failed. No execution permission was granted.", "status");
                return;
              }
              setPlan(next);
              report(
                next.authorization_bound
                  ? "The existing authorization is bound. Plan approval did not grant it."
                  : "The authorization was not bound.",
                "status",
              );
            }}
          >
            Bind authorization
          </button>
          <button
            type="button"
            disabled={busy || action !== null}
            onClick={async () => {
              if (!plan || action) {
                return;
              }
              setAction("dispatch");
              const next = await dispatchPlan(plan, attempt);
              setAttempt((current) => current + 1);
              setAction(null);
              if (!next) {
                report("The dispatch request failed. No run was started.", "status");
                return;
              }
              setPlan(next);
              report(
                next.blockers && next.blockers.length > 0
                  ? "Dispatch is blocked."
                  : "The plan was referred to the scientific run service.",
                "status",
              );
            }}
          >
            Check dispatch
          </button>
          {plan.blockers && plan.blockers.length > 0 ? (
            <ul aria-label="Dispatch blockers">
              {plan.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {note ? <p role={noteRole}>{note}</p> : null}
    </section>
  );
}

function PlanCard({ plan }: { plan: GenerationPlan }) {
  const artifacts = artifactsOf(plan.fields);
  return (
    <div role="region" aria-label="Recorded generation plan">
      <p>
        {plan.plan_id} · revision {plan.revision}
        {plan.status ? ` · ${plan.status}` : ""} · {plan.approved ? "approved" : "unapproved"}
        {plan.authorization_bound ? " · authorization bound" : " · authorization not bound"}
      </p>
      <p>Digest {plan.digest}</p>
      <p>
        Input files are verified against the registered copies: {verification(plan)}.
      </p>
      <p>
        Approval launches execution: {executionClaim(plan.plan_approval_grants_execution)}
      </p>
      <dl aria-label="Plan settings">
        {SETTING_FIELDS.map((field) => (
          <div key={field}>
            <dt>{field}</dt>
            <dd>{settingText(plan.fields?.[field])}</dd>
          </div>
        ))}
      </dl>
      <ul aria-label="Plan input artifacts">
        {artifacts.length > 0 ? (
          artifacts.map((item) => (
            <li key={`${item.role ?? "artifact"}-${item.sha256 ?? "none"}`}>
              {item.role ?? "not recorded"} {item.sha256 ?? "not recorded"}
            </li>
          ))
        ) : (
          <li>not recorded</li>
        )}
      </ul>
      {(plan.questions ?? []).length > 0 ? (
        <ul aria-label="Open plan questions">
          {(plan.questions ?? []).map((item) => (
            <li key={`${item.field}-${item.question}`}>
              {item.field}: {item.question}
            </li>
          ))}
        </ul>
      ) : (
        <p>Open plan questions: none recorded.</p>
      )}
    </div>
  );
}

function verification(plan: GenerationPlan): string {
  const value = plan.fields?.input_hashes_verified_against_files;
  if (value === true) {
    return "yes";
  }
  if (value === false) {
    return "no";
  }
  return "not recorded";
}

function executionClaim(value: boolean | undefined): string {
  if (value === false) {
    return "no";
  }
  if (value === true) {
    return "yes";
  }
  return "not recorded";
}

function settingText(value: GenerationPlanFields[keyof GenerationPlanFields] | undefined): string {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  return "not recorded";
}

function artifactsOf(fields: GenerationPlanFields | undefined): PlanArtifact[] {
  const recorded = fields?.input_artifacts ?? fields?.stated_input_artifacts;
  if (!Array.isArray(recorded)) {
    return [];
  }
  return recorded.filter((item) => item !== null && typeof item === "object");
}
