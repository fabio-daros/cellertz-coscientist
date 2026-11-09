import { useEffect, useRef, useState } from "react";
import {
  readPlanSuggestions,
  requestPlanSuggestion,
  type PlanSuggestion,
  type SuggestionChoice,
} from "./design-plan";
import type { DesignPlanRecord } from "./design-plan";
import { SupervisorMark } from "./DesignRequest";

export function usePlanSuggestion(plan: DesignPlanRecord, enabled: boolean) {
  const [history, setHistory] = useState<PlanSuggestion[]>([]);
  const [suggesting, setSuggesting] = useState(false);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const binding = `${plan.requestId}:${plan.planId}:${plan.planRevision}:${plan.digest}`;
  const bindingRef = useRef(binding);
  bindingRef.current = binding;

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    setHistory([]);
    setPicked({});
    void readPlanSuggestions(plan.requestId, plan.planId).then((rows) => {
      if (!cancelled) {
        setHistory(rows.filter((item) => item.planId === plan.planId));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, plan.requestId, plan.planId, plan.planRevision, plan.digest]);

  const current = history.find((item) => item.digest === plan.digest && item.planRevision === plan.planRevision) ?? null;
  const stale = history.filter((item) => item.digest !== plan.digest || item.planRevision !== plan.planRevision);

  async function suggest() {
    const requested = binding;
    setSuggesting(true);
    try {
      const created = await requestPlanSuggestion(
        plan.requestId,
        plan.requestRevision,
        plan.planId,
        plan.planRevision,
        plan.digest,
      );
      if (bindingRef.current !== requested || !created) {
        return;
      }
      if (created.requestId !== plan.requestId || created.planId !== plan.planId || created.digest !== plan.digest) {
        return;
      }
      setHistory((items) => [created, ...items.filter((item) => item.suggestionId !== created.suggestionId)]);
    } finally {
      setSuggesting(false);
    }
  }

  return { suggesting, suggest, current, stale, picked, setPicked };
}

export function EditPlanWithAgentButton({
  suggesting,
  onSuggest,
}: {
  suggesting: boolean;
  onSuggest: () => void;
}) {
  return (
    <button type="button" className="suggest-configuration" onClick={onSuggest} disabled={suggesting}>
      <SupervisorMark />
      {suggesting ? "Suggesting…" : "Edit plan with agent"}
    </button>
  );
}

export function DesignSuggestion({
  suggestion,
  onApply,
}: {
  suggestion: ReturnType<typeof usePlanSuggestion>;
  onApply: (choices: SuggestionChoice[], suggestionId: string) => void;
}) {
  const { current, stale, picked, setPicked } = suggestion;
  if (!current && stale.length === 0) {
    return null;
  }
  return (
    <section aria-label="Configuration suggestion">
      {current ? <SuggestionCard suggestion={current} picked={picked} onPicked={setPicked} onApply={onApply} /> : null}
      {stale.length > 0 ? (
        <details>
          <summary>Earlier suggestions</summary>
          {stale.map((item) => (
            <p key={item.suggestionId}>This suggestion is out of date.</p>
          ))}
        </details>
      ) : null}
    </section>
  );
}

function SuggestionCard({
  suggestion,
  picked,
  onPicked,
  onApply,
}: {
  suggestion: PlanSuggestion;
  picked: Record<string, boolean>;
  onPicked: (value: Record<string, boolean>) => void;
  onApply: (choices: SuggestionChoice[], suggestionId: string) => void;
}) {
  const choices = suggestion.suggestions.filter((item) => item.applicable);
  const interrupted = suggestion.status === "token_budget_exceeded";
  const headline = headlineFor(suggestion, choices.length, interrupted);
  const direct = suggestion.literature.filter((record) => record.relevance === "direct");
  const context = suggestion.literature.filter((record) => record.relevance === "context");
  const system = detailSystem(suggestion, interrupted);
  return (
    <article aria-label={`Suggestion ${suggestion.suggestionId}`}>
      {headline ? <p>{headline}</p> : null}
      {interrupted ? <p>Manual plan values were left unchanged.</p> : null}
      {choices.map((item) => (
        <label key={item.field}>
          <input
            type="checkbox"
            checked={picked[item.field] === true}
            onChange={(event) => onPicked({ ...picked, [item.field]: event.target.checked })}
          />
          {item.label}: {shown(item.current)} → {shown(item.suggested)}
          {item.kind === "server_configuration" ? " Server configuration." : ""}
          {item.kind === "agent_recommendation" ? " Agent recommendation." : ""}
          {item.kind === "alternative" ? " Alternative." : ""}
          {item.justification ? ` ${item.justification}` : ""}
          {item.note ? ` ${item.note}` : ""}
        </label>
      ))}
      {suggestion.applicable && choices.length > 0 ? (
        <button
          type="button"
          onClick={() => onApply(choices.filter((item) => picked[item.field]), suggestion.suggestionId)}
        >
          Apply selected suggestions
        </button>
      ) : null}
      <details>
        <summary>Suggestion details</summary>
        <p>Planning origin stays the deterministic plan. This consultation was recorded later and did not create the plan.</p>
        {suggestion.recordComplete ? null : <p>Stored suggestion is incomplete. Text that was not stored is not shown.</p>}
        {suggestion.specialistState === "text" ? storedSpecialist(suggestion).map((line) => <p key={line}>Specialist response: {line}</p>) : null}
        {suggestion.region && !interrupted ? <p>{suggestion.region} This is not a residue recommendation.</p> : null}
        {interrupted && suggestion.literature.length > 0 ? (
          <p>These abstracts are not a verified residue map for the selected PDB.</p>
        ) : null}
        {system.map((line) => <p key={line}>System: {line}</p>)}
        {suggestion.limitations.map((line) => <p key={line}>System: {line}</p>)}
        <p>Agent: {suggestion.agent || "Not recorded"}</p>
        <p>Model: {suggestion.model || "Not recorded"}</p>
        <p>Tokens: {suggestion.tokens}</p>
        {suggestion.estimate ? <p>{suggestion.estimate}</p> : null}
        {suggestion.literatureFromCache ? (
          <p>Literature records were served from cache. Original retrieval: {suggestion.literatureRetrievedAt || "time not recorded"}.</p>
        ) : null}
        <LiteratureGroup label="Direct protein-interface evidence" records={direct} />
        <LiteratureGroup label="General context" records={context} />
        <p>Structure: {suggestion.structureSha256 || "Not recorded"}</p>
      </details>
    </article>
  );
}

const UNCHANGED = "Literature retrieved. No hotspot recommendation could be mapped to this structure. Plan unchanged.";

function headlineFor(suggestion: PlanSuggestion, choiceCount: number, interrupted: boolean): string | null {
  if (interrupted) {
    return "Configuration suggestion incomplete — token budget reached";
  }
  const hotspot = suggestion.residueMapping === "mapped"
    || suggestion.suggestions.some((item) => item.applicable && item.field === "hotspot_assumption");
  if (suggestion.literature.length > 0 && !hotspot && choiceCount === 0 && !suggestion.applicable) {
    return UNCHANGED;
  }
  return null;
}

function storedSpecialist(suggestion: PlanSuggestion): string[] {
  return [
    suggestion.specialistSummary,
    ...suggestion.specialistLines.filter((line) => line !== suggestion.specialistSummary),
  ].filter((line) => line.trim() !== "");
}

function detailSystem(suggestion: PlanSuggestion, interrupted: boolean): string[] {
  const hidden = new Set(suggestion.limitations.map((line) => line.trim()));
  if (interrupted) {
    hidden.add("Insufficient target-specific evidence to recommend hotspots.");
    hidden.add("The shared token budget was exhausted.");
  }
  return suggestion.systemMessages.filter((line) => !hidden.has(line.trim()));
}

function LiteratureGroup({ label, records }: { label: string; records: PlanSuggestion["literature"] }) {
  if (records.length === 0) {
    return null;
  }
  return (
    <>
      <p>{label}</p>
      {records.map((record) => (
        <p key={record.id}>
          {record.publicationKind === "preprint" ? "Preprint. " : ""}
          {record.id}: {record.title}. {record.textLevel === "abstract" ? "Abstract" : "Excerpt unavailable"}.
          {record.fullTextRetrieved ? "" : " Full text was not retrieved."} Consulted {record.retrievedAt || "at an unrecorded time"}.{" "}
          <a href={record.url}>{record.url}</a>
          {" "}Retrieved excerpt: {record.excerpt || "None retrieved."}
        </p>
      ))}
    </>
  );
}

function shown(value: string | number | null): string {
  return value === null || value === "" ? "Not recorded" : String(value);
}
