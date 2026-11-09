import type { CampaignPreview } from "./campaign-preview";

/**
 * Presentation states for the workspace. They project recorded contract
 * fields. They do not validate, approve, or execute a campaign.
 */

export const PLATFORM_MODULES = [
  {
    id: "target-discovery",
    label: "Target Discovery",
    state: "Planned",
    summary:
      "Future workspace for prioritized targets and therapeutic requirements. This module is not implemented.",
  },
  {
    id: "protein-design",
    label: "Protein Design",
    state: "Active workspace",
    summary: "The active workspace. The executable demonstration is PDL1 from Design request. A VHH campaign shown here is not launched automatically.",
  },
  {
    id: "mrna-design",
    label: "mRNA Design",
    state: "Planned",
    summary:
      "Future handoff into Codonscope for CDS, UTR, translation, stability, and manufacturability design. No candidate is sent there from this prototype.",
  },
  {
    id: "experimental-feedback",
    label: "Experimental feedback",
    state: "Planned",
    summary:
      "Future return of laboratory results into the workspace. No experimental result is recorded here.",
  },
] as const;

export type PlatformModuleId = (typeof PLATFORM_MODULES)[number]["id"];

export const WORKFLOW_STAGES = [
  { id: "define", label: "Define campaign" },
  { id: "target", label: "Target & hotspots" },
  { id: "tools", label: "Tools & generation" },
  { id: "screen", label: "Screen & evaluate" },
  { id: "compare", label: "Compare & prioritize" },
  { id: "review", label: "Human review" },
  { id: "handoff", label: "mRNA handoff" },
] as const;

export type WorkflowStageId = (typeof WORKFLOW_STAGES)[number]["id"];

export type CheckState = "Ready" | "Missing" | "Reference only" | "Blocked";

export type ReadinessRow = {
  id: string;
  label: string;
  state: CheckState;
};

export function mainBlocker(campaign: CampaignPreview): string {
  if (campaign.generator_version === null) {
    return "mBER version not recorded";
  }
  if (!campaign.execution_authorized) {
    return "Execution not authorized";
  }
  return "None recorded";
}

export function stageState(stage: WorkflowStageId, campaign: CampaignPreview): string {
  if (stage === "define" || stage === "tools") {
    return campaign.completeness === "incomplete" ? "Incomplete" : "Reference loaded";
  }
  if (stage === "target") {
    return "Reference loaded";
  }
  if (stage === "screen" || stage === "compare") {
    return "Not recorded";
  }
  if (stage === "review") {
    return "Blocked";
  }
  return "Planned";
}

export function readinessRows(campaign: CampaignPreview): ReadinessRow[] {
  const reference = campaign.provenance_kind === "operational_reference";
  const recorded = (present: boolean): CheckState =>
    present ? (reference ? "Reference only" : "Ready") : "Missing";
  return [
    { id: "target", label: "Target identified", state: recorded(Boolean(campaign.target_id)) },
    {
      id: "structure",
      label: "Source structure identified",
      state: recorded(Boolean(campaign.source_identifier)),
    },
    {
      id: "chains",
      label: "Chain namespaces preserved",
      state: recorded(campaign.chain_mapping.length > 0),
    },
    {
      id: "epitope",
      label: "Epitope definition available",
      state: recorded(campaign.epitope.residues.length > 0),
    },
    {
      id: "scaffold",
      label: "Scaffold reference available",
      state: recorded(Boolean(campaign.scaffold.masked_sequence)),
    },
    {
      id: "protocol",
      label: "Generator protocol identified",
      state: recorded(Boolean(campaign.generator_name)),
    },
    {
      id: "version",
      label: "Generator version recorded",
      state: campaign.generator_version === null ? "Missing" : "Ready",
    },
    {
      id: "stopping",
      label: "Stopping policy defined",
      state: recorded(campaign.accepted_design_target > 0 && campaign.max_trajectories > 0),
    },
    {
      id: "authorization",
      label: "Execution authorized",
      state: campaign.execution_authorized ? "Ready" : "Blocked",
    },
  ];
}

export const TERM_NOTES: Record<string, string> = {
  "operational reference":
    "A configuration recorded from prior work. It is not experimental validation and not approval.",
  "reproducibility readiness":
    "Whether a concrete generator version was recorded. not_ready is not an approval state.",
  "generator accepted":
    "The generator's own filter accepted an output. That record is not scientific validation.",
  "independently evaluated":
    "A separate evaluation was recorded. That record is not biological validation.",
  "scientific review":
    "A human scientific review was recorded. That record is not an experiment.",
  "experimental validation":
    "A laboratory result. None is recorded for these campaign references.",
};
