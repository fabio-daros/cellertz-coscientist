import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { CampaignAbout, useCampaignAbout } from "./CampaignAbout";
import {
  createCampaign,
  listCampaigns,
  readCampaign,
  type CampaignRecord,
} from "./campaign-records";
import {
  campaignPreview,
  formatCount,
  formatThreshold,
  metricLabel,
  recordedLabel,
  roleLabel,
  type CampaignPreview,
} from "./campaign-preview";
import { PLATFORM_MODULES } from "./workflow";

const OPERATION_LABELS: Record<string, string> = {
  remove_solvent: "Remove solvent",
  extract_chain: "Extract chain",
  relabel_chain: "Relabel chain",
  set_segid: "Set segment id",
  remove_selected_heteroatoms: "Remove selected heteroatoms",
};

export function WorkflowWorkspace({
  runnerReadiness = null,
  campaignId,
  onCampaignChange,
  demonstrationId = null,
  onOpenDemonstration,
  runs = [],
}: {
  runnerReadiness?: ReactNode;
  campaignId: string;
  onCampaignChange: (campaignId: string) => void;
  demonstrationId?: string | null;
  onOpenDemonstration: () => void;
  runs?: { run_id: string; display_status: string }[];
}) {
  const campaign =
    campaignPreview.campaigns.find((item) => item.campaign_id === campaignId) ??
    campaignPreview.campaigns[0];
  const planned = PLATFORM_MODULES.filter((item) => item.state === "Planned");
  const about = useCampaignAbout();

  return (
    <div className="workflow pilot-overview">
      <ResearchCampaigns about={about} />
      <section aria-label="Available demonstration">
        <h2>Available demonstration</h2>
        <h3>PDL1 binder design</h3>
        <p>Explore a recorded protein design workflow and its saved results.</p>
        <div className="pilot-actions">
          <button type="button" disabled={!demonstrationId} onClick={onOpenDemonstration}>
            Open demonstration
          </button>
        </div>
        {demonstrationId ? null : (
          <p>
            No demonstration journey is configured for this installation, and none is recorded in
            this browser.
          </p>
        )}
      </section>
      <section aria-label="Planned modules">
        <h2>Planned modules</h2>
        <ul>
          {planned.map((item) => (
            <li key={item.id}>{item.label}</li>
          ))}
        </ul>
        <p>These modules are planned.</p>
      </section>
      {campaign ? (
        <details>
          <summary>Reference configurations</summary>
          <p>
            CD22 and TACI are local VHH references. Choosing one does not change the PDL1 design
            request and does not start an operation.
          </p>
          <p>Missing fields in a reference are not tasks for the PDL1 demonstration.</p>
          <div className="choices">
            {campaignPreview.campaigns.map((item) => (
              <button
                key={item.campaign_id}
                type="button"
                aria-pressed={item.campaign_id === campaign.campaign_id}
                onClick={() => onCampaignChange(item.campaign_id)}
              >
                {item.target_id} / PDB {item.source_identifier}
              </button>
            ))}
          </div>
          <section aria-label="Reference summary">
            <h3>Reference summary</h3>
            <dl>
              <div>
                <dt>Target</dt>
                <dd>{campaign.target_id}</dd>
              </div>
              <div>
                <dt>Modality</dt>
                <dd>{campaign.modality.toUpperCase()}</dd>
              </div>
              <div>
                <dt>Structure</dt>
                <dd>
                  {campaign.source_type.toUpperCase()} {campaign.source_identifier}
                </dd>
              </div>
              <div>
                <dt>Description</dt>
                <dd>{campaign.provenance_note}</dd>
              </div>
            </dl>
          </section>
          <details>
            <summary>Reference details</summary>
            <p>
              These facts belong to the selected VHH reference. They do not set the PDL1
              demonstration budget and they do not start an operation.
            </p>
            <TargetStage campaign={campaign} />
            <ReferenceTools campaign={campaign} />
            <details>
              <summary>VHH scaffold</summary>
              <dl>
                <div>
                  <dt>Scaffold ID</dt>
                  <dd>{campaign.scaffold.scaffold_id}</dd>
                </div>
                <div>
                  <dt>Version</dt>
                  <dd>{recordedLabel(campaign.scaffold.scaffold_version)}</dd>
                </div>
                <div>
                  <dt>Masked sequence</dt>
                  <dd>
                    <pre>{campaign.scaffold.masked_sequence}</pre>
                  </dd>
                </div>
                <div>
                  <dt>Mutable regions</dt>
                  <dd>
                    {campaign.scaffold.mutable_regions
                      .map((region) => `${region.name} ${region.start}–${region.end}`)
                      .join(", ")}
                  </dd>
                </div>
                <div>
                  <dt>CDR regions</dt>
                  <dd>
                    {campaign.scaffold.cdr_regions
                      .map((region) => `${region.name.toUpperCase()} ${region.start}–${region.end}`)
                      .join(", ")}
                  </dd>
                </div>
              </dl>
            </details>
          </details>
        </details>
      ) : null}
      <details>
        <summary>Technical details</summary>
        <p>
          Saved campaigns are stored separately from design journeys and results. CD22 and TACI
          remain a local reference projection. That projection is not a saved campaign, and it is
          not linked to a design journey or a result.
        </p>
        <p>
          Scientific runner readiness is a separate read. It is not the PDL1 demonstration launcher.
        </p>
        {runnerReadiness}
        {runs.length > 0 ? (
          <section aria-label="Legacy prototype records">
            <h3>Legacy prototype records</h3>
            <p>These records belong to the local session fixture. They are not the PDL1 design journey.</p>
            <ul>
              {runs.map((run) => (
                <li key={run.run_id}>
                  {run.run_id}. {run.display_status}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </details>
      <CampaignAbout open={about.open} onClose={about.close} returnFocus={about.returnFocus} />
    </div>
  );
}

function TargetStage({ campaign }: { campaign: CampaignPreview }) {
  const overlap = campaign.prepared_target_chain_ids.filter((chain) =>
    campaign.source_partner_chain_ids.includes(chain),
  );
  return (
    <section aria-label="Target and hotspots">
      <dl>
        <div>
          <dt>Source structure</dt>
          <dd>
            {campaign.source_type} {campaign.source_identifier}
          </dd>
        </div>
        <div>
          <dt>Source target chains</dt>
          <dd>{campaign.source_target_chain_ids.join(", ")}</dd>
        </div>
        <div>
          <dt>Source partner chains</dt>
          <dd>{campaign.source_partner_chain_ids.join(", ")}</dd>
        </div>
        <div>
          <dt>Prepared artifact</dt>
          <dd>{campaign.prepared_artifact}</dd>
        </div>
        <div>
          <dt>Prepared content digest</dt>
          <dd>{recordedLabel(campaign.prepared_content_digest)}</dd>
        </div>
        <div>
          <dt>Prepared chains</dt>
          <dd>{campaign.prepared_target_chain_ids.join(", ")}</dd>
        </div>
      </dl>
      <h3>Namespace mapping</h3>
      <ul>
        {campaign.chain_mapping.map((item) => (
          <li key={`${item.source_chain_id}-${item.prepared_chain_id}`}>
            source target {item.source_chain_id} → prepared target {item.prepared_chain_id}
          </li>
        ))}
      </ul>
      {overlap.map((chain) => (
        <p key={chain}>source partner chain {chain} != prepared target chain {chain}</p>
      ))}
      <h3>Preparation</h3>
      <ol>
        {campaign.preparation.map((step) => (
          <li key={step.order}>{OPERATION_LABELS[step.operation] ?? step.operation}</li>
        ))}
      </ol>
      <h3>Epitope</h3>
      <dl>
        <div>
          <dt>Method</dt>
          <dd>{campaign.epitope.derivation_method}</dd>
        </div>
        <div>
          <dt>Cutoff</dt>
          <dd>{campaign.epitope.contact_cutoff_angstrom} Å</dd>
        </div>
      </dl>
      <ul aria-label="Hotspot residues">
        {campaign.epitope.residues.map((residue) => (
          <li key={`${residue.chain_namespace}-${residue.chain_id}-${residue.residue_number}`}>
            {residue.chain_namespace} {residue.chain_id}
            {residue.residue_number}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ResearchCampaigns({
  about,
}: {
  about: { show: (trigger: HTMLElement | null) => void };
}) {
  const [campaigns, setCampaigns] = useState<CampaignRecord[] | null>(null);
  const [listError, setListError] = useState("");
  const [composing, setComposing] = useState(false);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [notes, setNotes] = useState("");
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<CampaignRecord | null>(null);
  const [detailError, setDetailError] = useState("");

  useEffect(() => {
    let cancelled = false;
    listCampaigns()
      .then((items) => {
        if (!cancelled) {
          setCampaigns(items);
          setListError("");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCampaigns(null);
          setListError("Saved campaigns could not be read.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedObjective = objective.trim();
    if (!trimmedName) {
      setFormError("Campaign name is required.");
      return;
    }
    if (!trimmedObjective) {
      setFormError("Scientific objective is required.");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      const created = await createCampaign({
        name: trimmedName,
        scientific_objective: trimmedObjective,
        notes: notes.trim(),
      });
      setCampaigns((current) => [...(current ?? []), created]);
      setSelected(created);
      setComposing(false);
      setName("");
      setObjective("");
      setNotes("");
    } catch {
      setFormError("The campaign could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function openSaved(id: string) {
    setComposing(false);
    setDetailError("");
    try {
      setSelected(await readCampaign(id));
    } catch {
      setSelected(null);
      setDetailError("The campaign could not be read.");
    }
  }

  return (
    <section aria-label="Research campaigns">
      <h2>Research campaigns</h2>
      <p>Organize protein design work around a scientific objective.</p>
      <div className="pilot-actions">
        <button type="button" onClick={() => setComposing(true)}>
          New campaign
        </button>
        <button type="button" onClick={(event) => about.show(event.currentTarget)}>
          About this page
        </button>
      </div>
      {composing ? (
        <form className="campaign-form" onSubmit={save}>
          <label>
            Campaign name
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <p>The scientific objective is text supplied by the researcher.</p>
          <label>
            Scientific objective
            <textarea value={objective} onChange={(event) => setObjective(event.target.value)} />
          </label>
          <label>
            Notes
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>
          {formError ? <p role="alert">{formError}</p> : null}
          <div className="pilot-actions">
            <button type="submit" disabled={saving}>
              Save campaign
            </button>
            <button type="button" onClick={() => setComposing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {selected ? (
        <section aria-label="Campaign detail">
          <h3>{selected.name}</h3>
          <p>{selected.scientific_objective}</p>
          <p>{selected.notes ? selected.notes : "No notes are recorded."}</p>
          {selected.design_request_ids.length === 0 ? (
            <p>No design requests are linked to this campaign.</p>
          ) : (
            <ul>
              {selected.design_request_ids.map((id) => (
                <li key={id}>{id}</li>
              ))}
            </ul>
          )}
          <button type="button" onClick={() => setSelected(null)}>
            Back to campaigns
          </button>
        </section>
      ) : (
        <section aria-label="Saved campaigns">
          {listError ? <p role="alert">{listError}</p> : null}
          {detailError ? <p role="alert">{detailError}</p> : null}
          {campaigns === null && !listError ? <p>Loading saved campaigns.</p> : null}
          {campaigns && campaigns.length === 0 ? <p>No campaigns are saved.</p> : null}
          {campaigns && campaigns.length > 0 ? (
            <ul className="campaign-list">
              {campaigns.map((item) => (
                <li key={item.campaign_id}>
                  <button type="button" onClick={() => void openSaved(item.campaign_id)}>
                    {item.name}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      )}
    </section>
  );
}

function ReferenceTools({ campaign }: { campaign: CampaignPreview }) {
  return (
    <section aria-label="Reference tools">
      <h3>Tools named by this reference</h3>
      <p>
        This VHH reference names {campaign.generator_name} as its generator protocol (
        {campaign.protocol_id}). The recorded generator version is{" "}
        {recordedLabel(campaign.generator_version)}. BindCraft is not the generator protocol of this
        reference. The PDL1 demonstration is conducted from Design request.
      </p>
      <ul>
        {campaign.metrics.map((metric) => (
          <li key={metric.metric_name}>
            {metricLabel(metric.metric_name)} {metric.direction === "gte" ? "≥" : metric.direction}{" "}
            {formatThreshold(metric.threshold)} ({roleLabel(metric.role)})
          </li>
        ))}
      </ul>
      <p>
        On this VHH reference, iPTM and pLDDT are generator filters. They are not ranking_score.
        ranking_score remains raw ipSAE.
      </p>
      <p>
        This VHH reference records a stopping policy of {formatCount(campaign.accepted_design_target)}{" "}
        accepted designs and {formatCount(campaign.max_trajectories)} trajectories. That policy is
        not the budget of the PDL1 demonstration.
      </p>
    </section>
  );
}
