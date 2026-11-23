import { useCallback, useEffect, useRef, useState } from "react";
import { LESSON_MAX, lessonSentence, type RoundLesson, type RoundSeed, seedSentence } from "./rounds";

type Stage = {
  stage_id?: string;
  status?: string;
  blocking_reason?: string | null;
  output?: { ranking_signal?: string; ranking_score?: number; candidate_id?: string } | null;
};

type Round = {
  round_id: string;
  parent_round_id?: string | null;
  ordinal?: number;
  status?: string;
  plan_id?: string | null;
  plan_revision?: number | null;
  digest?: string | null;
  outcome_label?: string;
  execution_completed?: boolean;
  execution?: { status?: string; candidate_identity?: { design?: string } | null } | null;
  post_generation?: { status?: string } | null;
  specialist_stages?: Stage[];
  seeded_from?: RoundSeed | null;
  lesson?: RoundLesson | null;
};

type Journey = {
  journey_id: string;
  request_id: string;
  request_revision: number;
  rounds?: Round[];
};

const POLL_MS = 4000;

// The server's reason, in words. A refusal that only reloads the page teaches
// nothing about what to change.
const REFUSED: Record<string, string> = {
  plan_revision_conflict: "The plan changed after this round was opened. Reload and review it.",
  plan_identity_rejected: "That plan is not the active round of this request.",
  execution_not_permitted: "The server does not allow execution. Nothing was started.",
  configuration_incomplete: "The plan still has open questions. Nothing was started.",
  gpu_not_selected: "No GPU is selected on this plan. Nothing was started.",
  gpu_not_ready: "The GPU is not ready. Nothing was started.",
};

function describeScoring(round: Round): string | null {
  const scoring = (round.specialist_stages ?? []).find((stage) => stage.stage_id === "scoring");
  if (!scoring) {
    return null;
  }
  if (scoring.status === "completed" && scoring.output) {
    const { ranking_signal: signal, ranking_score: score, candidate_id: candidate } = scoring.output;
    return `Scored under ${signal ?? "an unnamed protocol"}${typeof score === "number" ? `: ${score}` : ""}${
      candidate ? ` for ${candidate}` : ""
    }.`;
  }
  return `Scoring ${scoring.status ?? "not recorded"}${scoring.blocking_reason ? `: ${scoring.blocking_reason}` : "."}`;
}

function isRunning(round: Round): boolean {
  const dispatch = round.post_generation?.status;
  return dispatch === "queued" || dispatch === "running" || round.execution?.status === "running";
}

/**
 * The rounds of one request, and the two things a person does between them:
 * open the next round, saying why it differs, and run it.
 *
 * It reads the same journey the server runs a launch through, so a round shown
 * here has the accepted candidate, the specialists' scoring and the seed the
 * server recorded — not a separate account of them.
 */
export function DesignRounds({ requestId }: { requestId: string }) {
  const [journey, setJourney] = useState<Journey | null>(null);
  const [lesson, setLesson] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const submitting = useRef(false);

  const read = useCallback(async (): Promise<Journey | null> => {
    try {
      const response = await fetch(`/api/design-journeys/by-request/${encodeURIComponent(requestId)}`);
      if (!response.ok) {
        return null;
      }
      const body = (await response.json()) as Journey;
      return body.request_id === requestId && Array.isArray(body.rounds) ? body : null;
    } catch {
      return null;
    }
  }, [requestId]);

  useEffect(() => {
    let cancelled = false;
    setJourney(null);
    setFailure(null);
    void read().then((found) => {
      if (!cancelled) {
        setJourney(found);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [read]);

  const rounds = (journey?.rounds ?? [])
    .filter((round) => round.status !== "cancelled")
    .sort((left, right) => (left.ordinal ?? 0) - (right.ordinal ?? 0));
  const last = rounds.length > 0 ? rounds[rounds.length - 1] : null;
  const anyRunning = rounds.some(isRunning);

  useEffect(() => {
    if (!anyRunning) {
      return;
    }
    let cancelled = false;
    const timer = window.setInterval(() => {
      void read().then((found) => {
        if (!cancelled && found) {
          setJourney(found);
        }
      });
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [anyRunning, read]);

  if (!journey || rounds.length === 0 || !last) {
    return null;
  }

  const canPrepare = last.execution_completed === true && !isRunning(last);
  const canRun = Boolean(last.parent_round_id) && !last.execution && !isRunning(last);

  async function post(url: string, body: unknown): Promise<Response | null> {
    if (submitting.current) {
      return null;
    }
    submitting.current = true;
    setPending(true);
    setFailure(null);
    try {
      return await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      setFailure("The server could not be reached. Nothing was started.");
      return null;
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  async function prepare() {
    if (!journey || !last) {
      return;
    }
    const text = lesson.trim();
    // Sent only when written: the route refuses an empty lesson, and a round
    // opened without one is a normal round.
    const response = await post(
      `/api/design-journeys/${journey.journey_id}/rounds`,
      text ? { parent_round_id: last.round_id, lesson: text } : { parent_round_id: last.round_id },
    );
    if (!response) {
      return;
    }
    const body = (await response.json()) as Journey & { detail?: string };
    if (!response.ok || !body.journey_id) {
      setFailure(body.detail || "The next round was not opened.");
      return;
    }
    setLesson("");
    setJourney(body);
  }

  async function run() {
    if (!journey || !last) {
      return;
    }
    const response = await post("/api/design-journeys/run", {
      request_id: journey.request_id,
      request_revision: journey.request_revision,
      plan_id: last.plan_id,
      revision: last.plan_revision,
      digest: last.digest,
      idempotency_key: `run_${last.plan_id}_${last.plan_revision}_${last.digest}`,
    });
    if (!response) {
      return;
    }
    const body = (await response.json()) as Journey & { status?: string };
    if (!response.ok) {
      setFailure(REFUSED[body.status ?? ""] ?? `The run was refused (${body.status ?? response.status}).`);
      return;
    }
    const found = await read();
    setJourney(found ?? body);
  }

  return (
    <section aria-label="Design rounds">
      <h3>Rounds</h3>
      <ol>
        {rounds.map((round) => {
          const design = round.execution?.candidate_identity?.design;
          const scoring = describeScoring(round);
          return (
            <li key={round.round_id} aria-label={`Round ${round.ordinal ?? ""}`}>
              <p>
                <strong>Round {round.ordinal ?? "?"}.</strong>{" "}
                {isRunning(round) ? "Running. The server is working on it." : (round.outcome_label ?? "Not recorded.")}
              </p>
              {round.parent_round_id ? (
                <p>
                  Starts from: {seedSentence(round)} Lesson: {lessonSentence(round)}
                </p>
              ) : null}
              {design ? <p>Accepted design {design}.</p> : null}
              {scoring ? <p>{scoring}</p> : null}
            </li>
          );
        })}
      </ol>
      {failure ? <p role="alert">{failure}</p> : null}
      {canPrepare ? (
        <p>
          <label htmlFor={`lesson-${last.round_id}`}>Why does the next round differ from this one? (optional)</label>
          <textarea
            id={`lesson-${last.round_id}`}
            rows={2}
            maxLength={LESSON_MAX}
            value={lesson}
            disabled={pending}
            onChange={(event) => setLesson(event.target.value)}
          />
          <small>
            {lesson.length} of {LESSON_MAX}. It is recorded beside what this round measured; leaving it empty
            records that none was declared.
          </small>
          <button type="button" disabled={pending} onClick={() => void prepare()}>
            Prepare next round
          </button>
        </p>
      ) : null}
      {canRun ? (
        <p>
          <button type="button" disabled={pending} onClick={() => void run()}>
            Run next round
          </button>
          This action will use the GPU.
        </p>
      ) : null}
    </section>
  );
}
