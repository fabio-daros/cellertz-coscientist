import previewFixture from "./campaign-fixtures.json";

/**
 * Display projection of the Phase 6C.1 operational references.
 * Python contracts remain the authority. This module does not validate,
 * approve, execute, or fill a missing scientific value.
 *
 * Official wordmark. The file is white; the header paints it black.
 * apps/protein-design-workspace/public/cellertz-logo.png
 */
export const CELLERTZ_LOGO_PATH = "/cellertz-logo.png";

export type ChainMapping = {
  source_chain_id: string;
  prepared_chain_id: string;
};

export type PreparationStep = {
  order: number;
  operation: string;
  parameters: Record<string, string>;
};

export type CampaignPreview = {
  contract_version: "vhh-campaign-6c1";
  campaign_id: string;
  target_id: string;
  session_id: string;
  modality: string;
  source_identifier: string;
  source_type: string;
  source_target_chain_ids: string[];
  source_partner_chain_ids: string[];
  prepared_target_chain_ids: string[];
  chain_mapping: ChainMapping[];
  preparation_status: string;
  preparation_note: string;
  original_artifact: string;
  prepared_artifact: string;
  prepared_content_digest: string | null;
  preparation: PreparationStep[];
  epitope: {
    derivation_method: string;
    source_structure_identifier: string;
    contact_cutoff_angstrom: number;
    verification_status: string;
    partner_chains: { namespace: string; chain_id: string }[];
    residues: { chain_namespace: string; chain_id: string; residue_number: number }[];
    biological_rationale: string;
  };
  scaffold: {
    design_modality: string;
    scaffold_id: string;
    scaffold_version: string | null;
    masked_sequence: string;
    mutable_regions: { name: string; start: number; end: number }[];
    cdr_regions: { name: string; start: number; end: number }[];
    provenance_kind: string;
  };
  generator_name: string;
  protocol_id: string;
  protocol_version: string | null;
  generator_version: string | null;
  generator_version_status: string;
  metrics: { metric_name: string; direction: string; threshold: number; role: string }[];
  accepted_design_target: number;
  max_trajectories: number;
  evidence: { stage: string; recorded: boolean }[];
  completeness: string;
  issues: string[];
  reproducible_readiness: string;
  execution_authorized: false;
  executable: false;
  provenance_kind: string;
  provenance_note: string;
  candidate_count: null;
};

export type CampaignPreviewFile = {
  contract_version: "vhh-campaign-6c1";
  provenance_kind: "operational_reference";
  campaigns: CampaignPreview[];
};

export const campaignPreview = previewFixture as CampaignPreviewFile;

export const EVIDENCE_STAGE_LABELS: Record<string, string> = {
  generated: "Generated",
  generator_accepted: "Generator accepted",
  independently_evaluated: "Independently evaluated",
  developability_screened: "Developability screened",
  off_target_screened: "Off-target screened",
  scientifically_reviewed: "Scientifically reviewed",
};

const METRIC_LABELS: Record<string, string> = {
  i_ptm: "iPTM",
  plddt: "pLDDT",
};

const ROLE_LABELS: Record<string, string> = {
  generator_filter: "Generator filter",
};

export function metricLabel(name: string): string {
  return METRIC_LABELS[name] ?? name;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export function formatThreshold(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function formatCount(value: number): string {
  return value.toLocaleString("en-US");
}

export function recordedLabel(value: string | null): string {
  return value === null ? "Not recorded" : value;
}
