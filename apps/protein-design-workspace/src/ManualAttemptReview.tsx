import {
  asCell,
  cellLabel,
  countLabel,
  manualAttemptReview,
  servedPdbUrl,
} from "./manual-attempt-review";
import { StructureTrace } from "./StructureTrace";

const review = manualAttemptReview;
const models = [review.models.model1, review.models.model2] as const;

type ReviewModel = (typeof models)[number];

export function ManualAttemptReview() {
  return (
    <section className="manual-review" aria-label="Manual review of attempt.2LUTUOX4">
      <header>
        <h2>Manual review · {review.attempt_id}</h2>
        <p>
          Design {review.design}. The two models are the MPNN/Relaxed PDBs. Accepted and Accepted/Ranked
          repeat the model2 hash and are not served again.
        </p>
      </header>
      <p className="manual-banner" role="status">
        Manual origin. Platform scientific execution remains disabled. This view does not generate, approve,
        or promote knowledge.
      </p>
      <p className="manual-banner manual-pending">
        Filter {review.filter_policy}. Scientific evaluation is pending.
      </p>
      <p className="manual-banner">
        Outside the ipSAE ranking queue and outside an approved run. This review has no ranking_score.
      </p>
      <section aria-label="BindCraft representative">
        <h3>Representative chosen by BindCraft</h3>
        <p>
          model2 is the representative chosen by BindCraft by global pLDDT ({review.representative.global_plddt_model2}{" "}
          versus {review.representative.global_plddt_model1} on model1). The rule confirmed in the image is the
          highest pLDDT. The Accepted order uses {review.representative.accepted_order_rule} and keeps that PDB.
        </p>
        <p>
          model2 is not the better interface. Binder pLDDT is higher on model1 (
          {review.representative.binder_plddt_model1} versus {review.representative.binder_plddt_model2}). i_pTM is
          0.16 on model1 and 0.05 on model2. n_InterfaceResidues is 15 on model1 and a recorded zero on model2.
        </p>
        <p>
          Target, chain {review.chains.target}. Binder, chain {review.chains.binder}. The chain B sequence
          matches the CSV sequence in both PDBs.
        </p>
        <p>
          InterfaceResidues: {cellLabel(asCell(review.interface_residues))}. That empty field is distinct from
          the recorded zero in 2_n_InterfaceResidues.
        </p>
      </section>
      <div className="manual-models">
        {models.map((model) => (
          <ModelPanel key={model.id} model={model} />
        ))}
      </div>
      <section aria-label="Heavy-atom contacts at 5 angstroms">
        <h3>Heavy-atom contacts at 5 Å</h3>
        <p>
          Independent analysis. A residue pair counts when the minimum heavy-atom distance is at most{" "}
          {review.geometric_contact_criterion.distance_angstrom} Å. This count is independent of BindCraft
          InterfaceResidues, n_InterfaceResidues, dSASA, and ShapeComplementarity.
        </p>
        <div className="manual-models">
          {models.map((model) => (
            <article key={model.id} aria-label={`Contacts for ${model.id}`}>
              <h4>{model.id}</h4>
              <dl>
                <div>
                  <dt>Binder residues in contact</dt>
                  <dd>{countLabel(model.contacts.binder_residues_in_contact)}</dd>
                </div>
                <div>
                  <dt>Target residues in contact</dt>
                  <dd>{countLabel(model.contacts.target_residues_in_contact)}</dd>
                </div>
                <div>
                  <dt>Residue pairs</dt>
                  <dd>{countLabel(model.contacts.residue_pairs)}</dd>
                </div>
                <div>
                  <dt>Minimum heavy-atom distance</dt>
                  <dd>{model.contacts.minimum_heavy_atom_distance_angstrom} Å</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>
      <section aria-label="Metrics by model">
        <h3>Metrics by model and averages</h3>
        <p>
          Each column follows that model's PDB. Numeric averages match the mean of the filled models, within
          0.01. Where model2 is blank, the CSV average treats the empty field as zero; the cell stays empty.
        </p>
        <p>
          InterfaceHbondsPercentage and InterfaceUnsatHbondsPercentage leave model2 blank. The CSV average is
          half of model1, which matches treating the empty field as zero. The cell itself is empty, not the
          string 0.
        </p>
        <div className="manual-table-scroll">
        <table>
          <caption>
            model1 column: SHA256 {review.models.model1.sha256}. model2 column: SHA256 {review.models.model2.sha256}.
          </caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">model1</th>
              <th scope="col">model2</th>
              <th scope="col">Average</th>
            </tr>
          </thead>
          <tbody>
            {review.metrics.map((row) => (
              <tr key={row.metric}>
                <th scope="row">{row.metric}</th>
                <td>{cellLabel(asCell(row.model1))}</td>
                <td>{cellLabel(asCell(row.model2))}</td>
                <td>{cellLabel(asCell(row.average))}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>
      <section aria-label="Models 3 to 5">
        <h3>Models 3, 4, and 5</h3>
        <p>No 3_*, 4_*, or 5_* cell is filled in this CSV. pLDDT for those models is absent.</p>
        <ul>
          {review.absent_models.map((item) => (
            <li key={item.model}>
              {item.model} pLDDT: {cellLabel(asCell(item.pLDDT))}
            </li>
          ))}
        </ul>
      </section>
      <Provenance />
    </section>
  );
}

function ModelPanel({ model }: { model: ReviewModel }) {
  return (
    <article className="manual-model" aria-label={`${model.id}, chain A target, chain B binder`}>
      <h3>
        {model.id}
        {model.representative ? " · representative by global pLDDT" : ""}
      </h3>
      <p>
        Chain A, target, {model.target_residues} residues. Chain B, binder, {model.binder_residues} residues.
      </p>
      <p>
        SHA256 <code>{model.sha256}</code>
      </p>
      {servedPdbUrl(model.pdb_url) ? (
        <p>
          <a href={model.pdb_url}>PDB for {model.id}</a>
        </p>
      ) : null}
      <StructureTrace model={model} />
    </article>
  );
}

function Provenance() {
  const item = review.provenance;
  return (
    <section aria-label="Provenance">
      <h3>Provenance</h3>
      <dl className="manual-provenance">
        <div>
          <dt>Attempt</dt>
          <dd>{review.attempt_id}</dd>
        </div>
        <div>
          <dt>Origin</dt>
          <dd>manual</dd>
        </div>
        <div>
          <dt>Host</dt>
          <dd>
            {item.host}, {item.user}, uid {item.uid}
          </dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{item.started_at}</dd>
        </div>
        <div>
          <dt>Finished</dt>
          <dd>{item.finished_at}</dd>
        </div>
        <div>
          <dt>Exit code</dt>
          <dd>{item.exit_code}</dd>
        </div>
        <div>
          <dt>GPU</dt>
          <dd>
            {item.gpu}. {item.gpu_note}.
          </dd>
        </div>
        <div>
          <dt>Cellertz seed</dt>
          <dd>{item.cellertz_seed}</dd>
        </div>
        <div>
          <dt>Trajectory seed</dt>
          <dd>{item.bindcraft_trajectory_seed}</dd>
        </div>
        <div>
          <dt>Filter</dt>
          <dd>{item.filters_column}</dd>
        </div>
        <div>
          <dt>SIF SHA256</dt>
          <dd>
            <code>{item.sif_sha256}</code>
          </dd>
        </div>
        <div>
          <dt>Launcher SHA256</dt>
          <dd>
            <code>{item.launcher_sha256}</code>
          </dd>
        </div>
        <div>
          <dt>Advanced profile SHA256</dt>
          <dd>
            <code>{item.advanced_profile_sha256}</code>
          </dd>
        </div>
        <div>
          <dt>Checked manifest SHA256</dt>
          <dd>
            <code>{review.source_manifest_sha256}</code>
          </dd>
        </div>
        <div>
          <dt>design_records SHA256</dt>
          <dd>
            <code>{item.design_records_sha256}</code>
          </dd>
        </div>
        <div>
          <dt>Accepted and Ranked</dt>
          <dd>
            Identical to {item.accepted_and_ranked_identical_to}. SHA256 <code>{item.accepted_pdb_sha256}</code>
          </dd>
        </div>
        <div>
          <dt>Trajectory</dt>
          <dd>
            {item.starting_trajectory_text}. {item.trajectory_note} Closing line: {item.closing_line}
          </dd>
        </div>
        <div>
          <dt>Platform execution</dt>
          <dd>{review.platform_scientific_execution}</dd>
        </div>
        <div>
          <dt>Scientific evaluation</dt>
          <dd>pending</dd>
        </div>
      </dl>
    </section>
  );
}
