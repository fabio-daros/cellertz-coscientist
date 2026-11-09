import { useEffect, useState } from "react";
import { asCell, cellLabel, servedPdbUrl, type RecordedCell } from "./manual-attempt-review";
import { StructureTrace, type TracedModel } from "./StructureTrace";

type Cell = { kind: string; text?: string };

type Contacts = {
  cutoff_angstrom?: number;
  binder_residues_in_contact: number;
  target_residues_in_contact: number;
  residue_pairs: number;
  minimum_heavy_atom_distance_angstrom: number | null;
  binder_heavy_atoms?: number;
  target_heavy_atoms?: number;
};

type ModelRecord = {
  id: string;
  representative: boolean;
  sequence: string;
  sha256: string;
  hash_matches: boolean;
  pdb_url: string;
  ca_counts: Record<string, number>;
  calculated_contacts: Contacts | null;
  recorded_contacts: Record<string, number | null> | null;
};

export type AttemptInspectionRecord = {
  status: string;
  error?: string;
  attempt_id: string;
  candidate: string;
  selected_model: string;
  origin: { kind: string; execution: string | null; detail: string };
  live_processing: boolean;
  specialist_activity: unknown[];
  specialist_activity_note: string;
  pdb_verified: boolean;
  execution_compliance_established: boolean;
  scientific_validation_established: boolean;
  scientific_evaluation: string;
  ranking_score: string;
  ranking_note: string;
  filter_policy: string;
  chains: { target: string; binder: string };
  sequence: string;
  representative: { model?: string; rule?: string };
  interface_residues: Cell;
  metrics: { metric: string; model1: Cell; model2: Cell; average: Cell }[];
  execution_constraints_reported: boolean;
  execution_constraints: {
    unmet?: boolean;
    requested_max_trajectories?: number;
    accepted_trajectory_files?: string[];
    low_confidence_trajectory_files?: string[];
    detail?: string;
  } | null;
  models: Record<string, ModelRecord>;
  evidence_references: { kind: string; attempt_id?: string; candidate?: string; model_id?: string; sha256?: string; href?: string }[];
  coordinator_traces?: CoordinatorTrace[];
  coordinator_trace_note?: string | null;
  experimental_validation_established?: boolean;
  execution_compliance_note?: string;
  target?: string | null;
  saved_specialist_processing?: SavedSpecialistRecord;
};

type TraceReference = {
  kind?: string;
  attempt_id?: string;
  model_id?: string;
  sha256?: string;
  href?: string;
  structure_href?: string;
  models?: Record<string, { sha256?: string; structure_href?: string; pdb_url?: string }>;
};

type TraceStep = {
  tool: string | null;
  specialist_role: string;
  execution: string;
  agent_executed: boolean;
  status: string;
  requested_id?: string | null;
  resolved_attempt_id?: string | null;
  summary?: string;
  evidence_references?: TraceReference[];
};

type CoordinatorTrace = {
  trace_id: string;
  source_query: string;
  execution_record: string | null;
  conversation_id: string;
  attempt_id: string;
  steps: TraceStep[];
};

type SavedSpecialistRecord = {
  status?: string;
  detail?: string;
  journey_id?: string | null;
  round_id?: string | null;
  plan_id?: string | null;
  plan_revision?: number | null;
  digest?: string | null;
  candidate_id?: string | null;
  ranking_score?: number | string;
  ranking_score_scope?: string;
  applies_to_each_model?: boolean;
  per_model_ranking_scores?:
    | string
    | { model_number?: number; ipsae?: number; ipsae_ab?: number; ipsae_ba?: number }[];
  score_source?: string;
  ranking_signal?: string;
  model_ids?: string[] | string;
  artifact_model_ids?: string[];
  aggregation?: Record<string, string | number>;
  rank?: number | string;
  rank_note?: string;
  critique_agreement?: string | null;
  critique_kind?: string;
  prioritization_bucket?: string;
  prioritization_reason?: string;
  prioritization_kind?: string;
  attempted_trajectory_budget?: {
    status?: string;
    compliance_established?: boolean | null;
    requested_attempted_trajectories?: number | null;
    termination_reason?: string | null;
    establishes_execution_compliance?: boolean;
    missing_is_violation?: boolean;
  };
  stages?: {
    stage_id?: string;
    status?: string;
    agent_executed?: boolean;
    implementation?: string;
    evidence_references?: { plan_id?: string; revision?: number; digest?: string; attempt_id?: string; candidate_id?: string }[];
  }[];
  superseded?: { status?: string; used_as_current_score?: boolean; ranking_score?: number | string; stage_ids?: string[] }[];
  scoring_audit?: {
    status?: string;
    records?: {
      status?: string;
      agrees?: boolean | null;
      agreement?: string;
      original_ranking_score?: number | string | null;
      recalculated_ranking_score?: number | string;
      model_aggregation?: string;
      protocol_id?: string;
      calculated_models?: { model_number?: number; ipsae?: number; ipsae_ab?: number; ipsae_ba?: number }[];
    }[];
  };
  original_ranking_score?: number | string;
  ipsae_provenance?: "not_recorded" | string | Record<string, unknown>;
};

function auditSummary(saved: SavedSpecialistRecord): string {
  const audit = saved.scoring_audit;
  const records = audit?.records ?? [];
  if (!audit || audit.status === "not_recorded" || records.length === 0) {
    return "Scoring audit: not recorded. The original persisted ranking_score is unchanged.";
  }
  const completed = [...records].reverse().find((item) => item.status === "completed");
  const record = completed ?? records[records.length - 1];
  if (!record || record.status !== "completed") {
    return `Scoring audit: ${record?.status ?? "not recorded"}. It does not replace the original ranking_score.`;
  }
  const agreement = record.agrees === true ? "agrees" : record.agrees === false ? "disagrees" : "not comparable";
  return `Scoring audit recalculated ranking_score ${String(record.recalculated_ranking_score)}. Agreement ${agreement}. The original persisted ranking_score stays ${String(saved.original_ranking_score ?? saved.ranking_score)}.`;
}

function perModelText(value: SavedSpecialistRecord["per_model_ranking_scores"]): string {
  if (!Array.isArray(value)) return `Per-model ranking scores: ${value ?? "not recorded"}.`;
  return value
    .map((item) => `model${item.model_number ?? "?"} ipSAE ${item.ipsae} ab ${item.ipsae_ab} ba ${item.ipsae_ba}`)
    .join("; ");
}

function auditDetail(saved: SavedSpecialistRecord): string {
  const records = saved.scoring_audit?.records ?? [];
  if (records.length === 0) return "No scoring audit record.";
  return records
    .map((record) => {
      const models = (record.calculated_models ?? [])
        .map((item) => `model${item.model_number} ${item.ipsae} ab ${item.ipsae_ab} ba ${item.ipsae_ba}`)
        .join("; ");
      return `Audit ${record.status}. Protocol ${record.protocol_id ?? "not recorded"}. Across-model ${record.model_aggregation ?? "not recorded"}. ${models}`;
    })
    .join(" ");
}

function aggregationSentence(aggregation: Record<string, string | number> | undefined): string {
  const model = aggregation?.model_aggregation ?? "not recorded";
  const within = aggregation?.within_model_aggregation ?? "not recorded";
  if (model === "not_recorded" && within === "not_recorded") {
    return "Model aggregation is not recorded. No aggregation method is invented for this score.";
  }
  return `Recorded model aggregation ${String(model)}. Recorded within-model aggregation ${String(within)}.`;
}

function provenanceSummary(saved: SavedSpecialistRecord): string {
  const recorded = saved.ipsae_provenance;
  if (!recorded || recorded === "not_recorded") {
    return "Original ipSAE provenance is not recorded. A later audit does not supply that missing provenance.";
  }
  if (typeof recorded === "object") {
    const protocol = recorded.protocol_id ?? "not recorded";
    const aggregation = recorded.model_aggregation ?? "not recorded";
    return `Original ipSAE provenance is recorded. Protocol ${String(protocol)}. Model aggregation ${String(aggregation)}.`;
  }
  return "Original ipSAE provenance is recorded.";
}

function budgetText(saved: SavedSpecialistRecord): string {
  const budget = saved.attempted_trajectory_budget;
  if (budget?.status !== "recorded") {
    return "Attempted-trajectory-budget compliance is not recorded. Missing evidence is not a proven violation.";
  }
  if (budget.compliance_established) {
    return "Attempted-trajectory-budget compliance is established. This does not establish overall execution compliance.";
  }
  return "Attempted-trajectory-budget compliance is recorded as not established. This is separate from PDB integrity and scientific validation.";
}

function SavedSpecialist({ record }: { record: AttemptInspectionRecord }) {
  const saved = record.saved_specialist_processing;
  if (!saved) {
    return <p>Persisted specialist processing was not included in this inspection.</p>;
  }
  const score = typeof saved.ranking_score === "number" ? String(saved.ranking_score) : "not recorded";
  const aggregation = saved.aggregation ?? {};
  const perModel = Array.isArray(saved.per_model_ranking_scores);
  const agreement = String(saved.critique_agreement ?? "not recorded");
  const bucket = saved.prioritization_bucket ?? "not recorded";
  return (
    <section aria-label="Persisted specialist processing">
      <h3>Computational evaluation</h3>
      <p>
        ranking_score is raw ipSAE. Original persisted ranking_score {score}. Scope{" "}
        {saved.ranking_score_scope ?? "candidate"}. This value is not an independent score for each model.
        BindCraft i_pTM is a different recorded metric.
      </p>
      <p>
        Signal {saved.ranking_signal ?? "not recorded"}. Source {saved.score_source ?? "not recorded"}.{" "}
        {aggregationSentence(aggregation)}
      </p>
      <p>{provenanceSummary(saved)}</p>
      <p>{auditSummary(saved)}</p>
      {perModel ? (
        <section aria-label="Per-model scores">
          <h4>Per-model scores</h4>
          <p>{perModelText(saved.per_model_ranking_scores)}</p>
        </section>
      ) : (
        <p>Per-model ranking scores: {typeof saved.per_model_ranking_scores === "string" ? saved.per_model_ranking_scores : "not recorded"}.</p>
      )}
      <h3>Specialists</h3>
      <p>{saved.detail}</p>
      <p>{saved.rank_note ?? "Rank is not recorded. This does not establish comparative superiority."}</p>
      <p>
        Critique {saved.critique_kind ?? "not recorded"}. Agreement {agreement}.
        {agreement === "unknown" ? " Unknown agreement stays unknown." : ""}
      </p>
      <p>
        Prioritization {saved.prioritization_kind ?? "not recorded"}. Bucket {bucket}.{" "}
        {saved.prioritization_reason ?? "Reason not recorded."}
        {bucket === "core"
          ? " The core bucket is a selection by the recorded policy. It is not experimental validation."
          : ""}
      </p>
      <details>
        <summary>Scoring technical details</summary>
        <p>
          Aggregation {String(aggregation.model_aggregation ?? "not recorded")}. Within-model aggregation{" "}
          {String(aggregation.within_model_aggregation ?? "not recorded")}. Protocol{" "}
          {String(aggregation.protocol_id ?? "not recorded")}. PAE cutoff{" "}
          {String(aggregation.pae_cutoff ?? "not recorded")}. Distance cutoff{" "}
          {String(aggregation.distance_cutoff ?? "not recorded")}. Chains{" "}
          {String(aggregation.chain_a ?? "not recorded")} and {String(aggregation.chain_b ?? "not recorded")}.
        </p>
        <p>{auditDetail(saved)}</p>
        {(saved.stages ?? []).map((stage) => (
          <p key={stage.stage_id}>
            {stage.stage_id}: {stage.status}. Agent executed {String(stage.agent_executed)}. {stage.implementation}.{" "}
            {(stage.evidence_references ?? [])
              .map((item) => `${item.attempt_id ?? ""} ${item.candidate_id ?? ""} revision ${item.revision ?? ""}`)
              .join(" ")}
          </p>
        ))}
        {(saved.superseded ?? []).map((item, index) => (
          <p key={`superseded-${index}`}>
            Superseded processing is historical. Used as the current ranking_score: {String(item.used_as_current_score)}.
            Superseded ranking_score {String(item.ranking_score ?? "not recorded")}.
          </p>
        ))}
      </details>
      <details>
        <summary>Budget and plan record</summary>
        <p>
          {budgetText(saved)} Requested attempted trajectories{" "}
          {saved.attempted_trajectory_budget?.requested_attempted_trajectories ?? "not recorded"}. Termination{" "}
          {saved.attempted_trajectory_budget?.termination_reason ?? "not recorded"}.
        </p>
        <p>
          Plan {saved.plan_id ?? "not recorded"} revision {saved.plan_revision ?? "not recorded"} digest{" "}
          {saved.digest ?? "not recorded"}. Journey {saved.journey_id ?? "not recorded"} {saved.round_id ?? ""}{" "}
          candidate {saved.candidate_id ?? "not recorded"}.
        </p>
      </details>
    </section>
  );
}

export function openRecordedResults(attempt: string, candidate: string, model = "model1"): void {
  const params = new URLSearchParams({ area: "results", attempt, candidate, model });
  window.history.pushState({}, "", `/?${params.toString()}`);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function resultsQuery(): { attempt: string; candidate: string; model: string } {
  const params = new URLSearchParams(window.location.search);
  return {
    attempt: params.get("attempt") ?? "",
    candidate: params.get("candidate") ?? "",
    model: params.get("model") ?? "",
  };
}

export function AttemptInspection() {
  const [query, setQuery] = useState(resultsQuery);
  const { attempt, candidate, model } = query;
  const [record, setRecord] = useState<AttemptInspectionRecord | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const sync = () => setQuery(resultsQuery());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  useEffect(() => {
    if (!attempt) {
      setRecord(null);
      setError("");
      return;
    }
    if (!candidate || !model) {
      setRecord(null);
      setError("This attempt has no selected candidate. No other attempt is opened instead.");
      return;
    }
    const params = new URLSearchParams({ candidate, model });
    const requestedAttempt = attempt;
    const requestedCandidate = candidate;
    let cancelled = false;
    setRecord(null);
    setError("");
    fetch(`/api/attempts/${encodeURIComponent(requestedAttempt)}/inspection?${params.toString()}`)
      .then(async (response) => {
        const body = (await response.json()) as AttemptInspectionRecord;
        if (!response.ok || body.status !== "ok") {
          throw new Error(body.error || "The attempt inspection was not found.");
        }
        return body;
      })
      .then((body) => {
        if (cancelled || body.attempt_id !== requestedAttempt || body.candidate !== requestedCandidate) {
          return;
        }
        setRecord(body);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setRecord(null);
          setError(reason instanceof Error ? reason.message : "The attempt inspection was not found.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, candidate, model]);

  function chooseModel(next: string) {
    const params = new URLSearchParams(window.location.search);
    params.set("model", next);
    window.history.replaceState({}, "", `/?${params.toString()}`);
    setQuery(resultsQuery());
  }

  return (
    <section className="manual-review" aria-label="Attempt results">
      <header>
        <h2>Results</h2>
        <p>Recorded attempt inspection. This page does not generate, score, or rank a new candidate.</p>
      </header>
      {!attempt ? (
        <p>No attempt is selected. Results stays empty until a round records one.</p>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {!record && !error && attempt ? <p role="status">Loading the recorded result.</p> : null}
      {record ? <InspectionBody record={record} model={model} onModel={chooseModel} /> : null}
    </section>
  );
}

function InspectionBody({
  record,
  model,
  onModel,
}: {
  record: AttemptInspectionRecord;
  model: string;
  onModel: (model: string) => void;
}) {
  const selected = record.models[model];
  const traced = selected ? tracedModel(record, selected) : null;
  const constraints = record.execution_constraints;
  const saved = record.saved_specialist_processing;
  const [copied, setCopied] = useState("");
  const score = candidateScore(record);
  const length = record.sequence ? String(record.sequence.length) : "not recorded";
  async function copySequence() {
    if (!record.sequence) {
      setCopied("The binder sequence was not copied.");
      return;
    }
    try {
      await navigator.clipboard.writeText(record.sequence);
      setCopied("Binder sequence copied.");
    } catch {
      setCopied("The binder sequence was not copied.");
    }
  }
  return (
    <>
      <section className="result-summary" aria-label="Result summary">
        <h3>Result summary</h3>
        <p role="status">{resultStatus(record)}</p>
        <dl>
          {record.target ? (
            <div>
              <dt>Target</dt>
              <dd>{record.target}</dd>
            </div>
          ) : null}
          <div>
            <dt>Round</dt>
            <dd>{saved?.round_id || "not recorded"}</dd>
          </div>
          <div>
            <dt>Attempt</dt>
            <dd>{record.attempt_id}</dd>
          </div>
          <div>
            <dt>Candidate</dt>
            <dd>{record.candidate}</dd>
          </div>
          <div>
            <dt>Binder length</dt>
            <dd>{length}</dd>
          </div>
          <div>
            <dt>Representative</dt>
            <dd>
              {record.representative.model ?? "not recorded"} by {record.representative.rule ?? "not recorded"}
            </dd>
          </div>
          <div>
            <dt>Generation</dt>
            <dd>{record.origin.detail}</dd>
          </div>
          <div>
            <dt>Processing</dt>
            <dd>{saved?.status ?? "not included"}</dd>
          </div>
          {score ? (
            <div>
              <dt>ipSAE</dt>
              <dd>{score}</dd>
            </div>
          ) : null}
        </dl>
        <p>{resultLimitation(record)}</p>
      </section>
      <section aria-label="Structure">
        <h3>Structure</h3>
        <p>Alpha-carbon trace. This is not a full atomic representation.</p>
        <p>
          Chain {record.chains.target || "not recorded"}, target. Chain {record.chains.binder || "not recorded"}, binder.
        </p>
        <div className="model-switch">
          {Object.values(record.models).map((item) => (
            <button key={item.id} type="button" onClick={() => onModel(item.id)} aria-pressed={item.id === model}>
              {item.id}
              {item.representative ? ", representative" : ""}
            </button>
          ))}
        </div>
        {traced ? (
          <StructureTrace
            model={traced}
            contactsRecorded={selected?.recorded_contacts != null && countsRecorded(selected.recorded_contacts)}
          />
        ) : (
          <p role="alert">The selected structure is not opened. A hash mismatch or a missing file is not replaced.</p>
        )}
        <p>
          Binder sequence: <code className="binder-sequence">{record.sequence || "not recorded"}</code>
        </p>
        <button type="button" onClick={() => void copySequence()} disabled={!record.sequence}>
          Copy binder sequence
        </button>
        {copied ? <p role="status">{copied}</p> : null}
        <ul>
          {Object.values(record.models).map((item) => (
            <li key={`${item.id}-pdb`}>
              <a href={item.pdb_url}>Download {item.id} PDB</a>
              {item.representative ? " Representative model." : ""}
            </li>
          ))}
        </ul>
      </section>
      <div className="manual-models">
        {Object.values(record.models).map((item) => (
          <article key={item.id} aria-label={`Model ${item.id}`}>
            <h3>{item.id}</h3>
            <p>{item.representative ? "Representative model." : "Not the representative model."}</p>
            <h4>Calculated contacts</h4>
            <p>Viewer calculation from the served PDB. Not the recorded review.</p>
            <ContactList contacts={item.calculated_contacts} calculated />
            <h4>Recorded contacts</h4>
            <p>Recorded on the review. Not recalculated here.</p>
            <ContactList contacts={item.recorded_contacts} calculated={false} />
          </article>
        ))}
      </div>
      <SavedSpecialist record={record} />
      <p>
        {record.ranking_note} Recorded review ranking_score is {record.ranking_score}.
      </p>
      <details>
        <summary>Generation metrics</summary>
        <p>Recorded metrics are the original generation review. A later raw ipSAE value is separate.</p>
        <p>InterfaceResidues: {labelCell(record.interface_residues)}.</p>
        <div className="manual-table-scroll">
          <table>
            <caption>Recorded metrics</caption>
            <thead>
              <tr>
                <th>Metric</th>
                <th>model1</th>
                <th>model2</th>
                <th>average</th>
              </tr>
            </thead>
            <tbody>
              {record.metrics.map((row) => (
                <tr key={row.metric}>
                  <th scope="row">{row.metric}</th>
                  <td>{labelCell(row.model1)}</td>
                  <td>{labelCell(row.model2)}</td>
                  <td>{labelCell(row.average)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <details>
        <summary>Provenance, hashes, and references</summary>
        <p>{record.origin.detail} Execution recorded as {record.origin.execution ?? "not recorded"}.</p>
        <p>
          Artifact verification: {record.pdb_verified ? "PDB hashes match the review." : "PDB hashes do not match."}{" "}
          PDB integrity is separate from execution compliance and from scientific validation.
        </p>
        <p>{record.specialist_activity_note}</p>
        <h3>Structure hashes</h3>
        <ul>
          {Object.values(record.models).map((item) => (
            <li key={`${item.id}-hash`}>
              {item.id} SHA-256 <code>{item.sha256}</code> {item.pdb_url}
            </li>
          ))}
        </ul>
        <section aria-label="Evidence references">
          <h3>Evidence references</h3>
          <ul>
            {record.evidence_references.map((item) => (
              <li key={`${item.kind}-${item.model_id ?? item.sha256 ?? item.attempt_id}`}>
                {item.kind}
                {item.attempt_id ? ` ${item.attempt_id}` : ""}
                {item.candidate ? ` ${item.candidate}` : ""}
                {item.model_id ? ` ${item.model_id}` : ""}
                {item.sha256 ? ` ${item.sha256}` : ""}
                {item.href ? ` ${item.href}` : ""}
              </li>
            ))}
          </ul>
        </section>
      </details>
      <details>
        <summary>Execution constraints and budget</summary>
        <p>
          Scientific evaluation is {record.scientific_evaluation}. Scientific validation established:{" "}
          {String(record.scientific_validation_established)}. Experimental validation established:{" "}
          {String(record.experimental_validation_established ?? record.scientific_validation_established)}.
        </p>
        <p>{record.execution_compliance_note ?? "Overall execution compliance is not established by this inspection."}</p>
        {record.execution_constraints_reported && constraints ? (
          <p>
            Execution constraint unmet is {String(constraints.unmet)}. requested_max_trajectories is{" "}
            {String(constraints.requested_max_trajectories)}. Accepted trajectory files:{" "}
            {(constraints.accepted_trajectory_files ?? []).join(", ") || "none"}. Low-confidence trajectory files:{" "}
            {(constraints.low_confidence_trajectory_files ?? []).join(", ") || "none"}. These review
            constraints are separate from attempted-trajectory-budget compliance.
          </p>
        ) : (
          <p>
            The review does not report execution constraints. That absence is not compliance and not a
            proven violation.
          </p>
        )}
      </details>
      <details>
        <summary>Coordinator trace</summary>
        <CoordinatorTraceSection record={record} />
      </details>
    </>
  );
}

function candidateScore(record: AttemptInspectionRecord): string | null {
  const saved = record.saved_specialist_processing?.ranking_score;
  if (typeof saved === "number") {
    return String(saved);
  }
  if (record.ranking_score && record.ranking_score !== "not_recorded") {
    return record.ranking_score;
  }
  return null;
}

function resultStatus(record: AttemptInspectionRecord): string {
  const saved = record.saved_specialist_processing;
  const generated = record.sequence
    ? "A binder candidate was generated."
    : "A generated binder sequence is not recorded.";
  const processing = saved?.status === "linked" || saved?.status === "recorded"
    ? "Specialist processing is recorded."
    : "Specialist processing is not recorded for this attempt.";
  const evaluated = typeof saved?.ranking_score === "number"
    ? "Computational evaluation recorded one raw ipSAE ranking score."
    : "A calculated ipSAE ranking score is not recorded.";
  return `${generated} ${processing} ${evaluated} Scientific validation is not established. Experimental validation is not established.`;
}

function resultLimitation(record: AttemptInspectionRecord): string {
  if (record.filter_policy === "no_filters") {
    return "This demonstration used no_filters. That policy does not establish scientific quality.";
  }
  return "Computational scores are not scientific or experimental validation.";
}

function CoordinatorTraceSection({ record }: { record: AttemptInspectionRecord }) {
  const supplied = Array.isArray(record.coordinator_traces) ? record.coordinator_traces : [];
  const traces = supplied.filter(
    (trace) => typeof trace.source_query === "string" && trace.source_query.trim().length > 0,
  );
  const note =
    record.coordinator_trace_note ??
    (traces.length === 0
      ? supplied.length > 0
        ? "No recorded coordinator trace. A saved record without a question is not a coordinator trace."
        : "No recorded coordinator trace"
      : supplied.length > traces.length
        ? "Records without a question are not coordinator traces."
        : null);
  return (
    <section aria-label="Recorded coordinator trace">
      <h3>Recorded coordinator trace</h3>
      <p>
        Saved questions and tool results for this attempt. This is not a new analysis. Deterministic
        tools are separate from language-model synthesis. This record is not specialist processing.
      </p>
      {note ? <p>{note}</p> : null}
      {traces.map((trace) => (
        <article key={trace.trace_id} aria-label={`Coordinator trace ${trace.trace_id}`}>
          <p>Source query: {trace.source_query}</p>
          <p>
            Execution record: {trace.execution_record ?? "none"}. Conversation {trace.conversation_id}.
          </p>
          <ol>
            {trace.steps.map((step, index) => (
              <li key={`${trace.trace_id}-${index}`}>
                {step.tool ?? "no tool"} · {executionLabel(step.execution)} · role {step.specialist_role} ·
                status {step.status}. Requested {step.requested_id ?? "none"}. Resolved{" "}
                {step.resolved_attempt_id ?? "none"}. Specialist agent executed: {String(step.agent_executed)}.
                {step.summary ? ` ${step.summary}` : ""}
                <TraceReferences references={step.evidence_references ?? []} />
              </li>
            ))}
          </ol>
        </article>
      ))}
    </section>
  );
}

function TraceReferences({ references }: { references: TraceReference[] }) {
  const links = references.flatMap((reference) => {
    const models = reference.models ?? {};
    return Object.entries(models)
      .filter((entry) => typeof entry[1]?.structure_href === "string")
      .map(([modelId, model]) => ({ modelId, href: model.structure_href as string, sha256: model.sha256 }));
  });
  if (links.length === 0) {
    return null;
  }
  return (
    <ul>
      {links.map((link) => (
        <li key={`${link.modelId}-${link.href}`}>
          {link.modelId} {link.sha256 ?? ""} <a href={link.href}>{link.href}</a>
        </li>
      ))}
    </ul>
  );
}

function executionLabel(value: string): string {
  if (value === "deterministic_tool") {
    return "Deterministic tool";
  }
  if (value === "language_model_synthesis") {
    return "Language-model synthesis";
  }
  return value;
}

function tracedModel(record: AttemptInspectionRecord, model: ModelRecord): TracedModel | null {
  const calculated = model.calculated_contacts;
  if (
    !record.pdb_verified ||
    !model.hash_matches ||
    !servedPdbUrl(model.pdb_url) ||
    !calculated ||
    typeof calculated.minimum_heavy_atom_distance_angstrom !== "number" ||
    typeof calculated.binder_heavy_atoms !== "number" ||
    typeof calculated.target_heavy_atoms !== "number"
  ) {
    return null;
  }
  const target = model.ca_counts[record.chains.target];
  const binder = model.ca_counts[record.chains.binder];
  if (!target || !binder) {
    return null;
  }
  return {
    id: model.id,
    pdb_url: model.pdb_url,
    target_chain: record.chains.target,
    binder_chain: record.chains.binder,
    target_residues: target,
    binder_residues: binder,
    contacts: {
      cutoff_angstrom: calculated.cutoff_angstrom ?? 5,
      binder_residues_in_contact: calculated.binder_residues_in_contact,
      target_residues_in_contact: calculated.target_residues_in_contact,
      residue_pairs: calculated.residue_pairs,
      minimum_heavy_atom_distance_angstrom: calculated.minimum_heavy_atom_distance_angstrom,
      binder_heavy_atoms: calculated.binder_heavy_atoms,
      target_heavy_atoms: calculated.target_heavy_atoms,
    },
  };
}

function countsRecorded(contacts: Record<string, number | null>): boolean {
  return ["residue_pairs", "binder_residues_in_contact", "target_residues_in_contact"].every(
    (key) => typeof contacts[key] === "number",
  );
}

function ContactList({
  contacts,
  calculated,
}: {
  contacts: Contacts | Record<string, number | null> | null;
  calculated: boolean;
}) {
  if (!contacts) {
    return <p>{calculated ? "Contacts were not calculated." : "Contacts were not recorded."}</p>;
  }
  return (
    <dl>
      {["residue_pairs", "binder_residues_in_contact", "target_residues_in_contact"].map((key) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{contactText(contacts[key as keyof typeof contacts], calculated)}</dd>
        </div>
      ))}
    </dl>
  );
}

function contactText(value: unknown, calculated: boolean): string {
  if (typeof value !== "number") {
    return calculated ? "not calculated" : "not recorded";
  }
  if (!calculated && value === 0) {
    return "0 (recorded zero)";
  }
  return String(value);
}

function labelCell(cell: Cell): string {
  try {
    return cellLabel(asCell(cell) as RecordedCell);
  } catch {
    return cell.kind;
  }
}
