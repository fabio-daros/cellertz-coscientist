import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  analyzeDesignRequest,
  listDesignDrafts,
  listSupervisorAnalyses,
  modelNotExecuted,
  updateDesignDraft,
  type ListedDesignDraft,
  type SupervisorAnalysis,
  type SupervisorModelCall,
} from "../design-draft";
import { prototypeWorkspace } from "../fixture";

const SAVED_STATUSES = new Set(["draft_created", "draft_updated", "request_complete"]);

type SavedDraft = {
  requestId: string;
  revision: number;
};

/** Identity returned by the draft endpoint. Absent fields stay absent. */
function savedDraft(reply: Awaited<ReturnType<typeof updateDesignDraft>>): SavedDraft | null {
  const draft = reply.draft;
  if (!draft || !SAVED_STATUSES.has(reply.status)) {
    return null;
  }
  if (typeof draft.request_id !== "string" || draft.request_id.trim() === "") {
    return null;
  }
  if (!Number.isInteger(draft.revision) || draft.revision < 1) {
    return null;
  }
  return { requestId: draft.request_id, revision: draft.revision };
}

function remember(items: ListedDesignDraft[] | null, next: ListedDesignDraft): ListedDesignDraft[] {
  return [next, ...(items ?? []).filter((item) => item.requestId !== next.requestId)];
}

function analysisOutdated(
  analysis: SupervisorAnalysis,
  known: SavedDraft | null,
  savedMessage: string,
  intent: string,
): boolean {
  if (analysis.status !== "analyzed" && analysis.status !== "analysis_rejected") {
    return false;
  }
  if (analysis.current === false) {
    return true;
  }
  return (
    !known ||
    known.requestId !== analysis.requestId ||
    known.revision !== analysis.revision ||
    savedMessage !== analysis.message ||
    intent.trim() !== analysis.message
  );
}

function idleAnalysis(
  status: SupervisorAnalysis["status"],
  requestId: string,
  revision: number,
  message: string,
  summary: string,
): SupervisorAnalysis {
  return {
    status,
    current: false,
    analysisId: "",
    analyzedAt: null,
    requestId,
    revision,
    message,
    summary,
    modelCall: modelNotExecuted,
    extractedQuestions: [],
    supervisorQuestions: [],
    configuredChoices: [],
    generatorLimitation: null,
  };
}

function executedAt(value: string | null): string {
  if (!value) {
    return "Not reported";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const formatted = new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date);
  return `${formatted} UTC`;
}

function previousKey(item: SupervisorAnalysis, index: number): string {
  return item.analysisId || `${item.analyzedAt ?? "undated"}-${item.revision}-${index}`;
}

function UsageFields({ call, label = "Model usage" }: { call: SupervisorModelCall | null; label?: string }) {
  const executed = call?.executed === true;
  const model = !call ? "" : executed ? call.model : "Model not run";
  const token = (value: string | undefined) => (executed && value ? value : "");
  const input = token(call?.inputTokens);
  const output = token(call?.outputTokens);
  const total = token(call?.totalTokens);
  const fields = [
    ["model", model],
    ["input", input],
    ["output", output],
    ["total", total],
  ] as const;
  return (
    <div className="usage-fields" role="group" aria-label={label}>
      {call?.provider ? <span className="usage-provider">{call.provider}</span> : null}
      <div className="usage-boxes">
        {fields.map(([label, value]) => (
          <span key={label}>
            <span className="usage-label">{label}: </span>
            {value ? <span className="usage-value">{value}</span> : null}
          </span>
        ))}
      </div>
    </div>
  );
}

export function SupervisorMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="9" cy="8" r="3" />
      <path d="M3.8 19.2v-.8a4.2 4.2 0 0 1 4.2-4.2H11" />
      <path d="M15.2 14.4l1.7 1.7 3.5-3.6" />
    </svg>
  );
}

function SupervisorReview({
  analysis,
  outdated,
  answers,
  answering,
  suspended,
  answerNotice,
  onAnswer,
  onSend,
}: {
  analysis: SupervisorAnalysis;
  outdated: boolean;
  answers: Record<string, string>;
  answering: boolean;
  suspended: boolean;
  answerNotice: "error" | null;
  onAnswer: (key: string, value: string) => void;
  onSend: () => void;
}) {
  const configuredFields = new Set(analysis.configuredChoices.map((choice) => choice.field));
  const recordedQuestions = analysis.extractedQuestions.filter((item) => !configuredFields.has(item.field));
  const recordedFields = new Set(recordedQuestions.map((item) => item.field));
  const supervisorQuestions = analysis.supervisorQuestions.filter(
    (item) => !item.field || !recordedFields.has(item.field),
  );
  const answerable = [
    ...recordedQuestions.map((item) => item.field),
    ...supervisorQuestions.flatMap((item) => (item.field ? [item.field] : [])),
  ];
  const hasAnswer = answerable.some((field) => (answers[field] ?? "").trim().length > 0);
  return (
    <section className="supervisor-review" aria-label="Agent: Supervisor">
      <h3>
        <SupervisorMark />
        Agent: Supervisor
      </h3>
      <div className="execution-usage">
        <UsageFields call={analysis.modelCall} />
      </div>
      {analysis.status === "analyzed" || analysis.status === "analysis_rejected" ? (
        <p>Analysis of revision {analysis.revision}.</p>
      ) : null}
      {analysis.analyzedAt ? <p className="analysis-stamp">{analysis.analyzedAt}</p> : null}
      {outdated ? (
        <>
          <p>This analysis is out of date.</p>
          <p>Analyze the current revision before answering.</p>
        </>
      ) : null}
      <p>{analysis.summary}</p>
      {analysis.configuredChoices.map((choice) => (
        <p key={choice.field}>{choice.text}</p>
      ))}
      {analysis.generatorLimitation ? <p>{analysis.generatorLimitation}</p> : null}
      {recordedQuestions.length > 0 ? (
        <>
          <h4>Recorded gaps</h4>
          <p>These were already recorded. They are not a finding of the Supervisor.</p>
          {recordedQuestions.map((item) =>
            outdated ? (
              <p key={item.field}>{item.question}</p>
            ) : (
              <label key={item.field} htmlFor={`recorded-${item.field}`}>
                {item.question}
                <input
                  id={`recorded-${item.field}`}
                  value={answers[item.field] ?? ""}
                  onChange={(event) => onAnswer(item.field, event.target.value)}
                />
              </label>
            ),
          )}
        </>
      ) : null}
      {supervisorQuestions.length > 0 ? (
        <>
          <h4>Supervisor questions</h4>
          {supervisorQuestions.map((item) => {
            const field = item.field;
            return !outdated && field ? (
              <label key={item.question} htmlFor={`supervisor-${field}`}>
                {item.question}
                <input
                  id={`supervisor-${field}`}
                  value={answers[field] ?? ""}
                  onChange={(event) => onAnswer(field, event.target.value)}
                />
              </label>
            ) : (
              <p key={item.question}>{item.question}</p>
            );
          })}
        </>
      ) : null}
      {!outdated && answerable.length > 0 ? (
        <>
          <button type="button" disabled={answering || suspended || !hasAnswer} onClick={onSend}>
            {answering ? "Sending…" : "Send answers"}
          </button>
          {answerNotice === "error" ? <p>The answers were not saved.</p> : null}
        </>
      ) : null}
    </section>
  );
}

export type SelectedDesignRequest = {
  requestId: string;
  revision: number;
  message: string;
};

/** The opening request for a protein design. Saving stores a draft and nothing further. */
export function DesignRequest({
  onSelection,
}: {
  onSelection?: (selection: SelectedDesignRequest | null) => void;
}) {
  const [intent, setIntent] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<"saved" | "unchanged" | "error" | null>(null);
  const [cards, setCards] = useState<ListedDesignDraft[] | null>(null);
  const [listFailed, setListFailed] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<SupervisorAnalysis | null>(null);
  const [previousAnalyses, setPreviousAnalyses] = useState<SupervisorAnalysis[]>([]);
  const [openPreviousId, setOpenPreviousId] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [answering, setAnswering] = useState(false);
  const [answerNotice, setAnswerNotice] = useState<"error" | null>(null);
  const savingRef = useRef(false);
  const analyzingRef = useRef(false);
  const answeringRef = useRef(false);
  const savedRef = useRef<SavedDraft | null>(null);
  const analysisRef = useRef<SupervisorAnalysis | null>(null);
  const previousRef = useRef<SupervisorAnalysis[]>([]);
  const historyToken = useRef(0);
  const intentRef = useRef(intent);
  const onSelectionRef = useRef(onSelection);
  onSelectionRef.current = onSelection;
  analysisRef.current = analysis;
  previousRef.current = previousAnalyses;
  intentRef.current = intent;
  const hasIntent = intent.trim().length > 0;
  const describedBy =
    notice === "saved"
      ? "design-request-saved"
      : notice === "unchanged"
        ? "design-request-unchanged"
        : notice === "error"
          ? "design-request-error"
          : undefined;

  useEffect(() => {
    let active = true;
    listDesignDrafts()
      .then((items) => {
        if (!active) {
          return;
        }
        if (items === null) {
          setListFailed(true);
          setCards([]);
          return;
        }
        setCards(items);
        const requestId = new URLSearchParams(window.location.search).get("request");
        const item = requestId ? items.find((card) => card.requestId === requestId) : undefined;
        if (item && savedRef.current?.requestId !== item.requestId) {
          savedRef.current = { requestId: item.requestId, revision: item.revision };
          setSelectedId(item.requestId);
          onSelectionRef.current?.({
            requestId: item.requestId,
            revision: item.revision,
            message: item.message,
          });
          setIntent(item.message);
          void loadHistory(item.requestId, historyToken.current);
        }
      })
      .catch(() => {
        if (active) {
          setListFailed(true);
          setCards([]);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = intentRef.current.trim();
    const visible = intentRef.current;
    if (!message || savingRef.current) {
      return;
    }
    const known = savedRef.current;
    const open = known ? (cards ?? []).find((item) => item.requestId === known.requestId) : undefined;
    if (open && open.message.trim() === message) {
      setNotice("unchanged");
      return;
    }
    const duplicate = !known && (cards ?? []).some((item) => item.message.trim() === message);
    if (duplicate) {
      setNotice("unchanged");
      return;
    }
    savingRef.current = true;
    setSaving(true);
    setNotice(null);
    try {
      let reply = await updateDesignDraft({
        session_id: prototypeWorkspace.session_id,
        message,
        request_id: known?.requestId ?? null,
        revision: known?.revision ?? null,
      });
      let created = savedDraft(reply);
      let createdAsNew = !known;
      if (!created && known && reply.status !== "revision_conflict") {
        reply = await updateDesignDraft({
          session_id: prototypeWorkspace.session_id,
          message,
          request_id: null,
          revision: null,
        });
        created = savedDraft(reply);
        createdAsNew = true;
      }
      if (created) {
        setCards((current) => remember(current, { message, requestId: created.requestId, revision: created.revision }));
        if (createdAsNew) {
          savedRef.current = null;
          setSelectedId(null);
          setAnalysis(null);
          setPreviousAnalyses([]);
          setAnalysisError(null);
          setAnswers({});
          onSelectionRef.current?.(null);
        } else {
          savedRef.current = created;
          setSelectedId(created.requestId);
          onSelectionRef.current?.({ requestId: created.requestId, revision: created.revision, message });
        }
      }
      if (created && intentRef.current === visible) {
        setNotice("saved");
      } else if (!created) {
        if (reply.status !== "revision_conflict") {
          savedRef.current = null;
          setSelectedId(null);
          setAnalysis(null);
          setPreviousAnalyses([]);
          setAnalysisError(null);
          setAnswers({});
          onSelectionRef.current?.(null);
        }
        setNotice("error");
      }
    } catch {
      setNotice("error");
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  function choose(item: ListedDesignDraft) {
    if (savingRef.current) {
      return;
    }
    historyToken.current += 1;
    const token = historyToken.current;
    setAnalysis(null);
    setPreviousAnalyses([]);
    setOpenPreviousId(null);
    setAnalysisError(null);
    setAnswers({});
    setAnswerNotice(null);
    if (savedRef.current?.requestId === item.requestId) {
      savedRef.current = null;
      setSelectedId(null);
      setIntent("");
      setNotice(null);
      onSelectionRef.current?.(null);
      return;
    }
    savedRef.current = { requestId: item.requestId, revision: item.revision };
    setSelectedId(item.requestId);
    onSelectionRef.current?.({ requestId: item.requestId, revision: item.revision, message: item.message });
    setIntent(item.message);
    setNotice(null);
    void loadHistory(item.requestId, token);
  }

  async function loadHistory(requestId: string, token: number) {
    const loaded = await listSupervisorAnalyses(requestId);
    if (token !== historyToken.current || savedRef.current?.requestId !== requestId) {
      return;
    }
    if (loaded === null) {
      setAnalysisError("The saved analyses could not be loaded.");
      return;
    }
    const visible = loaded.filter((item) => item.requestId === requestId);
    setAnalysis(visible[0] ?? null);
    setPreviousAnalyses(visible.slice(1));
  }

  function sameSelection(requestId: string, revision: number) {
    const known = savedRef.current;
    return known?.requestId === requestId && known.revision === revision;
  }

  async function analyze() {
    const known = savedRef.current;
    const open = known ? (cards ?? []).find((item) => item.requestId === known.requestId) : undefined;
    if (!known || !open || open.revision !== known.revision || analyzingRef.current || answeringRef.current) {
      return;
    }
    const requestId = known.requestId;
    const revision = known.revision;
    const message = open.message;
    historyToken.current += 1;
    const token = historyToken.current;
    analyzingRef.current = true;
    setAnalyzing(true);
    setAnalysisError(null);
    setAnswerNotice(null);
    try {
      const listed = await listDesignDrafts();
      if (!sameSelection(requestId, revision)) {
        return;
      }
      const server = listed?.find((item) => item.requestId === requestId);
      if (listed === null || !server || server.revision !== revision || server.message !== message) {
        setAnalysisError(
          listed === null
            ? "The analysis was not run. The saved revision could not be read."
            : "The analysis was not run. The selected revision is not the current revision.",
        );
        return;
      }
      const result = await analyzeDesignRequest({
        session_id: prototypeWorkspace.session_id,
        request_id: requestId,
        revision,
        message,
      });
      if (!sameSelection(requestId, revision)) {
        return;
      }
      const again = await listDesignDrafts();
      if (!sameSelection(requestId, revision)) {
        return;
      }
      if (
        !result ||
        result.requestId !== requestId ||
        result.revision !== revision ||
        result.status === "supervisor_unavailable" ||
        result.status === "draft_not_found" ||
        result.status === "revision_conflict"
      ) {
        setAnalysisError(
          result?.summary || "The Supervisor could not analyze this request. No scientific action was taken.",
        );
        return;
      }
      const latest = again?.find((item) => item.requestId === requestId);
      const moved = again === null || !latest || latest.revision !== revision || latest.message !== message;
      const shown = moved ? { ...result, current: false } : result;
      const prior = analysisRef.current;
      const older =
        prior && prior.requestId === requestId ? [prior, ...previousRef.current] : previousRef.current;
      setAnalysis(shown);
      setPreviousAnalyses(
        older.filter(
          (item) =>
            item.requestId === requestId &&
            !(shown.analysisId !== "" && item.analysisId === shown.analysisId),
        ),
      );
      setAnswers({});
      const stored = await listSupervisorAnalyses(requestId);
      if (token !== historyToken.current || savedRef.current?.requestId !== requestId) {
        return;
      }
      if (stored && stored.length > 0) {
        setAnalysis(stored[0]);
        setPreviousAnalyses(stored.slice(1));
      }
    } finally {
      analyzingRef.current = false;
      setAnalyzing(false);
    }
  }

  async function sendAnswers() {
    const known = savedRef.current;
    const open = known ? (cards ?? []).find((item) => item.requestId === known.requestId) : undefined;
    if (!known || !open || !analysis || analysisOutdated(analysis, known, open.message, intentRef.current)) {
      return;
    }
    if (answeringRef.current || analyzingRef.current) {
      return;
    }
    const configured = new Set(analysis.configuredChoices.map((choice) => choice.field));
    const clarifications: { field: string; value: string }[] = [];
    const seen = new Set<string>();
    for (const item of [...analysis.extractedQuestions, ...analysis.supervisorQuestions]) {
      if (!("field" in item) || !item.field || configured.has(item.field) || seen.has(item.field)) {
        continue;
      }
      const value = (answers[item.field] ?? "").trim();
      if (!value) {
        continue;
      }
      seen.add(item.field);
      clarifications.push({ field: item.field, value });
    }
    if (clarifications.length === 0) {
      return;
    }
    const message = open.message;
    const requestId = known.requestId;
    const revision = known.revision;
    answeringRef.current = true;
    setAnswering(true);
    setAnswerNotice(null);
    try {
      const reply = await updateDesignDraft({
        session_id: prototypeWorkspace.session_id,
        message,
        request_id: requestId,
        revision,
        clarifications,
      });
      const created = savedDraft(reply);
      if (!created || created.requestId !== requestId) {
        setAnswerNotice("error");
        return;
      }
      setCards((current) => remember(current, { message, requestId: created.requestId, revision: created.revision }));
      if (savedRef.current?.requestId !== requestId || savedRef.current.revision !== revision) {
        return;
      }
      savedRef.current = created;
      setSelectedId(created.requestId);
      setIntent(message);
      onSelectionRef.current?.({ requestId: created.requestId, revision: created.revision, message });
      setAnswers({});
      setNotice("saved");
    } catch {
      setAnswerNotice("error");
    } finally {
      answeringRef.current = false;
      setAnswering(false);
    }
  }

  return (
    <form className="design-request" aria-label="Design request" onSubmit={(event) => void save(event)}>
      <h2>Design request</h2>
      <p>What would you like to design?</p>
      <label htmlFor="design-intent">Design intent</label>
      <textarea
        id="design-intent"
        value={intent}
        rows={6}
        placeholder="Describe your protein design objective…"
        aria-describedby={describedBy}
        onChange={(event) => {
          setIntent(event.target.value);
          setNotice(null);
        }}
      />
      <button type="submit" disabled={!hasIntent || saving}>
        {saving ? "Saving…" : "Save request"}
      </button>
      {listFailed ? <p id="design-request-list-error">The saved requests could not be loaded.</p> : null}
      {cards && cards.length > 0 ? (
        <ul className="design-request-cards" aria-label="Saved requests">
          {cards.map((item) => (
            <li key={item.requestId}>
              <button
                type="button"
                aria-pressed={selectedId === item.requestId}
                title={item.message}
                onClick={() => choose(item)}
              >
                {item.message.trim()}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {notice === "saved" ? <p id="design-request-saved">Request saved.</p> : null}
      {notice === "unchanged" ? <p id="design-request-unchanged">This request is already saved.</p> : null}
      {notice === "error" ? <p id="design-request-error">The request was not saved.</p> : null}
      {selectedId ? (
        <button type="button" className="analyze-request" disabled={analyzing || answering} onClick={() => void analyze()}>
          <SupervisorMark />
          {analyzing ? "Analyzing…" : "Analyze Request"}
        </button>
      ) : null}
      {analysisError ? <p id="design-request-analysis-error">{analysisError}</p> : null}
      {analysisError && !analysis ? (
        <div className="execution-usage">
          <UsageFields call={null} />
        </div>
      ) : null}
      {analysis ? (
        <SupervisorReview
          analysis={analysis}
          outdated={analysisOutdated(
            analysis,
            savedRef.current,
            (cards ?? []).find((item) => item.requestId === analysis.requestId)?.message ?? "",
            intent,
          )}
          answers={answers}
          answering={answering}
          suspended={analyzing}
          answerNotice={answerNotice}
          onAnswer={(key, value) => setAnswers((current) => ({ ...current, [key]: value }))}
          onSend={() => void sendAnswers()}
        />
      ) : null}
      {previousAnalyses.length > 0 ? (
        <section className="previous-analyses">
          <h4 id="previous-analyses-heading">Previous analyses</h4>
          <table aria-labelledby="previous-analyses-heading">
            <thead>
              <tr>
                <th scope="col">Date and time</th>
              </tr>
            </thead>
            <tbody>
              {previousAnalyses.map((item, index) => {
                const key = previousKey(item, index);
                const open = openPreviousId === key;
                return (
                  <tr key={key}>
                    <td>
                      <button type="button" aria-expanded={open} onClick={() => setOpenPreviousId(open ? null : key)}>
                        {executedAt(item.analyzedAt)}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {previousAnalyses.map((item, index) => {
            const key = previousKey(item, index);
            if (openPreviousId !== key) {
              return null;
            }
            return (
              <article key={key}>
                <p>Analysis of revision {item.revision}.</p>
                <p className="analysis-stamp">{executedAt(item.analyzedAt)}</p>
                <div className="execution-usage">
                  <UsageFields call={item.modelCall} label="Saved model usage" />
                </div>
                <p>{item.summary}</p>
                {item.configuredChoices.map((choice) => (
                  <p key={choice.field}>{choice.text}</p>
                ))}
                {item.generatorLimitation ? <p>{item.generatorLimitation}</p> : null}
                {item.extractedQuestions.map((question) => (
                  <p key={question.field}>{question.question}</p>
                ))}
                {item.supervisorQuestions.map((question) => (
                  <p key={question.question}>{question.question}</p>
                ))}
              </article>
            );
          })}
        </section>
      ) : null}
    </form>
  );
}
