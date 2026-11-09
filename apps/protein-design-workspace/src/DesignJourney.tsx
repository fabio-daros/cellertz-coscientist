import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { openRecordedResults } from "./AttemptInspection";

type JourneyStage = {
  stage_id: string;
  status: string;
  implementation_kind: string;
  implementation: string;
  tools: string[];
  agent_executed: boolean;
  evidence_references: {
    kind?: string;
    request_id?: string;
    plan_id?: string;
    journey_id?: string;
    attempt_id?: string;
    candidate_id?: string | null;
    revision?: number;
    digest?: string;
  }[];
  output: Record<string, unknown> | null;
  limitations: string[];
  contribution?: string;
  blocking_reason?: string | null;
  failure_reason?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

type ExecutionReview = {
  plan_id: string | null;
  revision: number | null;
  digest: string | null;
  input_identities: unknown;
  input_registration: string | null;
  resource_budget: Record<string, unknown>;
  questions: unknown[];
  unresolved_blockers: string[];
  configuration_blockers?: string[];
  recorded_plan_approval: boolean;
  authorization_available?: boolean;
  server_execution?: string;
  journey_gpu_execution?: string | null;
  execution_authorized: boolean;
  launches_compute: boolean;
};

type BudgetUsage = {
  attempted_trajectories?: number;
  low_confidence_trajectories?: number;
  relaxed_trajectories?: number;
  requested_attempted_trajectories?: number;
};

type RoundParameter = {
  name?: string;
  value?: unknown;
  provenance?: string;
  source?: string;
  parent_round_id?: string;
  parent_plan_revision?: number | null;
};

type JourneyExecution = {
  launch_id?: string;
  attempt_id?: string;
  gpu_index?: number | null;
  status?: string;
  consumed?: boolean;
  terminal?: boolean;
  budget_usage?: BudgetUsage | null;
  progress?: { status?: string }[];
  accepted_candidate?: boolean | null;
  successful_candidate_generation?: boolean;
  candidate_id?: string | null;
  termination_reason?: string | null;
  budget_compliance_established?: boolean;
  scientific_validation_established?: boolean;
  failure_counters?: Record<string, string> | null;
  result_link?: {
    journey_id?: string;
    attempt_id?: string;
    candidate_id?: string | null;
  };
  candidate_identity?: { design?: string; candidate_id?: string } | null;
  digest?: string;
  plan_id?: string;
  revision?: number;
  attempt_directory?: string;
  designs_directory?: string;
};

export type DesignJourneyRecord = {
  journey_id: string;
  request_id: string;
  request_revision: number;
  plan_id: string | null;
  plan_revision: number | null;
  gpu_execution: string;
  approved: boolean;
  specialist_agents_executed: boolean;
  approval?: {
    reviewer_id?: string;
    recorded_at?: string;
    digest?: string;
    plan_id?: string;
    revision?: number;
    scope?: string;
  } | null;
  execution_authorization?: {
    execution_authorized?: boolean;
    launches_compute?: boolean;
    digest?: string;
    plan_id?: string;
    revision?: number;
  } | null;
  execution_review?: ExecutionReview | null;
  execution?: JourneyExecution | null;
  rounds?: RoundCard[];
  knowledge: { status: string; detail: string; sources: { id: string; version: number | null; title: string }[] };
  hermes: { status: string; detail: string };
  stages: JourneyStage[];
  request_fields?: Record<string, { value?: unknown; provenance?: string; source?: string }>;
  conflicts?: JourneyConflict[];
  historical_conflicts?: JourneyConflict[];
  acknowledgement_evidence?: AcknowledgementEvidence | null;
  approval_history?: { revision?: number; digest?: string; reviewer_id?: string; scope?: string; recorded_at?: string }[];
  events?: { kind?: string; digest?: string; round_id?: string; detail?: string }[];
  completion?: {
    request_fields?: Record<string, { value?: unknown; provenance?: string; source?: string }>;
    conflicts?: JourneyConflict[];
    historical_conflicts?: JourneyConflict[];
    acknowledgement_evidence?: AcknowledgementEvidence | null;
    approval_history?: { revision?: number; digest?: string }[];
    technical_demonstration?: TechnicalDemonstration | null;
  } | null;
};

type RoundCard = {
  round_id: string;
  parent_round_id?: string | null;
  child_round_id?: string | null;
  child_round_ids?: string[];
  status?: string;
  cancelled_at?: string | null;
  cancellable?: boolean;
  ordinal?: number;
  active?: boolean;
  plan_id?: string | null;
  plan_revision?: number | null;
  digest?: string | null;
  target?: string | null;
  outcome?: string | null;
  outcome_label?: string;
  execution_completed?: boolean;
  successful_candidate_generation?: boolean;
  candidate_count?: number;
  budget_usage?: BudgetUsage | null;
  budget_compliance_established?: boolean;
  scientific_validation_established?: boolean;
  recorded_review_status?: string;
  recorded_review_evidence?: string;
  execution_authorization_status?: string;
  execution_authorization_evidence?: string;
  approved_gpu?: string | null;
  specialist_timestamp_observation?: string | null;
  launch_gpu_index?: number | null;
  gpu_proposal?: string | null;
  gpu_proposal_source?: string | null;
  gpu_proposal_historical?: boolean;
  profile_gpu?: string | null;
  profile_accepts_gpu_proposal?: boolean;
  host_gpu_index?: string | null;
  profile_restriction?: string;
  specialist_stages?: JourneyStage[];
  specialist_events?: { stage_id?: string; status?: string; at?: string; reason?: string; implementation_kind?: string }[];
  specialist_progress_available?: boolean;
  post_generation?: {
    automatic?: boolean;
    status?: string;
    phase?: string;
    events?: { sequence?: number; at?: string; kind?: string; status?: string; detail?: string; implementation_kind?: string }[];
  } | null;
  results_publication?: { status?: string; started_at?: string; completed_at?: string } | null;
  parameters?: RoundParameter[];
  sequence_limits?: Record<string, number> | null;
  attempt_id?: string | null;
  approval?: DesignJourneyRecord["approval"];
  execution_authorization?: DesignJourneyRecord["execution_authorization"];
  execution?: JourneyExecution | null;
};

type JourneyConflict = {
  field?: string;
  conflict?: string;
  status?: string;
  plan_revision?: number;
  digest?: string;
  detail?: string;
};

type AcknowledgementEvidence = {
  filter_policy?: string | null;
  recorded?: boolean;
  text?: string | null;
  plan_request_text?: string;
  blocker?: string | null;
  establishes_scientific_quality?: boolean;
};

type TechnicalDemonstration = {
  name: string;
  profile_id: string;
  authorizes_execution: boolean;
  launches_compute: boolean;
  acknowledgement: string;
  values: { name: string; value: unknown; source: string; recordable: boolean; note: string }[];
  conflicts: { field?: string; conflict?: string }[];
};

const STATUS_LABEL: Record<string, string> = {
  pending: "pending",
  running: "running",
  completed: "completed",
  blocked: "blocked",
  failed: "failed",
  not_applicable: "not applicable",
};

type DraftField = { name: string; value: string | number | null; provenance: string };

class JourneyTransportError extends Error {
  constructor(status: number) {
    super(`The design journey could not be read. The server returned HTTP ${status}.`);
    this.name = "JourneyTransportError";
  }
}

class JourneyAbsent extends Error {
  constructor() {
    super("The design journey was not recorded.");
    this.name = "JourneyAbsent";
  }
}

const journeyLoads = new Map<string, Promise<DesignJourneyRecord>>();

export function clearJourneyLoadCache(): void {
  journeyLoads.clear();
}

async function readJourney(response: Response): Promise<DesignJourneyRecord> {
  if (response.status === 404) {
    throw new JourneyAbsent();
  }
  let body: Partial<DesignJourneyRecord> & { status?: string };
  try {
    body = (await response.json()) as Partial<DesignJourneyRecord> & { status?: string };
  } catch {
    throw new JourneyTransportError(response.status);
  }
  if (body.status === "journey_absent") {
    throw new JourneyAbsent();
  }
  if (!response.ok || !body.journey_id || !Array.isArray(body.stages)) {
    throw new JourneyTransportError(response.status);
  }
  return body as DesignJourneyRecord;
}

export function DesignJourney({
  requestId,
  revision,
  requestText,
  fields = [],
  mode = "record",
  journeyId,
  onRecorded,
}: {
  requestId?: string;
  revision?: number;
  requestText?: string;
  fields?: DraftField[];
  mode?: "record" | "restore";
  journeyId?: string;
  onRecorded?: (record: DesignJourneyRecord) => void;
}) {
  const [record, setRecord] = useState<DesignJourneyRecord | null>(null);
  const [note, setNote] = useState("Loading the recorded journey.");
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const fieldKey = JSON.stringify(fields);
  const onRecordedRef = useRef(onRecorded);
  onRecordedRef.current = onRecorded;
  const loadKeyRef = useRef("");

  useEffect(() => {
    let cancelled = false;
    setRecord(null);
    setLoadFailed(false);
    setNote("Loading the recorded journey.");
    const recordedFields = JSON.parse(fieldKey) as DraftField[];
    const restore = mode === "restore";
    if (restore && !journeyId) {
      setNote("The design journey was not recorded.");
      return () => {
        cancelled = true;
      };
    }
    if (!restore && (!requestId || !requestText || revision === undefined)) {
      setNote("The design journey was not recorded.");
      return () => {
        cancelled = true;
      };
    }
    const loadKey = restore
      ? `restore:${journeyId}`
      : `record:${requestId}:${revision}:${requestText}:${fieldKey}`;
    loadKeyRef.current = loadKey;
    let pending = journeyLoads.get(loadKey);
    if (!pending) {
      const request = restore
        ? fetch(`/api/design-journeys/${journeyId}`)
        : fetch("/api/design-journeys", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              request_id: requestId,
              request_revision: revision,
              request_text: requestText,
              fields: recordedFields,
            }),
          });
      pending = request.then((response) => readJourney(response));
      journeyLoads.set(loadKey, pending);
      void pending.catch(() => {
        if (journeyLoads.get(loadKey) === pending) {
          journeyLoads.delete(loadKey);
        }
      });
    }
    void pending
      .then((body) => {
        if (!cancelled) {
          setRecord(body);
          setNote("");
          onRecordedRef.current?.(body);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setRecord(null);
          setLoadFailed(true);
          setNote(
            error instanceof JourneyAbsent
              ? error.message
              : error instanceof JourneyTransportError
                ? error.message
                : "The design journey could not be read.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, mode, journeyId, requestId, revision, requestText, fieldKey]);

  return (
    <section className="design-journey" aria-label="Design journey">
      <h3>Design journey</h3>
      {note ? <p role="status">{note}</p> : null}
      {loadFailed ? (
        <button type="button" onClick={() => setAttempt((value) => value + 1)}>
          Try again
        </button>
      ) : null}
      {record ? (
        <JourneyRecord
          record={record}
          onApproved={(next) => {
            if (loadKeyRef.current) {
              journeyLoads.set(loadKeyRef.current, Promise.resolve(next));
            }
            setRecord(next);
            setNote("");
          }}
          onRejected={(message) => setNote(message)}
        />
      ) : null}
      <p>
        PDL1 demonstration. This screen follows one round from the request to execution. Refreshing
        reads the saved journey. It does not rerun specialists or launch generation.
      </p>
    </section>
  );
}

function JourneyRecord({
  record,
  onApproved,
  onRejected,
}: {
  record: DesignJourneyRecord;
  onApproved: (record: DesignJourneyRecord) => void;
  onRejected: (message: string) => void;
}) {
  const planning = record.stages.find((stage) => stage.stage_id === "generation_planning");
  const approval = record.stages.find((stage) => stage.stage_id === "approval");
  const roundCancelled = activeRoundCancelled(record);
  const step = professorStep(record);
  const active = (record.rounds ?? []).find((card) => card.active !== false);
  const leadWithRound = Boolean(active && resultsHref(active) && !runningRound(active));
  const review = record.execution_review;
  const serverDisabled = review?.server_execution === "disabled"
    || (review?.configuration_blockers ?? []).includes("demonstration_not_configured");
  const rounds = (
    <RoundCards record={record} onPrepared={onApproved} onRejected={onRejected} />
  );
  const nextStep = (
    <section className="next-step" aria-label="Next step">
        <h4>Next: {step.title}</h4>
        <p>{step.detail}</p>
        {step.blockers.length > 0 ? (
          <ul>
            {step.blockers.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : null}
        <PlanSummary record={record} />
        {roundCancelled ? (
          <p>This round is cancelled. It cannot be approved, authorized, or launched.</p>
        ) : null}
        {!roundCancelled && approval?.status === "pending" && planning?.output && !record.execution_authorization ? (
          <ApprovalForm
            journeyId={record.journey_id}
            planId={record.plan_id}
            revision={record.plan_revision}
            digest={typeof planning.output.digest === "string" ? planning.output.digest : ""}
            onApproved={onApproved}
            onRejected={onRejected}
          />
        ) : null}
        {!roundCancelled && record.execution?.consumed !== true && record.completion?.technical_demonstration ? (
          <PlanCompletion record={record} onSaved={onApproved} onRejected={onRejected} />
        ) : null}
        {record.execution?.consumed === true ? (
          <p>This round already started. Plan changes belong to a new round.</p>
        ) : null}
        {!roundCancelled && record.execution_review && authorizationOffered(record) ? (
          <ExecutionAuthorizationForm
            journeyId={record.journey_id}
            review={record.execution_review}
            onAuthorized={onApproved}
            onRejected={onRejected}
          />
        ) : null}
        {!roundCancelled && record.execution_review?.execution_authorized && record.execution?.consumed !== true ? (
          <LaunchForm
            journeyId={record.journey_id}
            review={record.execution_review}
            onLaunched={onApproved}
            onRejected={onRejected}
          />
        ) : null}
    </section>
  );
  return (
    <>
      {leadWithRound && serverDisabled ? (
        <p role="status">New executions are disabled. Saved results remain available.</p>
      ) : null}
      {leadWithRound ? rounds : null}
      {leadWithRound ? null : nextStep}
      {leadWithRound ? null : rounds}
    </>
  );
}

function RoundCards({
  record,
  onPrepared,
  onRejected,
}: {
  record: DesignJourneyRecord;
  onPrepared: (record: DesignJourneyRecord) => void;
  onRejected: (message: string) => void;
}) {
  const cards = record.rounds && record.rounds.length > 0 ? record.rounds : [syntheticRound(record)];
  const [pendingRound, setPendingRound] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [cancelledOpen, setCancelledOpen] = useState(false);
  const [actionError, setActionError] = useState<{ roundId: string; message: string } | null>(null);
  const submitting = useRef(false);

  async function prepare(roundId: string) {
    if (submitting.current) {
      return;
    }
    submitting.current = true;
    setPendingRound(roundId);
    try {
      const response = await fetch(`/api/design-journeys/${record.journey_id}/rounds`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ parent_round_id: roundId }),
      });
      const body = (await response.json()) as DesignJourneyRecord & { status?: string; detail?: string };
      if (!response.ok || !body.journey_id) {
        const message = body.detail || "The next round was not prepared. Generation was not launched.";
        setActionError({ roundId, message });
        onRejected(message);
        return;
      }
      setActionError(null);
      onPrepared(body);
    } catch {
      const message = "The next round was not prepared. Generation was not launched.";
      setActionError({ roundId, message });
      onRejected(message);
    } finally {
      submitting.current = false;
      setPendingRound(null);
    }
  }

  async function cancel(roundId: string) {
    if (submitting.current) {
      return;
    }
    submitting.current = true;
    setPendingRound(roundId);
    try {
      const response = await fetch(`/api/design-journeys/${record.journey_id}/rounds/${roundId}/cancellation`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      const body = (await response.json()) as DesignJourneyRecord & { status?: string; detail?: string };
      if (!response.ok || !body.journey_id) {
        const message = body.detail || "This round was not cancelled. Generation was not launched.";
        setActionError({ roundId, message });
        onRejected(message);
        return;
      }
      setActionError(null);
      setExpanded((current) => ({ ...current, [roundId]: false }));
      setCancelledOpen(false);
      onPrepared(body);
    } catch {
      const message = "This round was not cancelled. Generation was not launched.";
      setActionError({ roundId, message });
      onRejected(message);
    } finally {
      submitting.current = false;
      setPendingRound(null);
    }
  }

  async function recordSpecialists(roundId: string) {
    if (submitting.current) {
      return;
    }
    submitting.current = true;
    setPendingRound(roundId);
    setActionError(null);
    try {
      const response = await fetch(
        `/api/design-journeys/${record.journey_id}/rounds/${roundId}/specialist-progress`,
        { method: "POST", headers: { "content-type": "application/json" }, body: "{}" },
      );
      const body = (await response.json()) as DesignJourneyRecord & { status?: string; detail?: string };
      if (!response.ok || !body.journey_id) {
        const message = body.detail || "Specialist progress was not recorded. Generation was not launched.";
        setActionError({ roundId, message });
        onRejected(message);
        return;
      }
      setActionError(null);
      onPrepared(body);
    } catch {
      const message = "Specialist progress was not recorded. Generation was not launched.";
      setActionError({ roundId, message });
      onRejected(message);
    } finally {
      submitting.current = false;
      setPendingRound(null);
    }
  }

  const onPreparedRef = useRef(onPrepared);
  onPreparedRef.current = onPrepared;
  const watching = cards.some((card) => {
    const post = card.post_generation?.status;
    if (post === "queued" || post === "running") {
      return true;
    }
    return (card.specialist_stages ?? []).some((stage) => stage.status === "running");
  });

  useEffect(() => {
    if (!watching) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void fetch(`/api/design-journeys/${record.journey_id}`)
        .then((response) => readJourney(response))
        .then((body) => onPreparedRef.current(body))
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [record.journey_id, watching]);
  const visible = cards.filter((card) => card.status !== "cancelled");
  const cancelled = cards.filter((card) => card.status === "cancelled");

  function renderCard(card: RoundCard) {
        const openChild = openReplacement(cards, card);
        const prepareLabel = cards.some(
          (item) => item.parent_round_id === card.round_id && item.status === "cancelled",
        )
          ? "Prepare another round"
          : "Prepare next round";
        const launchConsumed = card.execution?.consumed === true;
        const postStatus = card.post_generation?.status;
        const processing = postStatus === "queued" || postStatus === "running"
          || (launchConsumed && card.execution_completed !== true);
        const canPrepare = card.status !== "cancelled" && !openChild && !processing;
        const canCancel =
          card.cancellable === true ||
          (card.cancellable !== false &&
            card.status !== "cancelled" &&
            !launchConsumed &&
            Boolean(card.parent_round_id));
        const detailsOpen = expanded[card.round_id] === true;
        const cancelledCard = card.status === "cancelled";
        const compact = Boolean(resultsHref(card) && !runningRound(card) && !cancelledCard);
        const includeJourney = card.active !== false;
        return (
          <article key={card.round_id} className="round-card" aria-label={`Round ${card.ordinal ?? ""}`}>
            <h4>{compact ? `Round ${card.ordinal ?? "1"} · ${roundHeadline(card)}` : `Round ${card.ordinal ?? "1"}`}</h4>
            {compact ? <p className="round-facts">{roundFacts(card, record)}</p> : <p>{roundStateText(card)}</p>}
            {compact || !card.post_generation ? null : <p>{postProcessingText(card)}</p>}
            {runningRound(card) ? (
              <ol aria-label={`Round ${card.ordinal ?? ""} progress`}>
                {(card.specialist_stages ?? []).length > 0 ? (
                  (card.specialist_stages ?? []).map((stage) => (
                    <li key={`${card.round_id}-${stage.stage_id}-progress`}>
                      {stageLabel(stage.stage_id)}: {stage.status}
                    </li>
                  ))
                ) : (
                  <li>Generation: {card.post_generation?.status ?? card.execution?.status ?? "not recorded"}</li>
                )}
              </ol>
            ) : null}
            {resultsHref(card) ? (
              <p>
                <a
                  className="view-results"
                  href={resultsHref(card) ?? undefined}
                  onClick={(event) => {
                    const attempt = card.execution?.attempt_id ?? card.attempt_id;
                    const design = card.execution?.candidate_identity?.design;
                    if (!attempt || !design) {
                      return;
                    }
                    event.preventDefault();
                    openRecordedResults(attempt, design);
                  }}
                >
                  View results
                </a>
              </p>
            ) : null}
            {compact ? null : (
            <dl>
              <div>
                <dt>Target</dt>
                <dd>{card.target ?? "not recorded"}</dd>
              </div>
              {cancelledCard ? (
                <div>
                  <dt>Cancelled</dt>
                  <dd>{card.cancelled_at ?? "time not recorded"}</dd>
                </div>
              ) : (
                <>
                  <div>
                    <dt>Budget</dt>
                    <dd>{budgetSentence(card)}</dd>
                  </div>
                  <div>
                    <dt>Candidates</dt>
                    <dd>{typeof card.candidate_count === "number" ? card.candidate_count : "not recorded"}</dd>
                  </div>
                  <div>
                    <dt>Review</dt>
                    <dd>{plainReview(reviewLabel(card, record))}</dd>
                  </div>
                  <div>
                    <dt>Authorization</dt>
                    <dd>{plainAuthorization(authorizationLabel(card, record))}</dd>
                  </div>
                  <div>
                    <dt>Next</dt>
                    <dd>{cardNextAction(card, record)}</dd>
                  </div>
                </>
              )}
            </dl>
            )}
            {actionError?.roundId === card.round_id ? <p role="alert">{actionError.message}</p> : null}
            <div className={compact ? "round-toolbar" : undefined}>
            <div className="round-actions">
              {canCancel ? (
                <button
                  type="button"
                  disabled={pendingRound === card.round_id}
                  onClick={() => void cancel(card.round_id)}
                >
                  Cancel round
                </button>
              ) : null}
              {card.specialist_progress_available && card.post_generation?.automatic !== true ? (
                <button
                  type="button"
                  disabled={pendingRound === card.round_id}
                  onClick={() => void recordSpecialists(card.round_id)}
                >
                  Record specialist progress
                </button>
              ) : null}
              {canPrepare && (card.active !== false || prepareLabel === "Prepare another round") ? (
                <button
                  type="button"
                  disabled={pendingRound === card.round_id}
                  onClick={() => void prepare(card.round_id)}
                >
                  {prepareLabel}
                </button>
              ) : null}
            </div>
            <details id={`round-details-${card.round_id}`} className="round-technical" open={detailsOpen}>
              <summary
                onClick={(event) => {
                  event.preventDefault();
                  setExpanded((current) => ({ ...current, [card.round_id]: !current[card.round_id] }));
                }}
              >
                {detailsOpen ? "Hide technical details" : "Technical details"}
              </summary>
              <TechnicalRecord card={card} record={record} includeJourney={includeJourney} />
            </details>
            </div>
          </article>
        );
  }

  const current = visible.filter((card) => card.active !== false);
  const earlier = visible.filter((card) => card.active === false);
  return (
    <section className="design-rounds" aria-label="Design rounds">
      {(current.length > 0 ? current : visible).map((card) => renderCard(card))}
      {earlier.length > 0 && current.length > 0 ? (
        <details className="earlier-rounds">
          <summary>Earlier rounds ({earlier.length})</summary>
          {earlier.map((card) => renderCard(card))}
        </details>
      ) : null}
      {cancelled.length > 0 ? (
        <details
          className="cancelled-rounds"
          open={cancelledOpen}
          role="group"
          aria-label={`Cancelled rounds (${cancelled.length})`}
        >
          <summary
            onClick={(event) => {
              event.preventDefault();
              setCancelledOpen((open) => !open);
            }}
          >
            Cancelled rounds ({cancelled.length})
          </summary>
          {cancelled.map((card) => renderCard(card))}
        </details>
      ) : null}
    </section>
  );
}

function RoundDetailFields({
  card,
  record,
  section,
}: {
  card: RoundCard;
  record: DesignJourneyRecord;
  section: "plan" | "approval" | "execution";
}) {
  const execution = card.execution;
  const link = execution?.result_link;
  if (section === "plan") {
    return (
      <>
      <dl>
        <div>
          <dt>Plan</dt>
          <dd>
            {card.plan_id ?? "none"} revision {card.plan_revision ?? "none"}.
          </dd>
        </div>
        <div>
          <dt>Digest</dt>
          <dd>{card.digest ?? "not recorded"}</dd>
        </div>
      </dl>
      {card.parameters && card.parameters.length > 0 ? (
        <>
          <h5>Parameters</h5>
          {card.parameters.map((item, index) => (
            <dl key={`${item.name ?? "parameter"}-${index}`}>
              <div>
                <dt>Name</dt>
                <dd>{item.name ?? "not recorded"}</dd>
              </div>
              <div>
                <dt>Value</dt>
                <dd>{shown(item.value)}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>
                  {item.source ?? "not recorded"}
                  {item.source === "previous_round" || item.provenance === "preserved"
                    ? " Historical proposal from an earlier round."
                    : ""}
                </dd>
              </div>
              <div>
                <dt>Provenance</dt>
                <dd>{item.provenance ?? "not recorded"}</dd>
              </div>
            </dl>
          ))}
        </>
      ) : null}
      {card.sequence_limits ? <ReviewGroup title="Sequence limits" value={card.sequence_limits} /> : null}
      </>
    );
  }
  if (section === "approval") {
    return (
      <>
      {card.approval ? (
        <dl>
          <div>
            <dt>Reviewer</dt>
            <dd>{card.approval.reviewer_id ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Recorded at</dt>
            <dd>{card.approval.recorded_at ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Approval digest</dt>
            <dd>{card.approval.digest ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Approval revision</dt>
            <dd>{card.approval.revision ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Scope</dt>
            <dd>{card.approval.scope ?? "not recorded"}</dd>
          </div>
        </dl>
      ) : null}
      {card.execution_authorization ? (
        <dl>
          <div>
            <dt>Execution authorized</dt>
            <dd>{String(card.execution_authorization.execution_authorized === true)}</dd>
          </div>
          <div>
            <dt>Launches compute</dt>
            <dd>{String(card.execution_authorization.launches_compute === true)}</dd>
          </div>
        </dl>
      ) : null}
      </>
    );
  }
  return (
    <>
      {execution ? (
        <dl>
          <div>
            <dt>Attempt</dt>
            <dd>{execution.attempt_id ?? card.attempt_id ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Attempt directory</dt>
            <dd>{execution.attempt_directory ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Designs directory</dt>
            <dd>{execution.designs_directory ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Launch</dt>
            <dd>{execution.launch_id ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Result journey</dt>
            <dd>{link?.journey_id ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Result attempt</dt>
            <dd>{link?.attempt_id ?? "not recorded"}</dd>
          </div>
          <div>
            <dt>Result candidate</dt>
            <dd>{link?.candidate_id ?? "none"}</dd>
          </div>
        </dl>
      ) : null}
      {card.parent_round_id ? <p>{gpuProposalNote(card, record)}</p> : <p>{gpuAssignmentText(card, record)}</p>}
    </>
  );
}

const PLAN_STAGE_IDS = new Set(["design_request", "generation_planning"]);
const APPROVAL_STAGE_IDS = new Set(["approval", "execution_authorization"]);
const SPECIALIST_STAGE_IDS = new Set(["generation", "scoring", "ranking", "critique", "prioritization"]);

function StageArticle({ stage }: { stage: JourneyStage }) {
  return (
    <article aria-label={`${stage.stage_id} ${STATUS_LABEL[stage.status] ?? stage.status}`}>
      <h4>
        {stage.stage_id}: {STATUS_LABEL[stage.status] ?? stage.status}
      </h4>
      <p>
        Implementation {stage.implementation} ({stage.implementation_kind}). Tools{" "}
        {stage.tools.length > 0 ? stage.tools.join(", ") : "none"}. Agent executed{" "}
        {String(stage.agent_executed)}.
      </p>
      {stage.evidence_references.length > 0 ? (
        <ul>
          {stage.evidence_references.map((item, index) => (
            <li key={`${stage.stage_id}-evidence-${index}`}>{evidenceReference(item)}</li>
          ))}
        </ul>
      ) : null}
      {stage.stage_id === "generation_planning" && stage.output ? (
        <PlanReview output={stage.output} limitations={stage.limitations} />
      ) : null}
      {stage.stage_id === "design_request" && stage.output ? <RequestEvidence output={stage.output} /> : null}
      {stage.stage_id !== "generation_planning" && stage.stage_id !== "design_request" && stage.output ? (
        <ReviewGroup title="Output" value={stage.output} />
      ) : null}
      {stage.stage_id !== "generation_planning" ? stage.limitations.map((item) => <p key={item}>{item}</p>) : null}
    </article>
  );
}

function StageGroup({ stages }: { stages: JourneyStage[] }) {
  if (stages.length === 0) {
    return null;
  }
  return (
    <ol>
      {stages.map((stage) => (
        <li key={stage.stage_id}>
          <StageArticle stage={stage} />
        </li>
      ))}
    </ol>
  );
}

function TechnicalRecord({
  card,
  record,
  includeJourney,
}: {
  card: RoundCard;
  record: DesignJourneyRecord;
  includeJourney: boolean;
}) {
  const stages = includeJourney ? record.stages : [];
  const planStages = stages.filter((stage) => PLAN_STAGE_IDS.has(stage.stage_id));
  const approvalStages = stages.filter((stage) => APPROVAL_STAGE_IDS.has(stage.stage_id));
  const specialistJourney = stages.filter((stage) => SPECIALIST_STAGE_IDS.has(stage.stage_id));
  const executionStages = stages.filter(
    (stage) => !PLAN_STAGE_IDS.has(stage.stage_id) && !APPROVAL_STAGE_IDS.has(stage.stage_id) && !SPECIALIST_STAGE_IDS.has(stage.stage_id),
  );
  const covered = new Set(specialistJourney.map((stage) => stage.stage_id));
  const cardSpecialists = (card.specialist_stages ?? []).filter((stage) => !covered.has(stage.stage_id));
  return (
    <>
      <section className="technical-section" aria-label="Plan">
        <h5>Plan</h5>
        <RoundDetailFields card={card} record={record} section="plan" />
        <StageGroup stages={planStages} />
      </section>
      <section className="technical-section" aria-label="Approval and authorization">
        <h5>Approval and authorization</h5>
        <RoundDetailFields card={card} record={record} section="approval" />
        {reviewNote(card, record) ? <p>{reviewNote(card, record)}</p> : null}
        <p>Recorded review {reviewLabel(card, record)}. Execution authorization {authorizationLabel(card, record)}.</p>
        <StageGroup stages={approvalStages} />
        {includeJourney && record.approval_history && record.approval_history.length > 0 ? (
          <section aria-label="Approval history">
            <h4>Approval history</h4>
            <p>A new plan revision requires approval. Earlier approvals stay on the revisions they recorded.</p>
            <ul>
              {record.approval_history.map((item) => (
                <li key={`${item.revision}-${item.digest}`}>
                  Revision {item.revision ?? "none"}. Digest {item.digest ?? "none"}. Reviewer{" "}
                  {item.reviewer_id ?? "none"}. Scope {item.scope ?? "none"}.
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {includeJourney && record.execution_review ? <ExecutionReviewPanel review={record.execution_review} /> : null}
      </section>
      <section className="technical-section" aria-label="Execution">
        <h5>Execution</h5>
        <RoundDetailFields card={card} record={record} section="execution" />
        {card.status === "cancelled" ? null : (
          <>
            {card.attempt_id ? <p>Attempt {card.attempt_id}.</p> : null}
            <p>{budgetSentence(card)}</p>
            <p>Execution completed: {card.execution_completed ? "yes" : "no"}.</p>
            <p>Successful candidate generation: {card.successful_candidate_generation ? "yes" : "no"}.</p>
            <p>Budget usage {budgetText(card.budget_usage)}.</p>
            {card.budget_compliance_established ? <p>Budget compliance established.</p> : null}
            <p>Scientific validation is not established. Experimental validation is not established.</p>
            {card.post_generation ? <p>{postProcessingText(card)}</p> : null}
          </>
        )}
        {card.post_generation?.events?.length ? (
          <>
            <h5>Post-generation events</h5>
            <ol>
              {card.post_generation.events.map((event) => (
                <li key={event.sequence ?? event.at}>
                  {event.sequence ?? ""}. {event.at ?? "time not recorded"}. {event.kind ?? event.status}. {event.detail}
                </li>
              ))}
            </ol>
          </>
        ) : null}
        <StageGroup stages={executionStages} />
        {includeJourney && record.execution ? <ExecutionOutcome execution={record.execution} /> : null}
        {includeJourney ? (
          <p>
            Journey {record.journey_id}. Request {record.request_id} revision {record.request_revision}.
            Plan {record.plan_id ?? "none"} revision {record.plan_revision ?? "none"}. Saved journey GPU
            execution {record.gpu_execution}. Approved {String(record.approved)}. Specialist agents executed{" "}
            {String(record.specialist_agents_executed)}.
          </p>
        ) : null}
      </section>
      <section className="technical-section" aria-label="Specialists">
        <h5>Specialists</h5>
        {card.status === "cancelled" ? null : (card.specialist_stages ?? []).length > 0 ? (
          (card.specialist_stages ?? []).map((stage) => (
            <p key={`${card.round_id}-${stage.stage_id}-summary`}>{specialistSummary(stage)}</p>
          ))
        ) : (
          <p>Specialists have not run for this round.</p>
        )}
        {timestampObservation(card) ? <p>{timestampObservation(card)}</p> : null}
        {cardSpecialists.map((stage) => (
          <article key={`${card.round_id}-${stage.stage_id}-detail`}>
            <h5>{stage.stage_id}</h5>
            <p>
              Implementation {stage.implementation} ({stage.implementation_kind}). Agent executed{" "}
              {String(stage.agent_executed)}. Started {stage.started_at ?? "not recorded"}. Completed{" "}
              {stage.completed_at ?? "not recorded"}.
            </p>
            <p>{stage.contribution ?? stage.blocking_reason ?? stage.failure_reason ?? "No contribution recorded."}</p>
            {stage.evidence_references.length > 0 ? (
              <ul>
                {stage.evidence_references.map((item, index) => (
                  <li key={`${stage.stage_id}-reference-${index}`}>{evidenceReference(item)}</li>
                ))}
              </ul>
            ) : null}
          </article>
        ))}
        <StageGroup stages={specialistJourney} />
        {includeJourney ? (
          <>
            <p>Second Brain is not an action in this pilot. Status {record.knowledge.status}. {record.knowledge.detail}</p>
            <p>Hermes is not an action in this pilot. Status {record.hermes.status}. {record.hermes.detail}</p>
          </>
        ) : null}
      </section>
    </>
  );
}

function roundHeadline(card: RoundCard): string {
  if (card.status === "cancelled") {
    return "Cancelled";
  }
  if (runningRound(card)) {
    return "In progress";
  }
  if (card.execution?.status === "budget_exhausted" && card.execution.accepted_candidate !== true) {
    return "Finished without an accepted candidate";
  }
  if (card.post_generation?.status === "failed" || card.execution?.status === "launch_failed") {
    return "Did not succeed";
  }
  if (resultsHref(card) || (card.execution_completed && card.successful_candidate_generation)) {
    return "Completed";
  }
  const label = card.outcome_label || roundStateText(card);
  return label.replace(/\.$/, "");
}

function roundFacts(card: RoundCard, record: DesignJourneyRecord): string {
  const target = card.target ?? recordedTarget(record) ?? "Target not recorded";
  const count = card.candidate_count;
  const candidates = typeof count === "number"
    ? `${count} ${count === 1 ? "candidate" : "candidates"}`
    : "candidates not recorded";
  const reviewed = plainReview(reviewLabel(card, record)) === "Review recorded";
  const authorized = plainAuthorization(authorizationLabel(card, record)) === "Execution authorized";
  const gate = reviewed && authorized
    ? "review and authorization recorded"
    : reviewed
      ? "review recorded"
      : authorized
        ? "authorization recorded"
        : "review and authorization not recorded";
  return `${target} · ${candidates} · ${gate}`;
}

function resultsHref(card: RoundCard): string | null {
  const attempt = card.execution?.attempt_id ?? card.attempt_id;
  const design = card.execution?.candidate_identity?.design;
  if (card.results_publication?.status !== "verified" || !attempt || !design) {
    return null;
  }
  const params = new URLSearchParams({
    area: "results",
    attempt,
    candidate: design,
    model: "model1",
  });
  return `/?${params.toString()}`;
}

function evidenceReference(item: JourneyStage["evidence_references"][number]): string {
  return [
    item.kind,
    item.journey_id,
    item.request_id ?? item.plan_id,
    item.attempt_id,
    item.candidate_id,
    item.revision !== undefined ? `revision ${item.revision}` : "",
    item.digest ? `digest ${item.digest}` : "",
  ]
    .filter((part) => part !== undefined && part !== null && part !== "")
    .join(" ");
}

function roundDigest(card: RoundCard): string | null {
  return card.digest || card.execution?.digest || card.approval?.digest || null;
}

function savedEvent(record: DesignJourneyRecord, kind: string, digest: string | null): boolean {
  if (!digest) {
    return false;
  }
  return (record.events ?? []).some((item) => item.kind === kind && item.digest === digest);
}

function reviewLabel(card: RoundCard, record: DesignJourneyRecord): string {
  const digest = roundDigest(card);
  if (card.approval?.digest && digest && card.approval.digest !== digest) {
    return savedEvent(record, "approval_recorded", digest) ? "recorded_plan" : "not_recorded";
  }
  if (card.recorded_review_status && card.recorded_review_status !== "not_recorded") {
    return card.recorded_review_status;
  }
  if (card.recorded_review_evidence === "journey_event" || savedEvent(record, "approval_recorded", digest)) {
    return "recorded_plan";
  }
  return "not_recorded";
}

function authorizationLabel(card: RoundCard, record: DesignJourneyRecord): string {
  const digest = roundDigest(card);
  if (
    card.execution_authorization?.digest
    && digest
    && card.execution_authorization.digest !== digest
  ) {
    return savedEvent(record, "execution_authorized", digest) ? "authorized" : "not_authorized";
  }
  if (card.execution_authorization_status === "authorized") {
    return "authorized";
  }
  if (
    card.execution_authorization_evidence === "journey_event"
    || savedEvent(record, "execution_authorized", digest)
  ) {
    return "authorized";
  }
  return "not_authorized";
}

function reviewNote(card: RoundCard, record: DesignJourneyRecord): string | null {
  const digest = roundDigest(card);
  const fromEvent = card.recorded_review_evidence === "journey_event" || (
    !card.approval && savedEvent(record, "approval_recorded", digest)
  );
  if (!fromEvent || reviewLabel(card, record) === "not_recorded") {
    return null;
  }
  return "Saved approval event matches this round's digest. This round does not store the reviewer.";
}

function explicitGpuIndex(value: unknown): string | null {
  if (typeof value !== "string" || !/^[0-7]$/.test(value)) {
    return null;
  }
  return value;
}

function boundApprovedGpu(card: RoundCard, record: DesignJourneyRecord): string | null {
  const recorded = explicitGpuIndex(card.approved_gpu);
  if (recorded) {
    return recorded;
  }
  const approval = card.approval;
  const review = record.execution_review;
  const gpu = explicitGpuIndex(review?.resource_budget?.gpu);
  if (!approval || !review || !gpu) {
    return null;
  }
  if (
    approval.plan_id !== card.plan_id
    || approval.revision !== card.plan_revision
    || !approval.digest
    || approval.digest !== card.digest
    || review.plan_id !== card.plan_id
    || review.revision !== card.plan_revision
    || review.digest !== card.digest
  ) {
    return null;
  }
  return gpu;
}

function gpuAssignmentText(card: RoundCard, record: DesignJourneyRecord): string {
  const proposal = card.gpu_proposal && card.gpu_proposal.trim() ? card.gpu_proposal : "not selected";
  const inherited = card.gpu_proposal_historical === true || card.gpu_proposal_source === "previous_round";
  const proposalText = inherited
    ? `Historical GPU proposal ${proposal}. It is not this round's approved GPU.`
    : `GPU proposal ${proposal}.`;
  const approvedGpu = boundApprovedGpu(card, record);
  const approved = approvedGpu
    ? `Approved GPU ${approvedGpu}.`
    : "Approved GPU is not recorded on this round.";
  const launched = card.launch_gpu_index != null
    ? `Launch GPU ${card.launch_gpu_index}.`
    : "Launch GPU is not recorded.";
  const finished = card.execution_completed === true || card.execution?.consumed === true || card.status === "cancelled";
  const choice = finished
    ? "This round does not ask for another GPU index."
    : "Choose an explicit index on this plan before launch.";
  return `${proposalText} ${approved} ${launched} ${choice}`;
}

function budgetSentence(card: RoundCard): string {
  const planned = card.sequence_limits?.max_trajectories;
  const executed = card.execution?.budget_usage?.requested_attempted_trajectories
    ?? card.budget_usage?.requested_attempted_trajectories;
  const launched = card.execution?.consumed === true || card.execution_completed === true;
  if (!launched) {
    return typeof planned === "number"
      ? `Planned recorded attempted-trajectory budget ${planned}.`
      : "This card does not record a planned attempted-trajectory budget.";
  }
  if (typeof executed === "number") {
    return `Executed attempted-trajectory budget ${executed}.`;
  }
  return typeof planned === "number"
    ? `Planned recorded attempted-trajectory budget ${planned}. The executed budget is not recorded.`
    : "This card does not record an attempted-trajectory budget.";
}

function postProcessingText(card: RoundCard): string {
  const post = card.post_generation;
  if (!post) {
    return "";
  }
  if (post.phase === "generation" && post.status === "queued") {
    return "Generation is queued. Scoring has not started.";
  }
  if (post.phase === "generation" && post.status === "running") {
    return "Generation is running. Scoring waits until generation finishes.";
  }
  if (post.status === "failed" && post.phase === "publication") {
    return "Results were not published. Scores already recorded on this round stay. This is not a successful publication.";
  }
  if (post.status === "failed") {
    return `This round stopped during ${post.phase ?? "processing"}. It did not succeed.`;
  }
  if (post.status === "unresolved" || post.status === "refused") {
    return "This round is unresolved. It is not a successful result.";
  }
  if (post.status === "completed" && post.phase === "specialist") {
    return "Generation finished. Scoring, ranking, critique, and prioritization are recorded for this round.";
  }
  if (post.status === "running" && post.phase === "specialist") {
    return "Scoring and review are running. Generation is no longer the current step.";
  }
  return `${post.phase ?? "Processing"} is ${post.status ?? "not recorded"}.`;
}

function runningRound(card: RoundCard): boolean {
  const post = card.post_generation?.status;
  if (post === "queued" || post === "running") {
    return true;
  }
  return (card.specialist_stages ?? []).some((stage) => stage.status === "running");
}

function stageLabel(stageId: string): string {
  const names: Record<string, string> = {
    generation: "Generation",
    scoring: "Scoring",
    ranking: "Ranking",
    critique: "Critique",
    prioritization: "Prioritization",
  };
  return names[stageId] ?? stageId;
}

function roundStateText(card: RoundCard): string {
  if (card.status === "cancelled") {
    return "Cancelled.";
  }
  if (runningRound(card)) {
    return "In progress.";
  }
  if (card.outcome_label) {
    return card.outcome_label;
  }
  if (card.execution?.status === "budget_exhausted" && card.execution.accepted_candidate !== true) {
    return "Finished without an accepted candidate.";
  }
  if (card.post_generation?.status === "failed") {
    return "Finished with a failure.";
  }
  if (card.execution_completed && card.successful_candidate_generation) {
    return "Finished with a generated candidate.";
  }
  return "No launch recorded.";
}

function plainReview(status: string): string {
  if (status === "recorded_plan" || status === "recorded") {
    return "Review recorded";
  }
  return "Review not recorded";
}

function plainAuthorization(status: string): string {
  return status === "authorized" ? "Execution authorized" : "Execution not authorized";
}

function cardNextAction(card: RoundCard, record: DesignJourneyRecord): string {
  if (runningRound(card)) {
    return "Follow this round. No further launch is available.";
  }
  if (resultsHref(card)) {
    return "Open View results for this attempt.";
  }
  if (card.execution?.status === "budget_exhausted" && card.execution.accepted_candidate !== true) {
    return "No candidate was accepted. This is not a successful design.";
  }
  if (card.post_generation?.status === "failed") {
    return "Inspect the failure in Technical details. Do not treat this round as published.";
  }
  if (card.execution?.consumed === true && card.execution_completed !== true) {
    return "Follow the recorded execution.";
  }
  if (plainReview(reviewLabel(card, record)) === "Review not recorded") {
    return "Record approval before authorization.";
  }
  if (plainAuthorization(authorizationLabel(card, record)) === "Execution not authorized") {
    return "Authorize execution. Authorization does not launch.";
  }
  if (card.execution?.consumed !== true) {
    return "Start execution only after authorization, on the approved GPU.";
  }
  return "No further action is recorded for this round.";
}

function professorStep(record: DesignJourneyRecord): { title: string; detail: string; blockers: string[] } {
  const review = record.execution_review;
  const blockers: string[] = [];
  const questions = Array.isArray(review?.questions) ? review.questions : [];
  if (!review) {
    blockers.push("The execution review is missing from this response. That absence is not an approval.");
  }
  if (questions.length > 0) {
    blockers.push("Information is missing. The plan still has open questions.");
  }
  for (const item of review?.unresolved_blockers ?? []) {
    if (item === "open_questions") {
      continue;
    }
    if (item.includes("input") || item.includes("unverified") || item.includes("acknowledgement")) {
      blockers.push(`Information is missing: ${item}.`);
    } else {
      blockers.push(`The plan is blocked: ${item}.`);
    }
  }
  const configuration = review?.configuration_blockers ?? [];
  const serverDisabled = review?.server_execution === "disabled" || configuration.includes("demonstration_not_configured");
  if (serverDisabled) {
    blockers.push("New executions are disabled. Saved results remain available.");
  }
  const active = (record.rounds ?? []).find((card) => card.active !== false);
  if (active?.status === "cancelled") {
    return {
      title: "This round is cancelled",
      detail: "A cancelled round cannot be approved, authorized, or launched.",
      blockers,
    };
  }
  if (active && runningRound(active)) {
    const scoring = active.post_generation?.phase === "specialist" || (active.specialist_stages ?? []).some((stage) => stage.status === "running");
    return {
      title: scoring ? "Follow scoring and review" : "Follow generation",
      detail: scoring
        ? "Generation has produced its recorded state. Scoring and the following steps are still in progress."
        : "Generation is in progress. Scoring has not started.",
      blockers,
    };
  }
  if (active?.post_generation?.status === "failed" || active?.execution?.status === "launch_failed") {
    return {
      title: "This round did not succeed",
      detail: "The failure stays on the round. It is not a published result.",
      blockers,
    };
  }
  if (active?.execution?.status === "budget_exhausted" && active.execution.accepted_candidate !== true) {
    return {
      title: "No candidate was accepted",
      detail: "The round finished without an accepted candidate. That is not a successful design.",
      blockers,
    };
  }
  if (active && resultsHref(active)) {
    return {
      title: "Open the result",
      detail: "The attempt is recorded. Results shows the sequence, models, and persisted scores.",
      blockers,
    };
  }
  if (!record.approval && review?.recorded_plan_approval !== true) {
    blockers.push("Review is pending. Approval has not been recorded.");
  }
  if (questions.length > 0 || (review?.unresolved_blockers.length ?? 0) > 0) {
    return {
      title: "Review and save the plan",
      detail: "Save the missing plan fields before approval. Saving does not approve, authorize, or launch.",
      blockers,
    };
  }
  if (!record.execution_authorization && review?.execution_authorized !== true) {
    return {
      title: "Record approval, then authorize execution",
      detail: "Approval, authorization, and launch stay separate. Authorization does not start generation.",
      blockers,
    };
  }
  if (record.execution?.consumed !== true) {
    return {
      title: serverDisabled ? "Wait for the operator" : "Start execution",
      detail: serverDisabled
        ? "New executions are disabled. Saved results remain available."
        : "Launch uses the approved GPU index. It is a separate action from authorization.",
      blockers: serverDisabled ? blockers.filter((item) => item !== "New executions are disabled. Saved results remain available.") : blockers,
    };
  }
  return {
    title: "Follow the recorded round",
    detail: "The saved journey is shown below. This screen does not launch another attempt.",
    blockers,
  };
}

function planValue(source: Record<string, unknown> | undefined, name: string): string {
  const value = source?.[name];
  if (value == null || value === "") {
    return "not recorded";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function PlanSummary({ record }: { record: DesignJourneyRecord }) {
  const planning = record.stages.find((stage) => stage.stage_id === "generation_planning");
  const output = planning?.output;
  const parameters = output && typeof output.parameters === "object" && !Array.isArray(output.parameters)
    ? output.parameters as Record<string, unknown>
    : undefined;
  const budget = record.execution_review?.resource_budget;
  const objective = requestTextOf(record);
  const beforeApproval = !record.approval && record.execution_review?.recorded_plan_approval !== true;
  const target = planValue(parameters, "target_identity") !== "not recorded"
    ? planValue(parameters, "target_identity")
    : recordedTarget(record) ?? "not recorded";
  return (
    <section aria-label="Plan summary">
      <h5>Plan summary</h5>
      <dl>
        <div><dt>Target</dt><dd>{target}</dd></div>
        <div><dt>Length</dt><dd>{planValue(parameters, "binder_length")}</dd></div>
        <div><dt>Hotspot</dt><dd>{planValue(parameters, "hotspot_assumption")}</dd></div>
        <div><dt>Attempted-trajectory budget</dt><dd>{planValue(budget, "max_trajectories")}</dd></div>
        {beforeApproval ? (
          <>
            <div><dt>Objective</dt><dd>{objective || "not recorded"}</dd></div>
            <div><dt>Filter policy</dt><dd>{planValue(parameters, "filter_policy")}</dd></div>
            <div><dt>Seed</dt><dd>{planValue(parameters, "seed")}</dd></div>
          </>
        ) : null}
      </dl>
      <details open={beforeApproval}>
        <summary>Computational resources</summary>
        <dl>
          <div><dt>GPU</dt><dd>{planValue(budget, "gpu")}</dd></div>
          <div><dt>CPU</dt><dd>{planValue(budget, "cpu_count")}</dd></div>
          <div><dt>Memory MiB</dt><dd>{planValue(budget, "memory_mib")}</dd></div>
          <div><dt>Deadline seconds</dt><dd>{planValue(budget, "execution_deadline_seconds")}</dd></div>
        </dl>
      </details>
      <details open={beforeApproval}>
        <summary>Advanced plan fields</summary>
        <dl>
          <div><dt>Plan</dt><dd>{record.plan_id ?? "not recorded"} revision {record.plan_revision ?? "not recorded"}</dd></div>
          {beforeApproval ? null : (
            <>
              <div><dt>Objective</dt><dd>{objective || "not recorded"}</dd></div>
              <div><dt>Filter policy</dt><dd>{planValue(parameters, "filter_policy")}</dd></div>
              <div><dt>Seed</dt><dd>{planValue(parameters, "seed")}</dd></div>
            </>
          )}
          <div><dt>Sequences</dt><dd>{planValue(budget, "num_seqs")}</dd></div>
          <div><dt>MPNN sequences</dt><dd>{planValue(budget, "max_mpnn_sequences")}</dd></div>
        </dl>
      </details>
    </section>
  );
}

function specialistSummary(stage: JourneyStage): string {
  if (stage.stage_id === "generation") {
    const normalized = stage.output?.protein_generation_agent_executed === true || stage.agent_executed === true;
    return normalized
      ? "Generation completed. BindCraft produced the artifacts on the GPU. ProteinGenerationAgent normalized those saved artifacts for this round."
      : "Generation recorded. BindCraft or the launcher produced the outcome. ProteinGenerationAgent did not normalize this round.";
  }
  if (stage.stage_id === "scoring") {
    return stage.status === "completed"
      ? "Scoring completed. Scoring calculated raw ipSAE for this round. BindCraft i_pTM was not used."
      : "Scoring did not run. No ranking_score was calculated. BindCraft i_pTM is not ipSAE.";
  }
  const names: Record<string, string> = {
    ranking: "Ranking",
    critique: "Critique",
    prioritization: "Prioritization",
  };
  const name = names[stage.stage_id] ?? stage.stage_id;
  if (stage.status === "completed") {
    return `${name} completed. ${name} ran a deterministic implementation for this round.`;
  }
  return `${name} ${stage.status}. It has not completed for this round.`;
}

function timestampObservation(card: RoundCard): string | null {
  if (card.specialist_timestamp_observation) {
    return card.specialist_timestamp_observation;
  }
  const stages = card.specialist_stages ?? [];
  const generation = stages.find((stage) => stage.stage_id === "generation");
  const scoring = stages.find((stage) => stage.stage_id === "scoring");
  const generatedStart = generation?.started_at;
  const generatedEnd = generation?.completed_at;
  const scoredStart = scoring?.started_at;
  if (!generatedStart || !generatedEnd || !scoredStart) {
    return null;
  }
  const laterEnds = ["ranking", "critique", "prioritization"].flatMap((stageId) => {
    const completed = stages.find((stage) => stage.stage_id === stageId)?.completed_at;
    return completed ? [completed] : [];
  });
  const parts: string[] = [];
  if (generatedStart > scoredStart) {
    parts.push("Scoring starts before the recorded normalization start.");
  } else if (generatedEnd > scoredStart) {
    parts.push("The recorded normalization end is after scoring starts.");
  }
  if (laterEnds.some((completed) => completed < generatedEnd)) {
    parts.push("Ranking, critique, or prioritization finish before the recorded normalization end.");
  }
  if (parts.length === 0) {
    return null;
  }
  return `Saved timestamps overlap for this round. ${parts.join(" ")} These saved timestamps were not rewritten.`;
}

function gpuProposalNote(card: RoundCard, record: DesignJourneyRecord): string {
  return `The technical demonstration profile does not assign a host GPU. ${gpuAssignmentText(card, record)} ${budgetSentence(card)}`;
}

function activeRoundCancelled(record: DesignJourneyRecord): boolean {
  return (record.rounds ?? []).some((round) => round.active && round.status === "cancelled");
}

function openReplacement(cards: RoundCard[], card: RoundCard): RoundCard | undefined {
  if (card.child_round_id) {
    const pointed = cards.find((item) => item.round_id === card.child_round_id);
    if (pointed && pointed.status !== "cancelled") {
      return pointed;
    }
  }
  return cards.find((item) => item.parent_round_id === card.round_id && item.status !== "cancelled");
}

function recordedTarget(record: DesignJourneyRecord): string | null {
  const planning = record.stages.find((stage) => stage.stage_id === "generation_planning");
  const output = planning?.output;
  if (!output) {
    return null;
  }
  for (const group of [output.settings, output.parameters, output.resource_limits]) {
    if (!group || typeof group !== "object" || Array.isArray(group)) {
      continue;
    }
    const value = (group as Record<string, unknown>).target_identity;
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return null;
}

function budgetText(usage: BudgetUsage | null | undefined): string {
  if (!usage) {
    return "not recorded";
  }
  const attempted = usage.attempted_trajectories ?? "not recorded";
  const low = usage.low_confidence_trajectories ?? "not recorded";
  const relaxed = usage.relaxed_trajectories ?? "not recorded";
  return `${attempted} attempted, ${low} LowConfidence, ${relaxed} relaxed`;
}

function syntheticRound(record: DesignJourneyRecord): RoundCard {
  const execution = record.execution;
  const review = record.execution_review;
  const gpu = typeof review?.resource_budget?.gpu === "string" ? review.resource_budget.gpu : null;
  const accepted = execution?.accepted_candidate === true;
  const budget = execution?.status === "budget_exhausted";
  return {
    round_id: `round_${record.journey_id}_1`,
    parent_round_id: null,
    child_round_id: null,
    status: "open",
    cancelled_at: null,
    cancellable: false,
    ordinal: 1,
    active: true,
    plan_id: record.plan_id,
    plan_revision: record.plan_revision,
    digest: review?.digest ?? null,
    target: recordedTarget(record),
    outcome: execution?.status ?? null,
    outcome_label: budget
      ? "Budget exhausted without an accepted candidate."
      : execution?.successful_candidate_generation
        ? "Successful candidate generation."
        : execution?.terminal
          ? "Execution completed without a successful candidate."
          : execution?.status === "execution_unresolved"
            ? "Execution unresolved. It was not replayed."
            : execution?.consumed
              ? "Execution is still recorded as running."
              : "No launch recorded.",
    execution_completed: execution?.terminal === true,
    successful_candidate_generation: execution?.successful_candidate_generation === true,
    candidate_count: accepted ? 1 : 0,
    budget_usage: execution?.budget_usage ?? null,
    budget_compliance_established: execution?.budget_compliance_established === true,
    scientific_validation_established: false,
    recorded_review_status: record.approval?.scope === "recorded_plan"
      ? "recorded_plan"
      : record.approval
        ? "recorded"
        : "not_recorded",
    execution_authorization_status: record.execution_authorization ? "authorized" : "not_authorized",
    approved_gpu: record.approval ? gpu : null,
    launch_gpu_index: execution?.gpu_index ?? null,
    gpu_proposal: gpu,
    profile_gpu: null,
    profile_accepts_gpu_proposal: assignedGpuIndex(gpu) !== null,
    host_gpu_index: null,
    profile_restriction:
      "The technical demonstration profile does not assign a host GPU. Choose an explicit GPU index on this plan.",
    attempt_id: execution?.attempt_id ?? execution?.result_link?.attempt_id ?? null,
    approval: record.approval ?? null,
    execution_authorization: record.execution_authorization ?? null,
    execution: execution ?? null,
  };
}

function PlanReview({
  output,
  limitations,
}: {
  output: Record<string, unknown>;
  limitations: string[];
}) {
  return (
    <>
      <dl>
        <div>
          <dt>Plan revision</dt>
          <dd>{String(output.plan_revision ?? "not recorded")}</dd>
        </div>
        <div>
          <dt>Digest</dt>
          <dd>{String(output.digest ?? "not recorded")}</dd>
        </div>
      </dl>
      <ReviewGroup title="Inputs" value={output.inputs} />
      <ReviewGroup title="Parameters" value={output.parameters} />
      <ReviewGroup title="Resource limits" value={output.resource_limits} />
      <h5>Limitations</h5>
      {limitations.map((item) => (
        <p key={item}>{item}</p>
      ))}
    </>
  );
}

function RequestEvidence({ output }: { output: Record<string, unknown> }) {
  const conflicts = conflictList(output.conflicts);
  const historical = conflictList(output.historical_conflicts);
  const evidence = acknowledgementEvidence(output.acknowledgement_evidence);
  return (
    <>
      <dl>
        <div>
          <dt>Request text</dt>
          <dd>{String(output.request_text ?? "not recorded")}</dd>
        </div>
        <div>
          <dt>Plan request text</dt>
          <dd>
            {evidence && "plan_request_text" in evidence
              ? evidence.plan_request_text || "empty"
              : "The plan request text was not included in this response."}
          </dd>
        </div>
        <div>
          <dt>Filter acknowledgement</dt>
          <dd>
            {evidence?.recorded && evidence.text
              ? evidence.text
              : evidence
                ? "The technical demonstration acknowledgement is not recorded on this plan revision."
                : "The plan request text was not included in this response."}
          </dd>
        </div>
      </dl>
      {evidence?.recorded ? (
        <p>The current plan records this acknowledgement. no_filters does not establish scientific quality.</p>
      ) : null}
      <ConflictList title="Current conflicts" conflicts={conflicts} />
      <ConflictList title="Historical conflicts" conflicts={historical} historical />
    </>
  );
}

function ConflictList({
  title,
  conflicts,
  historical = false,
}: {
  title: string;
  conflicts: JourneyConflict[];
  historical?: boolean;
}) {
  if (conflicts.length === 0) {
    return null;
  }
  return (
    <>
      <h5>{title}</h5>
      <ul>
        {conflicts.map((item) => (
          <li key={`${item.field}-${item.conflict}`}>
            {historical ? "Historical conflict. " : ""}
            {item.detail ? `${item.detail} ` : ""}
            {item.field}: {item.conflict}
            {item.plan_revision !== undefined ? ` Revision ${item.plan_revision}.` : ""}
            {item.digest ? ` Digest ${item.digest}.` : ""}
          </li>
        ))}
      </ul>
    </>
  );
}

function conflictList(value: unknown): JourneyConflict[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is JourneyConflict => Boolean(item) && typeof item === "object");
}

function acknowledgementEvidence(value: unknown): AcknowledgementEvidence | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  return value as AcknowledgementEvidence;
}

function shown(item: unknown): ReactNode {
  if (item === null || item === undefined) {
    return "not recorded";
  }
  if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
    return String(item);
  }
  if (Array.isArray(item)) {
    if (item.length === 0) {
      return "none";
    }
    return (
      <ul>
        {item.map((entry, index) => (
          <li key={index}>{shown(entry)}</li>
        ))}
      </ul>
    );
  }
  const entries = Object.entries(item as Record<string, unknown>);
  if (entries.length === 0) {
    return "not recorded";
  }
  return (
    <dl>
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{shown(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ReviewGroup({ title, value }: { title: string; value: unknown }) {
  const entries = value && typeof value === "object" ? Object.entries(value) : [];
  return (
    <>
      <h5>{title}</h5>
      <dl>
        {entries.map(([key, item]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{shown(item)}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

function authorizationOffered(record: DesignJourneyRecord): boolean {
  const review = record.execution_review;
  if (!review || review.execution_authorized) {
    return false;
  }
  if (review.authorization_available === true) {
    return true;
  }
  if (review.authorization_available === false) {
    return false;
  }
  const stage = record.stages.find((item) => item.stage_id === "execution_authorization");
  return (
    stage?.status === "pending" &&
    !review.recorded_plan_approval &&
    review.unresolved_blockers.length === 0
  );
}

function ExecutionReviewPanel({ review }: { review: ExecutionReview }) {
  const configuration = review.configuration_blockers ?? [];
  return (
    <section aria-label="Execution review">
      <h4>Execution review</h4>
      <p>Recorded plan approval does not authorize execution. Authorization does not launch generation.</p>
      <dl>
        {review.server_execution ? (
          <div>
            <dt>Server execution</dt>
            <dd>{review.server_execution}</dd>
          </div>
        ) : null}
        {review.journey_gpu_execution ? (
          <div>
            <dt>Saved journey GPU execution</dt>
            <dd>{review.journey_gpu_execution}</dd>
          </div>
        ) : null}
        <div>
          <dt>Plan</dt>
          <dd>{review.plan_id ?? "not recorded"}</dd>
        </div>
        <div>
          <dt>Revision</dt>
          <dd>{review.revision ?? "not recorded"}</dd>
        </div>
        <div>
          <dt>Digest</dt>
          <dd>{review.digest ?? "not recorded"}</dd>
        </div>
        <div>
          <dt>Input registration</dt>
          <dd>{review.input_registration ?? "not recorded"}</dd>
        </div>
      </dl>
      <h5>Verified input identities</h5>
      {Array.isArray(review.input_identities) ? (
        <ul>
          {review.input_identities.map((item, index) => (
            <li key={`identity-${index}`}>{shown(item)}</li>
          ))}
        </ul>
      ) : (
        <p>{shown(review.input_identities)}</p>
      )}
      <ReviewGroup title="Resource budget" value={review.resource_budget} />
      <h5>Unresolved blockers</h5>
      {review.unresolved_blockers.length > 0 ? (
        <ul>
          {review.unresolved_blockers.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>None.</p>
      )}
      {review.server_execution ? (
        <p>Saved journey GPU execution is historical. Server execution is the current process mode.</p>
      ) : null}
      {configuration.length > 0 ? (
        <>
          <h5>Configuration blockers</h5>
          <ul>
            {configuration.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

function ExecutionOutcome({ execution }: { execution: JourneyExecution }) {
  const link = execution.result_link;
  const exhausted = execution.status === "budget_exhausted" && execution.accepted_candidate !== true;
  const counters = execution.failure_counters;
  return (
    <section aria-label="Execution outcome">
      <h4>Execution outcome</h4>
      {exhausted ? <p>Budget exhausted without an accepted candidate.</p> : null}
      <dl>
        <div>
          <dt>Status</dt>
          <dd>{execution.status ?? "not recorded"}</dd>
        </div>
        <div>
          <dt>Terminal</dt>
          <dd>{String(execution.terminal === true)}</dd>
        </div>
        <div>
          <dt>Accepted candidate</dt>
          <dd>{String(execution.accepted_candidate === true)}</dd>
        </div>
        <div>
          <dt>Successful candidate generation</dt>
          <dd>{String(execution.successful_candidate_generation === true)}</dd>
        </div>
        <div>
          <dt>Termination reason</dt>
          <dd>{execution.termination_reason ?? "not recorded"}</dd>
        </div>
        <div>
          <dt>Budget compliance</dt>
          <dd>{String(execution.budget_compliance_established === true)}</dd>
        </div>
        <div>
          <dt>Scientific validation</dt>
          <dd>{String(execution.scientific_validation_established === true)}</dd>
        </div>
        <div>
          <dt>Result link</dt>
          <dd>
            {link
              ? `${link.journey_id ?? "none"} ${link.attempt_id ?? "none"} ${link.candidate_id ?? "none"}`
              : "not recorded"}
          </dd>
        </div>
      </dl>
      {counters && Object.keys(counters).length > 0 ? (
        <>
          <h5>Failure counters</h5>
          <p>These failure counters are not metric scores.</p>
          <ul>
            {Object.entries(counters).map(([name, value]) => (
              <li key={name}>
                {name} {value}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <h5>Progress</h5>
      <ul>
        {(execution.progress ?? []).map((item, index) => (
          <li key={`${item.status ?? "progress"}-${index}`}>{item.status ?? "not recorded"}</li>
        ))}
      </ul>
    </section>
  );
}

function ExecutionAuthorizationForm({
  journeyId,
  review,
  onAuthorized,
  onRejected,
}: {
  journeyId: string;
  review: ExecutionReview;
  onAuthorized: (record: DesignJourneyRecord) => void;
  onRejected: (message: string) => void;
}) {
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const idempotencyKey = useRef(`execution_${crypto.randomUUID()}`);
  const ready = Boolean(review.plan_id && review.revision && review.digest && reviewer.trim() && reason.trim());

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || pending || !review.plan_id || review.revision === null) {
      return;
    }
    setPending(true);
    try {
      const response = await fetch(`/api/design-journeys/${journeyId}/execution-authorization`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          plan_id: review.plan_id,
          revision: review.revision,
          digest: review.digest,
          reviewer_id: reviewer.trim(),
          reason: reason.trim(),
          idempotency_key: idempotencyKey.current,
        }),
      });
      const body = (await response.json()) as Partial<DesignJourneyRecord> & { detail?: string };
      if (!response.ok || !body.journey_id || !Array.isArray(body.stages) || !body.execution_authorization) {
        onRejected(body.detail || "Execution was not authorized. Generation was not launched.");
        return;
      }
      onAuthorized(body as DesignJourneyRecord);
    } catch {
      onRejected("Execution was not authorized. Generation was not launched.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form aria-label="Authorize execution" onSubmit={(event) => void submit(event)}>
      <p>Authorize this revision for execution. This action does not launch generation.</p>
      <label>
        Execution reviewer
        <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} />
      </label>
      <label>
        Execution reason
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>
      <button type="submit" disabled={!ready || pending}>
        Authorize execution
      </button>
    </form>
  );
}

function assignedGpuIndex(value: unknown): string | null {
  return typeof value === "string" && /^(?:0|[1-7])$/.test(value) ? value : null;
}

function LaunchForm({
  journeyId,
  review,
  onLaunched,
  onRejected,
}: {
  journeyId: string;
  review: ExecutionReview;
  onLaunched: (record: DesignJourneyRecord) => void;
  onRejected: (message: string) => void;
}) {
  const assignedGpu = assignedGpuIndex(review.resource_budget.gpu);
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [gpuIndex, setGpuIndex] = useState(assignedGpu ?? "");
  const [pending, setPending] = useState(false);
  const idempotencyKey = useRef(`launch_${crypto.randomUUID()}`);
  const index = Number(gpuIndex);
  const ready = Boolean(
    review.plan_id &&
      review.revision &&
      review.digest &&
      reviewer.trim() &&
      reason.trim() &&
      Number.isInteger(index) &&
      index >= 0 &&
      index <= 7,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || pending || !review.plan_id || review.revision === null) {
      return;
    }
    setPending(true);
    try {
      const response = await fetch(`/api/design-journeys/${journeyId}/launch`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          plan_id: review.plan_id,
          revision: review.revision,
          digest: review.digest,
          reviewer_id: reviewer.trim(),
          reason: reason.trim(),
          idempotency_key: idempotencyKey.current,
          gpu_index: index,
        }),
      });
      const body = (await response.json()) as Partial<DesignJourneyRecord> & { detail?: string };
      if (!response.ok || !body.journey_id || !body.execution) {
        onRejected(body.detail || "The launch was not accepted.");
        return;
      }
      onLaunched(body as DesignJourneyRecord);
    } catch {
      onRejected("The launch was not accepted.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form aria-label="Launch authorized execution" onSubmit={(event) => void submit(event)}>
      <p>Launch is separate from authorization. The server rechecks this revision before starting.</p>
      <label>
        Launch reviewer
        <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} />
      </label>
      <label>
        Launch reason
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>
      <label>
        GPU index
        <input
          aria-label="GPU index"
          value={gpuIndex}
          onChange={(event) => setGpuIndex(event.target.value)}
          inputMode="numeric"
        />
      </label>
      <p>
        Approved GPU index {assignedGpu ?? "not selected"}. The launch request must use that same nvidia-smi
        index. The profile does not substitute another GPU.
      </p>
      <button type="submit" disabled={!ready || pending}>
        Launch execution
      </button>
    </form>
  );
}

function ApprovalForm({
  journeyId,
  planId,
  revision,
  digest,
  onApproved,
  onRejected,
}: {
  journeyId: string;
  planId: string | null;
  revision: number | null;
  digest: string;
  onApproved: (record: DesignJourneyRecord) => void;
  onRejected: (message: string) => void;
}) {
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const idempotencyKey = useRef(`approval_${crypto.randomUUID()}`);
  const ready = Boolean(planId && revision && digest && reviewer.trim() && reason.trim());

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || pending || !planId || revision === null) {
      return;
    }
    setPending(true);
    try {
      const response = await fetch(`/api/design-journeys/${journeyId}/approval`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          plan_id: planId,
          revision,
          digest,
          reviewer_id: reviewer.trim(),
          reason: reason.trim(),
          idempotency_key: idempotencyKey.current,
        }),
      });
      const body = (await response.json()) as Partial<DesignJourneyRecord> & { detail?: string };
      if (!response.ok || !body.journey_id || !Array.isArray(body.stages) || body.approved !== true) {
        onRejected(body.detail || "The approval was not recorded. GPU execution stays disabled.");
        return;
      }
      onApproved(body as DesignJourneyRecord);
    } catch {
      onRejected("The approval was not recorded. GPU execution stays disabled.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form aria-label="Record plan approval" onSubmit={(event) => void submit(event)}>
      <p>Review the saved plan above. Recording approval does not launch generation.</p>
      <label>
        Reviewer identity
        <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} />
      </label>
      <label>
        Reason
        <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>
      <button type="submit" disabled={!ready || pending}>
        Record approval
      </button>
    </form>
  );
}

const COMPLETION_FIELDS: { name: string; label: string; kind: "text" | "integer"; note?: string }[] = [
  { name: "target_id", label: "Request target", kind: "text" },
  { name: "target_identity", label: "Plan target", kind: "text" },
  { name: "objective", label: "Objective", kind: "text" },
  { name: "generator", label: "Generator", kind: "text" },
  { name: "n_candidates", label: "Candidate count", kind: "integer" },
  { name: "length_min", label: "Length minimum", kind: "integer" },
  { name: "length_max", label: "Length maximum", kind: "integer" },
  { name: "hotspot_assumption", label: "Hotspot assumption", kind: "text" },
  { name: "filter_policy", label: "Filter policy", kind: "text" },
  { name: "binder_length", label: "Binder length", kind: "integer" },
  { name: "seed", label: "Seed", kind: "integer" },
  { name: "cpu_count", label: "CPU count", kind: "integer" },
  {
    name: "memory_mib",
    label: "Host RAM (MiB)",
    kind: "integer",
    note: "Host RAM. This is not GPU VRAM.",
  },
  {
    name: "gpu",
    label: "GPU assignment",
    kind: "text",
    note: "Host GPU index. The technical demonstration profile does not assign one. This is not GPU VRAM. Leave this blank until an explicit index is chosen.",
  },
  { name: "execution_deadline_seconds", label: "Execution deadline (seconds)", kind: "integer" },
];

function PlanCompletion({
  record,
  onSaved,
  onRejected,
}: {
  record: DesignJourneyRecord;
  onSaved: (record: DesignJourneyRecord) => void;
  onRejected: (message: string) => void;
}) {
  const offer = record.completion?.technical_demonstration;
  const evidence = record.completion?.acknowledgement_evidence ?? record.acknowledgement_evidence;
  const acknowledgementRecorded = evidence?.recorded === true && Boolean(evidence.text);
  const [values, setValues] = useState<Record<string, string>>(() => savedValues(record));
  const [sequenceLimits, setSequenceLimits] = useState<Record<string, number> | null>(null);
  const [acknowledge, setAcknowledge] = useState(acknowledgementRecorded);
  const [saving, setSaving] = useState(false);
  const revisionKey = `${record.journey_id}:${record.plan_revision}`;

  useEffect(() => {
    setValues(savedValues(record));
    setSequenceLimits(null);
    setAcknowledge(acknowledgementRecorded);
  }, [revisionKey, record, acknowledgementRecorded]);

  function reviewPreset() {
    if (!offer) {
      return;
    }
    const next = { ...values };
    for (const item of offer.values) {
      if (!item.recordable || item.name === "sequence_limits") {
        continue;
      }
      next[item.name] = String(item.value);
    }
    setValues(next);
    const limits = offer.values.find((item) => item.name === "sequence_limits" && item.recordable);
    setSequenceLimits(
      limits && limits.value && typeof limits.value === "object"
        ? (limits.value as Record<string, number>)
        : null,
    );
    setAcknowledge(true);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving) {
      return;
    }
    const fields: {
      name: string;
      value: string | number | Record<string, number>;
      provenance: string;
      source?: string;
    }[] = COMPLETION_FIELDS.flatMap((field) => {
      const raw = values[field.name]?.trim() ?? "";
      if (!raw) {
        return [];
      }
      const value = field.kind === "integer" ? Number(raw) : raw;
      if (field.kind === "integer" && !Number.isInteger(value)) {
        return [];
      }
      const source = offer?.values.find(
        (item) => item.recordable && item.name === field.name && String(item.value) === raw,
      )?.source;
      return [{ name: field.name, value, provenance: "user", ...(source ? { source } : {}) }];
    });
    if (sequenceLimits) {
      const source = offer?.values.find((item) => item.name === "sequence_limits" && item.recordable)?.source;
      fields.push({
        name: "sequence_limits",
        value: sequenceLimits,
        provenance: "user",
        ...(source ? { source } : {}),
      });
    }
    const text = requestTextOf(record);
    setSaving(true);
    try {
      const response = await fetch("/api/design-journeys", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          request_id: record.request_id,
          request_revision: record.request_revision,
          request_text: text,
          fields,
          acknowledgements: acknowledge && values.filter_policy?.trim() === "no_filters" ? ["technical-smoke-test"] : [],
        }),
      });
      onSaved(await readJourney(response));
    } catch (error: unknown) {
      onRejected(
        error instanceof JourneyTransportError || error instanceof JourneyAbsent
          ? error.message
          : "The design journey could not be read.",
      );
    } finally {
      setSaving(false);
    }
  }

  const questions = planningQuestions(record);
  const conflicts = [...(record.completion?.conflicts ?? record.conflicts ?? []), ...(offer?.conflicts ?? [])];
  const historical = record.completion?.historical_conflicts ?? record.historical_conflicts ?? [];
  const sequenceNote = offer?.values.find((item) => item.name === "sequence_limits");

  return (
    <form aria-label="Complete saved plan" onSubmit={(event) => void save(event)}>
      <h4>Complete this plan</h4>
      <p>
        Saving records a new plan revision through the existing plan draft. The previous approval
        stays in the history. The new revision is not approved, not authorized, and does not launch
        generation. An empty field keeps the saved value.
      </p>
      {questions.length > 0 ? (
        <ul>
          {questions.map((item) => (
            <li key={item.field}>{item.question}</li>
          ))}
        </ul>
      ) : null}
      {conflicts.length > 0 ? (
        <ul>
          {conflicts.map((item) => (
            <li key={`${item.field}-${item.conflict}`}>
              {item.field}: {item.conflict}
            </li>
          ))}
        </ul>
      ) : null}
      <ConflictList title="Historical conflicts" conflicts={historical} historical />
      {acknowledgementRecorded && evidence?.text ? (
        <p>The current plan records this acknowledgement: {evidence.text}</p>
      ) : null}
      {offer ? (
        <>
          <p>
            {offer.name}. Profile {offer.profile_id}. Review these values before saving. This preset
            does not authorize execution and does not launch generation.
          </p>
          <p>{offer.acknowledgement}</p>
          <button type="button" onClick={reviewPreset}>
            Review technical demonstration values
          </button>
        </>
      ) : null}
      {sequenceNote && !sequenceNote.recordable ? <p>{sequenceNote.note}</p> : null}
      {COMPLETION_FIELDS.map((field) => (
        <label key={field.name}>
          {field.label}
          {field.note ? <span className="completion-note"> {field.note}</span> : null}
          <input
            value={values[field.name] ?? ""}
            onChange={(event) => setValues((current) => ({ ...current, [field.name]: event.target.value }))}
          />
        </label>
      ))}
      <label>
        <input
          type="checkbox"
          checked={acknowledge}
          onChange={(event) => setAcknowledge(event.target.checked)}
        />
        Record that no_filters is for the technical demonstration and does not establish scientific
        quality.
      </label>
      <button type="submit" disabled={saving}>
        Save plan changes
      </button>
    </form>
  );
}

function savedValues(record: DesignJourneyRecord): Record<string, string> {
  const planning = record.stages.find((stage) => stage.stage_id === "generation_planning");
  const output = planning?.output ?? {};
  const groups = [output.parameters, output.resource_limits, output.settings].filter(
    (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
  );
  const requestFields = record.completion?.request_fields ?? record.request_fields ?? {};
  const values: Record<string, string> = {};
  for (const field of COMPLETION_FIELDS) {
    const request = requestFields[field.name];
    if (request && request.value !== null && request.value !== undefined && request.value !== "") {
      values[field.name] = String(request.value);
      continue;
    }
    const found = groups.map((group) => group[field.name]).find((item) => item !== null && item !== undefined);
    values[field.name] = found === undefined ? "" : String(found);
  }
  return values;
}

function planningQuestions(record: DesignJourneyRecord): { field: string; question: string }[] {
  const planning = record.stages.find((stage) => stage.stage_id === "generation_planning");
  const questions = planning?.output?.questions;
  if (!Array.isArray(questions)) {
    return [];
  }
  return questions.flatMap((item) => {
    if (!item || typeof item !== "object") {
      return [];
    }
    const field = "field" in item ? String(item.field) : "";
    const question = "question" in item ? String(item.question) : "";
    return field && question ? [{ field, question }] : [];
  });
}

function requestTextOf(record: DesignJourneyRecord): string {
  const stage = record.stages.find((item) => item.stage_id === "design_request");
  const text = stage?.output?.request_text;
  return typeof text === "string" ? text : "";
}
