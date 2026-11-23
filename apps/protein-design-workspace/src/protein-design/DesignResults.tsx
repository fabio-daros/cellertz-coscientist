import { useEffect, useState } from "react";
import type { SelectedDesignRequest } from "./DesignRequest";
import { StructureTrace, type TracedModel } from "../StructureTrace";
import { DesignRounds } from "./DesignRounds";

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
type HotspotAuditBlock = {
  id: string;
  declared: number;
  contact_n: number;
  contact_pct: number;
  contacted_residues: number[];
};
type HotspotAudit = {
  status: string;
  cutoff_angstrom?: number;
  declared_residues?: string;
  measures_overlap_not_steering?: boolean;
  blocks: HotspotAuditBlock[];
};
type ScreenedModel = {
  length: number;
  net_charge: number;
  net_charge_limit: number;
  cysteines: { unpaired: number[]; without_sg_atom: number[] };
  sequons: string[];
  occlusion: {
    status: string;
    footprint?: number;
    ligand_residues?: number;
    limit: number | null;
    is_a_gate?: boolean;
  };
  rejected_by: string | null;
  verdict: string;
  unscreened: string[];
};
type NativeLigand = {
  status: string;
  ligand?: string;
  source?: string;
  offset_from_declared_frame?: number;
};
type DevelopabilityScreen = {
  status: string;
  thresholds_from?: string;
  native_ligand?: NativeLigand;
  models: Record<string, ScreenedModel>;
};
type AttemptResult = {
  hotspot_audit?: HotspotAudit;
  developability_screen?: DevelopabilityScreen;
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
      <DesignRounds requestId={selection.requestId} />
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

// Why there is no audit matters as much as a number: a plan that declared no
// block was never asked for one, and must not read as a run that missed it.
const AUDIT_ABSENT: Record<string, string> = {
  not_available: "Declared hotspot blocks were not read, so no audit was produced.",
  not_adopted: "The hotspot set on this plan was written by hand, not adopted from an agent, so it has no blocks.",
  residues_changed: "The hotspot residues were edited after the suggestion was adopted, so its blocks no longer describe this plan.",
  suggestion_absent: "The adopted suggestion is no longer stored, so its blocks cannot be read.",
  origin_incomplete: "The plan records an adopted hotspot without naming the suggestion it came from.",
  suggestion_has_no_hotspots: "The adopted suggestion carries no hotspot recommendation.",
  no_blocks_declared: "This plan declared no hotspot block.",
  attempt_not_in_catalog: "This attempt is not in the verified catalogue, so contact was not calculated.",
  hash_mismatch: "A served PDB does not match the verified hash, so contact was not calculated.",
};

function HotspotAuditPanel({ audit }: { audit?: HotspotAudit }) {
  if (!audit) {
    return null;
  }
  if (audit.status !== "calculated") {
    return (
      <section aria-label="Hotspot block audit">
        <h4>Hotspot block audit</h4>
        <p>{AUDIT_ABSENT[audit.status] ?? `No audit was produced (${audit.status}).`}</p>
      </section>
    );
  }
  return (
    <section aria-label="Hotspot block audit">
      <h4>Hotspot block audit</h4>
      <p>
        Measured from heavy-atom distance at {audit.cutoff_angstrom} Å in the verified PDB, against the blocks this
        plan declared{audit.declared_residues ? ` (${audit.declared_residues})` : ""}.
      </p>
      {audit.measures_overlap_not_steering ? (
        <p>
          These blocks were declared on the plan, not given to the generator that produced these structures. This is
          how much they overlap, not whether the generator obeyed them.
        </p>
      ) : null}
      <ul>
        {audit.blocks.map((block) => (
          <li key={block.id}>
            <b>{block.id}</b>: {block.contact_n} of {block.declared} declared residues reached ({block.contact_pct}%)
            {block.contacted_residues.length > 0 ? ` — ${block.contacted_residues.join(", ")}` : ""}.
          </li>
        ))}
      </ul>
    </section>
  );
}

// The filter that rejected a design, in the words of the screen that ran it.
const REJECTED_BY: Record<string, string> = {
  unpaired_cysteine: "a free cysteine thiol",
  glycosylation_sequon: "an N-X-S/T glycosylation sequon",
  occlusion_footprint: "occluding too much of the natural ligand's footprint",
  net_charge: "net charge above the limit",
};
const SCREEN_ABSENT: Record<string, string> = {
  not_available: "This result names no attempt, so no structure was screened.",
  attempt_not_in_catalog: "This attempt is not in the verified catalogue, so it was not screened.",
  hash_mismatch: "A served PDB does not match the verified hash, so the screen was not run on it.",
};

function occlusionText(screened: ScreenedModel, ligand?: NativeLigand): string {
  const occlusion = screened.occlusion;
  if (occlusion.status !== "measured") {
    return "no ligand footprint is declared for this target, so this was not measured.";
  }
  const name = ligand?.ligand ? `${ligand.ligand}'s` : "the natural ligand's";
  const measured = `${occlusion.footprint} of ${occlusion.ligand_residues} residues of ${name} site are covered`;
  // Whether covering the site is the goal or the fault belongs to the
  // campaign, so without a declared limit this is a number, not a verdict.
  return occlusion.is_a_gate
    ? `${measured} (limit ${occlusion.limit}).`
    : `${measured}. This campaign declared no limit, so it is measured, not filtered.`;
}

function DevelopabilityPanel({ screen, modelId }: { screen?: DevelopabilityScreen; modelId: string }) {
  if (!screen) {
    return null;
  }
  const screened = screen.models[modelId];
  if (!screened) {
    return (
      <section aria-label="Developability screen">
        <h3>Developability screen</h3>
        <p>{SCREEN_ABSENT[screen.status] ?? `No screen was produced (${screen.status}).`}</p>
      </section>
    );
  }
  return (
    <section aria-label="Developability screen">
      <h3>Developability screen</h3>
      <p>
        {screened.rejected_by
          ? `Rejected by ${REJECTED_BY[screened.rejected_by] ?? screened.rejected_by}.`
          : "Passed every filter that could be measured."}
      </p>
      <ul>
        <li>
          Free cysteine thiols: {screened.cysteines.unpaired.length === 0 ? "none" : screened.cysteines.unpaired.join(", ")}
          {screened.cysteines.without_sg_atom.length > 0
            ? ` (${screened.cysteines.without_sg_atom.join(", ")} carry no SG atom in the file, so pairing could not be shown)`
            : ""}
          .
        </li>
        <li>Glycosylation sequons: {screened.sequons.length === 0 ? "none" : screened.sequons.join(", ")}.</li>
        <li>
          Net charge: {screened.net_charge} (limit +{screened.net_charge_limit}, K+R minus D+E).
        </li>
        <li>Ligand occlusion: {occlusionText(screened, screen.native_ligand)}</li>
        {screen.native_ligand?.offset_from_declared_frame ? (
          <li>
            The footprint was declared on a file numbered {screen.native_ligand.offset_from_declared_frame} positions
            above this structure, and was shifted to match it. The offset was established from residue identity, not
            assumed.
          </li>
        ) : null}
      </ul>
      {screened.unscreened.length > 0 ? (
        <p>Not screened: {screened.unscreened.join(", ")}. A design that passed is not a design that passed everything.</p>
      ) : null}
      <p>
        Thresholds from the professor&apos;s own campaign, measured here from the verified structure, not from a review
        field. This is a pre-filter, not a score.
      </p>
    </section>
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
      <HotspotAuditPanel audit={result.hotspot_audit} />
      <DevelopabilityPanel screen={result.developability_screen} modelId={modelId} />
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
