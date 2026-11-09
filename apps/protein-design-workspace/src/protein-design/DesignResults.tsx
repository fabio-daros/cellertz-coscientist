import { useEffect, useState } from "react";
import type { SelectedDesignRequest } from "./DesignRequest";
import { StructureTrace, type TracedModel } from "../StructureTrace";

type Cell = { kind?: string; text?: string };
type Metric = { metric?: string; model1?: Cell; model2?: Cell };
type ModelRecord = {
  id: string;
  representative: boolean;
  sequence: string | null;
  sha256: string;
  hash_matches: boolean;
  pdb_url: string;
  ca_counts: Record<string, number>;
};
type Review = {
  candidate: string | null;
  filter_policy: string | null;
  scientific_evaluation: string | null;
  platform_ipsae_ranking: boolean;
  ranking_score_computed: boolean;
  representative: { model?: string } | null;
  chains: { target?: string; binder?: string };
  metrics: Metric[];
  omitted: string[];
  bindcraft_rank: string | null;
  stats_csv_sha256: string | null;
  pdb_verified: boolean;
  models: Record<string, ModelRecord>;
};
type AttemptResult = {
  attempt_id: string | null;
  status: string | null;
  diagnosis: string | null;
  launched: boolean;
  terminal: boolean;
  plan_id: string | null;
  revision: number | null;
  digest: string | null;
  recorded_at: string | null;
  review: Review | null;
  duration: string | null;
  executor_log: string | null;
};
type ResultsDocument = {
  request_id: string;
  request_revision: number | null;
  plan_id: string | null;
  plan_revision: number | null;
  results: AttemptResult[];
};

export function DesignResults({ selection }: { selection: SelectedDesignRequest | null }) {
  const [document, setDocument] = useState<ResultsDocument | null>(null);
  const [failed, setFailed] = useState(false);
  const requestId = selection?.requestId ?? null;

  useEffect(() => {
    if (!requestId) {
      setDocument(null);
      setFailed(false);
      return;
    }
    let cancelled = false;
    setDocument(null);
    setFailed(false);
    fetch(`/api/design-journeys/by-request/${encodeURIComponent(requestId)}/results`)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.json() as Promise<ResultsDocument>;
      })
      .then((body) => {
        if (!cancelled && body.request_id === requestId) {
          setDocument(body);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  if (!selection) {
    return <section aria-label="Results"><p>Select a saved request in Design request.</p></section>;
  }
  return (
    <section aria-label="Results">
      <h2>Results</h2>
      <p>{selection.message}</p>
      {failed ? <p>The results could not be loaded.</p> : null}
      {!failed && document && document.results.length === 0 ? <p>No results for this request.</p> : null}
      {document && document.results.length > 0 ? (
        <AttemptResults document={document} />
      ) : null}
    </section>
  );
}

function AttemptResults({ document }: { document: ResultsDocument }) {
  const [primary, ...earlier] = document.results;
  return (
    <>
      <AttemptCard result={primary} document={document} />
      {earlier.length > 0 ? (
        <section aria-label="Earlier attempts">
          <h3>Earlier attempts</h3>
          {earlier.map((result) => (
            <details key={result.attempt_id ?? result.recorded_at ?? result.status}>
              <summary>{result.attempt_id} · {result.status}</summary>
              <AttemptCard result={result} document={document} />
            </details>
          ))}
        </section>
      ) : null}
    </>
  );
}

function AttemptCard({ result, document }: { result: AttemptResult; document: ResultsDocument }) {
  const review = result.review;
  const representative = review ? representativeModel(review) : null;
  const [modelId, setModelId] = useState(representative?.id ?? "model1");
  const model = review?.models[modelId] ?? representative;
  const sequence = model?.sequence ?? "";
  const completed = result.status === "verified" && result.launched;
  return (
    <article aria-label={result.attempt_id ?? "Attempt"}>
      <p>Status: {completed ? "Generation completed" : result.status}.</p>
      {review?.scientific_evaluation === "pending" ? <p>Scientific evaluation pending.</p> : null}
      {review?.candidate ? <p>Candidate {review.candidate}.</p> : null}
      {sequence ? <p>Length: {sequence.length} amino acids.</p> : null}
      {sequence ? (
        <details>
          <summary>Sequence</summary>
          <p>{sequence}</p>
          <button type="button" onClick={() => void navigator.clipboard.writeText(sequence)}>
            Copy sequence
          </button>
        </details>
      ) : null}
      {review && model ? (
        <>
          <p>Two structure predictions of the same designed sequence.</p>
          <p>
            {orderedModels(review).map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.id === model.id}
                onClick={() => setModelId(item.id)}
              >
                {predictionLabel(item)}
              </button>
            ))}
          </p>
          <StructureTrace model={tracedModel(model, review)} contactsRecorded={false} />
        </>
      ) : null}
      {review ? <MetricList review={review} /> : null}
      <details>
        <summary>Details</summary>
        <dl>
          <div><dt>Attempt</dt><dd>{result.attempt_id}</dd></div>
          <div><dt>Request</dt><dd>{document.request_id}</dd></div>
          <div><dt>Request revision</dt><dd>{document.request_revision}</dd></div>
          <div><dt>Plan revision</dt><dd>{result.revision}</dd></div>
          <div><dt>Digest</dt><dd>{result.digest}</dd></div>
          {review ? (
            <div>
              <dt>Hashes</dt>
              <dd>
                {Object.values(review.models).map((item) => (
                  <span key={item.id}>{item.id} {item.sha256} {item.hash_matches ? "matches" : "does not match"}. </span>
                ))}
                {review.stats_csv_sha256 ? <span>Stats {review.stats_csv_sha256}.</span> : null}
              </dd>
            </div>
          ) : null}
          {result.duration ? <div><dt>Duration</dt><dd>{result.duration}</dd></div> : null}
          {result.executor_log ? <div><dt>Logs</dt><dd><pre>{result.executor_log}</pre></dd></div> : null}
          {result.diagnosis && !completed ? <div><dt>Diagnosis</dt><dd>{result.diagnosis}</dd></div> : null}
        </dl>
      </details>
    </article>
  );
}

function MetricList({ review }: { review: Review }) {
  const iptm1 = recordedMetric(review, "i_pTM", "model1");
  const iptm2 = recordedMetric(review, "i_pTM", "model2");
  return (
    <dl>
      {iptm1 || iptm2 ? (
        <div>
          <dt>i_pTM</dt>
          <dd>
            {iptm1 ? <span>Structure prediction 1 {iptm1}. </span> : null}
            {iptm2 ? <span>Structure prediction 2 {iptm2}.</span> : null}
          </dd>
        </div>
      ) : null}
      <div><dt>ipSAE</dt><dd>{scoreText(review, "ipSAE", review.platform_ipsae_ranking)}</dd></div>
      <div><dt>Ranking score</dt><dd>{scoreText(review, "ranking_score", review.ranking_score_computed)}</dd></div>
      {review.bindcraft_rank ? <div><dt>BindCraft rank</dt><dd>{review.bindcraft_rank}</dd></div> : null}
      <div><dt>Filter</dt><dd>{filterText(review.filter_policy)}</dd></div>
    </dl>
  );
}

function scoreText(review: Review, name: string, calculated: boolean): string {
  if (!calculated || review.omitted.some((item) => item.toLowerCase() === name.toLowerCase())) {
    return "Not calculated";
  }
  return recordedMetric(review, name, "model1") ?? "Not calculated";
}

function recordedMetric(review: Review, name: string, model: "model1" | "model2"): string | null {
  const row = review.metrics.find((item) => item.metric === name);
  const cell = row?.[model];
  if (!cell || (cell.kind !== "recorded" && cell.kind !== "recorded_zero") || typeof cell.text !== "string") {
    return null;
  }
  return cell.text;
}

function filterText(value: string | null): string {
  if (value === "no_filters") {
    return "Technical demonstration — no scientific filtering.";
  }
  return value ?? "";
}

function orderedModels(review: Review): ModelRecord[] {
  const preferred = ["model1", "model2"].flatMap((id) => {
    const item = review.models[id];
    return item ? [item] : [];
  });
  const rest = Object.values(review.models).filter((item) => item.id !== "model1" && item.id !== "model2");
  return [...preferred, ...rest];
}

function predictionLabel(item: ModelRecord): string {
  const name = item.id === "model1" ? "Structure prediction 1" : item.id === "model2" ? "Structure prediction 2" : item.id;
  return item.representative ? `${name} — representative` : name;
}

function representativeModel(review: Review): ModelRecord | null {
  const named = review.representative?.model;
  if (named && review.models[named]) {
    return review.models[named];
  }
  return Object.values(review.models).find((item) => item.representative) ?? Object.values(review.models)[0] ?? null;
}

function tracedModel(model: ModelRecord, review: Review): TracedModel {
  const target = review.chains.target ?? "A";
  const binder = review.chains.binder ?? "B";
  return {
    id: model.id,
    pdb_url: model.pdb_url,
    target_chain: target,
    binder_chain: binder,
    target_residues: model.ca_counts[target] ?? 0,
    binder_residues: model.ca_counts[binder] ?? 0,
    contacts: {
      cutoff_angstrom: 5,
      binder_residues_in_contact: 0,
      target_residues_in_contact: 0,
      residue_pairs: 0,
      minimum_heavy_atom_distance_angstrom: 0,
      binder_heavy_atoms: 0,
      target_heavy_atoms: 0,
    },
  };
}
