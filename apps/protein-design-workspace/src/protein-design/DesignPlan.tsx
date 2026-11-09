import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  createDesignPlan,
  readDesignPlan,
  reviseDesignPlan,
  type DesignPlanCatalog,
  type DesignPlanRecord,
  type PlanFact,
  type PlanRevisionEdits,
  type SuggestionChoice,
} from "./design-plan";
import type { SelectedDesignRequest } from "./DesignRequest";
import { DesignSuggestion, EditPlanWithAgentButton, usePlanSuggestion } from "./DesignSuggestion";
import { validateTargetBindingResidues } from "./verified-pdl1-structure";

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  ready_for_review: "Ready for review",
};

export function DesignPlan({ selection }: { selection: SelectedDesignRequest | null }) {
  const [catalog, setCatalog] = useState<DesignPlanCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  const selectionKey = selection ? `${selection.requestId}:${selection.revision}` : "";
  const [seenKey, setSeenKey] = useState(selectionKey);
  if (seenKey !== selectionKey) {
    setSeenKey(selectionKey);
    setCatalog(null);
    setError(null);
    setLoading(selection !== null);
    setCreating(false);
    setSaving(false);
    setNotice(null);
  }

  useEffect(() => {
    if (!selection) {
      return;
    }
    let cancelled = false;
    const requestId = selection.requestId;
    const revision = selection.revision;
    setCatalog(null);
    setError(null);
    setLoading(true);
    void readDesignPlan(requestId).then((loaded) => {
      if (cancelled) {
        return;
      }
      setLoading(false);
      if (!loaded || loaded.requestId !== requestId || loaded.revision !== revision) {
        setError(
          loaded && loaded.revision !== revision
            ? "The selected revision is not the saved revision."
            : "The plan could not be loaded.",
        );
        return;
      }
      setCatalog(loaded);
    });
    return () => {
      cancelled = true;
    };
  }, [selection]);

  async function reviewCurrentPlan() {
    const current = selectionRef.current;
    if (!current) {
      return;
    }
    setNotice("The plan changed. Review the current data before running.");
    const loaded = await readDesignPlan(current.requestId);
    const selected = selectionRef.current;
    if (!selected || selected.requestId !== current.requestId || selected.revision !== current.revision) {
      return;
    }
    if (!loaded || loaded.requestId !== current.requestId || loaded.revision !== current.revision) {
      setError("The plan could not be loaded.");
      return;
    }
    setCatalog(loaded);
  }

  async function create() {
    if (!selection || creating) {
      return;
    }
    const requestId = selection.requestId;
    const revision = selection.revision;
    setCreating(true);
    setError(null);
    const loaded = await createDesignPlan(requestId, revision);
    setCreating(false);
    const current = selectionRef.current;
    if (!current || current.requestId !== requestId || current.revision !== revision) {
      return;
    }
    if (!loaded || loaded.requestId !== requestId || loaded.revision !== revision) {
      setError("The plan was not created. The selected revision was left unchanged.");
      return;
    }
    setCatalog(loaded);
  }

  async function save(edits: PlanRevisionEdits): Promise<"recorded" | "failed"> {
    const current = selectionRef.current;
    const plan = visiblePlan(catalog, current);
    if (!current || !plan || saving) {
      return "failed";
    }
    const requestId = current.requestId;
    const revision = current.revision;
    setSaving(true);
    setNotice(null);
    const result = await reviseDesignPlan(requestId, revision, plan.planId, plan.planRevision, edits);
    setSaving(false);
    const selected = selectionRef.current;
    if (!selected || selected.requestId !== requestId || selected.revision !== revision) {
      return "failed";
    }
    if (result.kind === "conflict") {
      setNotice("This plan revision is no longer current. The saved plan was left unchanged.");
      return "failed";
    }
    if (result.kind === "invalid") {
      setNotice("The plan change was rejected. The saved plan was left unchanged.");
      return "failed";
    }
    if (result.kind !== "recorded" || result.catalog.requestId !== requestId || result.catalog.revision !== revision) {
      setNotice("The plan change was not saved.");
      return "failed";
    }
    setCatalog(result.catalog);
    setNotice(result.changed ? "Plan changes saved." : "No plan change was recorded.");
    return "recorded";
  }

  const visible =
    selection && catalog && catalog.requestId === selection.requestId && catalog.revision === selection.revision
      ? catalog
      : null;
  const current = visible?.plans.find((item) => item.requestRevision === selection?.revision) ?? null;
  return (
    <section className="design-plan" aria-label="Plan">
      <h2>Generation plan</h2>
      {!selection ? <p>Select a saved request in Design request.</p> : null}
      {selection ? (
        <p className="plan-binding">
          Saved request, revision {selection.revision}
        </p>
      ) : null}
      {selection ? <p>{selection.message}</p> : null}
      {selection && loading ? <p>Loading the plan.</p> : null}
      {error ? <p>{error}</p> : null}
      {visible && selection ? (
        <>
          {current === null ? <ShortSummary summary={visible.summary} /> : null}
          <CurrentPlan
            catalog={visible}
            revision={selection.revision}
            creating={creating}
            saving={saving}
            notice={notice}
            onCreate={() => void create()}
            onSave={save}
            onCancel={() => setNotice(null)}
            executionPermitted={visible.executionPermitted}
            onStale={() => void reviewCurrentPlan()}
          />
          <EarlierPlans plans={visible.plans.filter((item) => item.requestRevision !== selection.revision)} />
        </>
      ) : null}
    </section>
  );
}

function ShortSummary({ summary }: { summary: DesignPlanCatalog["summary"] }) {
  return (
    <dl className="plan-fields">
      <FactRow label="Target" presentation={plain(factPresentation(summary.target))} />
      <FactRow label="Candidates requested" presentation={plain(factPresentation(summary.candidatesRequested))} />
      <FactRow label="Generation method" presentation={plain(factPresentation(summary.generationMethod))} />
    </dl>
  );
}

function plain(presentation: PresentedValue): PresentedValue {
  return { value: presentation.value };
}

function displayedStatus(plan: DesignPlanRecord): string {
  return STATUS_LABELS[plan.status] ?? plan.status;
}

function serverExecutionLine(executionPermitted: boolean): string {
  return executionPermitted
    ? "Server execution: the current server allows the demonstration run."
    : "Server execution: the current server does not allow execution.";
}

function progressMessages(plan: DesignPlanRecord): string[] {
  const lines = reviewLines(plan.questions);
  for (const item of plan.questions) {
    const line = EXECUTION_PENDENCY[item.field];
    if (line && !lines.includes(line)) {
      lines.push(line);
    }
  }
  const gpu = plan.parameters.find((item) => item.field === "gpu")?.value ?? null;
  if ((gpu === null || gpu === "not_assigned") && !lines.includes("A GPU is not selected.")) {
    if (gpu === "not_assigned" || plan.questions.length === 0) {
      lines.push("A GPU is not selected.");
    }
  }
  return lines;
}

const EXECUTION_PENDENCY: Record<string, string> = {
  gpu: "A GPU is not selected.",
  cpu_count: "CPUs are still to be defined.",
  memory_mib: "Host RAM is still to be defined.",
  seed: "Process seed is still to be defined.",
  execution_deadline_seconds: "Maximum run time is still to be defined.",
};

function FieldOrigins({
  plan,
  summary,
}: {
  plan: DesignPlanRecord;
  summary: DesignPlanCatalog["summary"] | null;
}) {
  const rows: Array<readonly [string, string]> = [];
  const summaryRows: Array<readonly [string, string | undefined]> = summary
    ? [
        ["Target", origin(summary.target.source)],
        ["Candidates requested", origin(summary.candidatesRequested.source)],
        ["Generation method", origin(summary.generationMethod.source)],
      ]
    : [];
  for (const [label, source] of summaryRows) {
    if (source) {
      rows.push([label, source]);
    }
  }
  for (const item of plan.parameters) {
    if (item.source) {
      rows.push([item.label, item.source]);
    }
  }
  if (rows.length === 0) {
    return null;
  }
  return (
    <dl className="plan-fields">
      {rows.map(([label, source], index) => (
        <FactRow key={`${label}:${index}`} label={label} presentation={{ value: source ?? null }} />
      ))}
    </dl>
  );
}

function FactRow({
  label,
  presentation,
  missing = "To be defined",
}: {
  label: string;
  presentation: PresentedValue;
  missing?: string;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {presentation.value ?? missing}
        {presentation.value && presentation.source ? <span>{presentation.source}</span> : null}
        {presentation.note ? <span>{presentation.note}</span> : null}
      </dd>
    </>
  );
}

function visiblePlan(
  catalog: DesignPlanCatalog | null,
  selection: SelectedDesignRequest | null,
): DesignPlanRecord | null {
  if (!selection || !catalog || catalog.requestId !== selection.requestId || catalog.revision !== selection.revision) {
    return null;
  }
  return catalog.plans.find((item) => item.requestRevision === selection.revision) ?? null;
}

function CurrentPlan({
  catalog,
  revision,
  creating,
  saving,
  notice,
  onCreate,
  onSave,
  onCancel,
  executionPermitted,
  onStale,
}: {
  catalog: DesignPlanCatalog;
  revision: number;
  creating: boolean;
  saving: boolean;
  notice: string | null;
  onCreate: () => void;
  onSave: (edits: PlanRevisionEdits) => Promise<"recorded" | "failed">;
  onCancel: () => void;
  executionPermitted: boolean;
  onStale: () => void;
}) {
  const current = catalog.plans.find((item) => item.requestRevision === revision) ?? null;
  if (!current) {
    return (
      <button type="button" disabled={creating} onClick={onCreate}>
        {creating ? "Creating…" : "Create plan"}
      </button>
    );
  }
  return (
    <PlanBody
      plan={current}
      summary={catalog.summary}
      saving={saving}
      notice={notice}
      onSave={onSave}
      onCancel={onCancel}
      executionPermitted={executionPermitted}
      onStale={onStale}
    />
  );
}

function EarlierPlans({ plans }: { plans: DesignPlanRecord[] }) {
  if (plans.length === 0) {
    return null;
  }
  return (
    <section aria-label="Plans of earlier revisions">
      <h3>Plans of earlier revisions</h3>
      {plans.map((plan) => (
        <PlanBody key={plan.planId} plan={plan} summary={null} earlier />
      ))}
    </section>
  );
}

function PlanBody({
  plan,
  summary,
  earlier = false,
  saving = false,
  notice = null,
  onSave,
  onCancel,
  executionPermitted = false,
  onStale,
}: {
  plan: DesignPlanRecord;
  summary: DesignPlanCatalog["summary"] | null;
  earlier?: boolean;
  saving?: boolean;
  notice?: string | null;
  onSave?: (edits: PlanRevisionEdits) => Promise<"recorded" | "failed">;
  onCancel?: () => void;
  executionPermitted?: boolean;
  onStale?: () => void;
}) {
  const blockers = progressMessages(plan);
  const needsConfiguration = blockers.some((line) => line !== "The server does not allow execution.");
  const status = displayedStatus(plan);
  const [runNote, setRunNote] = useState<string | null>(null);
  const [runLog, setRunLog] = useState<string | null>(null);
  const formKey = `${plan.planId}:${plan.planRevision}`;
  const [seenForm, setSeenForm] = useState(formKey);
  const [editing, setEditing] = useState(false);
  const [length, setLength] = useState("");
  const [hotspotMode, setHotspotMode] = useState("");
  const [hotspotValue, setHotspotValue] = useState("");
  const [filterMode, setFilterMode] = useState("");
  const [seed, setSeed] = useState("");
  const [gpu, setGpu] = useState("");
  const [cpus, setCpus] = useState("");
  const [memory, setMemory] = useState("");
  const [deadline, setDeadline] = useState("");
  const [formNotice, setFormNotice] = useState<string | null>(null);
  const [applied, setApplied] = useState<Record<string, { source: string; suggestionId: string; value: string }>>({});
  const suggestion = usePlanSuggestion(plan, !earlier);
  if (seenForm !== formKey) {
    setSeenForm(formKey);
    setEditing(false);
    setLength("");
    setHotspotMode("");
    setHotspotValue("");
    setFilterMode("");
    setSeed("");
    setGpu("");
    setCpus("");
    setMemory("");
    setDeadline("");
    setFormNotice(null);
    setApplied({});
    setRunNote(null);
    setRunLog(null);
  }
  return (
    <article aria-label={earlier ? `Plan of revision ${plan.requestRevision}` : "Current plan"}>
      {earlier ? <h3>Plan of revision {plan.requestRevision}.</h3> : null}
      <p>Status: {status}</p>
      {earlier ? null : <p>{serverExecutionLine(executionPermitted)}</p>}
      {needsConfiguration ? <p>Needs configuration</p> : null}
      <dl className="plan-fields">
        <FactRow label="Target" presentation={plain(targetPresentation(summary, plan))} />
        <FactRow label="Candidates requested" presentation={plain(summaryFact(summary, "candidatesRequested"))} missing={summary ? "To be defined" : "Not recorded on this plan"} />
        <FactRow label="Generation method" presentation={plain(summaryFact(summary, "generationMethod"))} missing={summary ? "To be defined" : "Not recorded on this plan"} />
        <FactRow label="Binder length (amino acids)" presentation={plain(parameterPresentation(plan, "binder_length"))} />
        <FactRow label="Binding site (hotspots)" presentation={plain(parameterPresentation(plan, "hotspot_assumption"))} />
        <FactRow label="Candidate filtering" presentation={plain(parameterPresentation(plan, "filter_policy"))} />
      </dl>
      {blockers.length > 0 ? (
        <ul>
          {blockers.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      {onSave && !editing ? (
        <div className="plan-actions" role="group" aria-label="Plan editing">
          <button type="button" onClick={() => beginEdit(plan, {
            setEditing,
            setLength,
            setHotspotMode,
            setHotspotValue,
            setFilterMode,
            setSeed,
            setGpu,
            setCpus,
            setMemory,
            setDeadline,
            setFormNotice,
          })}>
            Edit plan
          </button>
          {!earlier ? (
            <EditPlanWithAgentButton suggesting={suggestion.suggesting} onSuggest={() => void suggestion.suggest()} />
          ) : null}
        </div>
      ) : null}
      {onSave && editing ? (
        <div className="plan-editor">
          <label>
            Binder length (amino acids)
            <input value={length} inputMode="numeric" autoComplete="off" onChange={(event) => setLength(event.target.value)} />
          </label>
          <label>
            Binding site (hotspots)
            <select value={hotspotMode} onChange={(event) => setHotspotMode(event.target.value)}>
              <option value="">Select how to define the binding site</option>
              <option value="residues">Specify hotspot residues</option>
              <option value="none">Design without predefined hotspots</option>
            </select>
          </label>
          <p>Optionally guide binder design toward specific residues on the target protein.</p>
          {hotspotMode === "residues" ? (
            <>
              <label>
                Hotspot residues — target PDB numbering
                <input value={hotspotValue} autoComplete="off" onChange={(event) => setHotspotValue(event.target.value)} />
              </label>
              <p>Use chain and residue numbers from the selected target structure.</p>
            </>
          ) : null}
          {hotspotMode === "none" ? (
            <p>Design without predefined hotspots. Binder design is not guided toward selected target residues.</p>
          ) : null}
          <label>
            Candidate filtering
            <select value={filterMode} onChange={(event) => setFilterMode(event.target.value)}>
              <option value="">Choose</option>
              <option value="technical_demonstration">Technical demonstration — no scientific filtering</option>
            </select>
          </label>
          <p>Candidates will not be screened using scientific quality filters.</p>
        </div>
      ) : null}
      <details key={`${formKey}:execution`}>
        <summary>Execution settings</summary>
        <dl className="plan-fields">
          {EXECUTION_ROWS.map(([field, label]) => (
            <FactRow key={field} label={label} presentation={plain(parameterPresentation(plan, field))} missing="Pending" />
          ))}
        </dl>
        {onSave && editing ? (
          <div className="plan-editor">
            <label>
              Process seed
              <input value={seed} inputMode="numeric" autoComplete="off" onChange={(event) => setSeed(event.target.value)} />
            </label>
            <label>
              GPU assignment
              <select value={gpu} onChange={(event) => setGpu(event.target.value)}>
                <option value="">Choose</option>
                <option value="not_assigned">Do not assign a GPU</option>
                {["0", "1", "2", "3", "4", "5", "6", "7"].map((index) => (
                  <option key={index} value={index}>
                    {index}
                  </option>
                ))}
              </select>
            </label>
            <label>
              CPUs
              <input value={cpus} inputMode="numeric" autoComplete="off" onChange={(event) => setCpus(event.target.value)} />
            </label>
            <label>
              Host RAM (MiB)
              <input value={memory} inputMode="numeric" autoComplete="off" onChange={(event) => setMemory(event.target.value)} />
            </label>
            <label>
              Maximum run time (seconds)
              <input value={deadline} inputMode="numeric" autoComplete="off" onChange={(event) => setDeadline(event.target.value)} />
            </label>
          </div>
        ) : null}
      </details>
      {onSave && editing ? (
        <div className="plan-actions" role="group" aria-label="Plan changes">
          <button
            className="save-plan"
            type="button"
            disabled={saving}
            onClick={() => {
              const edits = editsFromInputs(
                { length, hotspotMode, hotspotValue, filterMode, seed, gpu, cpus, memory, deadline },
                valuesFromPlan(plan),
                applied,
              );
              if (typeof edits === "object" && edits !== null && "residueMessage" in edits) {
                setFormNotice(edits.residueMessage);
                return;
              }
              if (edits === "invalid-number") {
                setFormNotice("The plan change was rejected. The saved plan was left unchanged.");
                return;
              }
              setFormNotice(null);
              void onSave(edits).then((outcome) => {
                if (outcome === "recorded") {
                  setEditing(false);
                }
              });
            }}
          >
            {saving ? "Saving…" : "Save plan changes"}
          </button>
          <button
            className="cancel-plan"
            type="button"
            disabled={saving}
            onClick={() => {
              setEditing(false);
              setFormNotice(null);
              setApplied({});
              onCancel?.();
            }}
          >
            Cancel
          </button>
        </div>
      ) : null}
      {formNotice ? <p>{formNotice}</p> : null}
      {notice ? <p>{notice}</p> : null}
      <details key={`${formKey}:technical`}>
        <summary>Technical details</summary>
        <p>Planning origin: deterministic generation plan. It was recorded without a model. A later specialist consultation is separate and does not change this origin.</p>
        <p>Plan id: {plan.planId}</p>
        <p>Plan revision: {plan.planRevision}</p>
        <p>Request revision: {plan.requestRevision}</p>
        <p>Digest: {plan.digest}</p>
        <FieldOrigins plan={plan} summary={summary} />
        <p>Creating a plan does not start generation.</p>
        <p>The requested quantity does not guarantee approved candidates.</p>
        <p>Filling these fields does not make the plan ready to run.</p>
        <p>This process seed does not guarantee reproduction of the BindCraft trajectory.</p>
        {interpretationLimitations(plan).length > 0 ? (
          <ul>
            {interpretationLimitations(plan).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : null}
        {preparedStatements(plan).length > 0 ? (
          <>
            <p>Preparation does not approve, authorize, or start execution.</p>
            <ul>
              {preparedStatements(plan).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        ) : null}
        {executionBlockers(plan).length > 0 ? (
          <ul>
            {executionBlockers(plan).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : null}
        {runNote ? <p>{runNote}</p> : null}
        {runLog ? (
          <>
            <p>Executor log</p>
            <pre>{runLog}</pre>
          </>
        ) : null}
      </details>
      {!earlier ? (
        <DesignSuggestion
          suggestion={suggestion}
          onApply={(choices, suggestionId) => {
            if (choices.length === 0) {
              return;
            }
            beginEdit(plan, {
              setEditing,
              setLength,
              setHotspotMode,
              setHotspotValue,
              setFilterMode,
              setSeed,
              setGpu,
              setCpus,
              setMemory,
              setDeadline,
              setFormNotice,
            });
            const next: Record<string, { source: string; suggestionId: string; value: string }> = {};
            for (const choice of choices) {
              const value = applyChoice(choice, {
                setLength,
                setHotspotMode,
                setHotspotValue,
                setFilterMode,
                setCpus,
                setMemory,
                setDeadline,
              });
              if (value !== null) {
                next[choice.field] = { source: choice.source, suggestionId, value };
              }
            }
            setApplied(next);
          }}
        />
      ) : null}
      {!earlier && onStale ? (
        <RunDesign
          plan={plan}
          editing={editing}
          executionPermitted={executionPermitted}
          onStale={onStale}
          onNote={setRunNote}
          onLog={setRunLog}
        />
      ) : null}
    </article>
  );
}

type PresentedValue = {
  value: string | null;
  source?: string;
  note?: string;
};

const EXECUTION_ROWS = [
  ["gpu", "GPU"],
  ["cpu_count", "CPUs"],
  ["memory_mib", "Host RAM (MiB)"],
  ["seed", "Process seed"],
  ["execution_deadline_seconds", "Maximum run time (seconds)"],
] as const;

const EXECUTION_FIELDS = new Set<string>(EXECUTION_ROWS.map(([field]) => field));

const CLEAR_REVIEW: Record<string, string> = {
  "Which target should this generation use?": "The target is still to be defined.",
  "How many residues should the binder have?": "Binder length (amino acids) is still to be defined.",
  "Which hotspot assumption should be recorded?":
    "The binding site (hotspots) is still to be defined before generation can be prepared.",
  "Which filter policy should be recorded? no_filters is not assumed and does not establish scientific quality.":
    "Candidate filtering is still to be defined.",
};

function factPresentation(fact: PlanFact): PresentedValue {
  if (fact.pending || fact.value === null) {
    return { value: null };
  }
  return { value: String(fact.value), source: origin(fact.source) };
}

function origin(source: PlanFact["source"]): string | undefined {
  if (source === "researcher") {
    return "Researcher";
  }
  if (source === "server_configuration") {
    return "Server configuration";
  }
  return undefined;
}

function summaryFact(
  summary: DesignPlanCatalog["summary"] | null,
  key: "candidatesRequested" | "generationMethod",
): PresentedValue {
  if (!summary) {
    return { value: null };
  }
  return factPresentation(summary[key]);
}

function targetPresentation(summary: DesignPlanCatalog["summary"] | null, plan: DesignPlanRecord): PresentedValue {
  const recordedTarget = parameterPresentation(plan, "target_identity");
  if (summary && !summary.target.pending && summary.target.value !== null) {
    const value = String(summary.target.value);
    const note =
      recordedTarget.value !== null && recordedTarget.value !== value
        ? `Plan field target_identity: ${recordedTarget.value}`
        : undefined;
    return { value, source: origin(summary.target.source), note };
  }
  return recordedTarget;
}

function parameterPresentation(plan: DesignPlanRecord, field: string): PresentedValue {
  const item = plan.parameters.find((parameter) => parameter.field === field);
  const note = capabilityNote(plan, field);
  if (field === "hotspot_assumption" && plan.hotspotStrategy === "none") {
    return { value: "Design without predefined hotspots", source: "Researcher", note };
  }
  if (
    field === "filter_policy"
    && (plan.filterMode === "technical_demonstration" || item?.value === "no_filters")
  ) {
    return { value: "Technical demonstration — no scientific filtering.", source: item?.source ?? "Researcher", note };
  }
  if (!item) {
    return { value: null };
  }
  return { value: item.value, source: item.source, note };
}

function hasIntegration(plan: DesignPlanRecord, field: string): boolean {
  return plan.integrations.some((integration) => integration.field === field);
}

function capabilityNote(plan: DesignPlanRecord, field: string): string | undefined {
  if (!hasIntegration(plan, field)) {
    return undefined;
  }
  if (field === "binder_length") {
    return "Prepared length. BindCraft capacity for this length is not recorded.";
  }
  if (field === "hotspot_assumption") {
    return plan.hotspotStrategy === "none"
      ? "Prepared as an empty hotspot list."
      : "Prepared in the derived settings.";
  }
  if (field === "filter_policy") {
    return "no_filters sets every threshold to null, so scientific quality conditions are skipped.";
  }
  if (field === "seed") {
    return "Recorded only. Prepared settings do not use it.";
  }
  if (field === "cpu_count") {
    return "Imposed Apptainer CPU limit when a launch is delivered.";
  }
  if (field === "memory_mib") {
    return "Imposed host-memory limit, not GPU memory.";
  }
  if (field === "gpu") {
    return plan.parameters.find((item) => item.field === "gpu")?.value === "not_assigned"
      ? "A launch without a selected GPU stays blocked."
      : "Selected for CUDA_VISIBLE_DEVICES. No VRAM is reserved.";
  }
  if (field === "execution_deadline_seconds") {
    return "Apptainer supervisor timeout. The process tree is stopped on timeout or worker loss.";
  }
  return undefined;
}

function RunDesign({
  plan,
  editing,
  executionPermitted,
  onStale,
  onNote,
  onLog,
}: {
  plan: DesignPlanRecord;
  editing: boolean;
  executionPermitted: boolean;
  onStale: () => void;
  onNote: (note: string | null) => void;
  onLog: (log: string | null) => void;
}) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(null);
  const startedAt = useRef<number | null>(null);
  const [attempt, setAttempt] = useState<{
    id: string;
    status: string;
    diagnosis: string | null;
    launched: boolean;
  } | null>(null);
  const [liveOutput, setLiveOutput] = useState("");
  const [serverRunning, setServerRunning] = useState(false);
  const liveOutputRef = useRef<HTMLPreElement | null>(null);
  const idempotencyKey = `run_${plan.planId}_${plan.planRevision}_${plan.digest}`;
  const gpu = plan.parameters.find((item) => item.field === "gpu")?.value ?? null;
  const configured = plan.questions.length === 0 && gpu !== null && gpu !== "not_assigned";
  const watchRunning = pending || serverRunning || (executionPermitted && configured);
  useEffect(() => {
    if (!watchRunning) {
      return;
    }
    let cancelled = false;
    let timer = 0;
    async function readRunning() {
      try {
        const response = await fetch(`/api/design-journeys/by-request/${encodeURIComponent(plan.requestId)}`);
        if (!response.ok) {
          return;
        }
        const body = (await response.json()) as {
          live_output?: unknown;
          execution?: { source?: unknown; status?: unknown; terminal?: unknown };
        };
        if (cancelled) {
          return;
        }
        const execution = body.execution;
        const running = execution?.source === "run_design"
          && execution.status === "running"
          && execution.terminal !== true;
        setServerRunning(running);
        if (typeof body.live_output === "string") {
          setLiveOutput(body.live_output);
        }
        if (running || pending) {
          timer = window.setTimeout(() => void readRunning(), 2000);
        }
      } catch {
        return;
      }
    }
    void readRunning();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [watchRunning, pending, plan.requestId]);
  useEffect(() => {
    const field = liveOutputRef.current;
    if (field) {
      field.scrollTop = field.scrollHeight;
    }
  }, [liveOutput]);
  useEffect(() => {
    if (!pending) {
      return;
    }
    startedAt.current = Date.now();
    setElapsedSeconds(0);
    const timer = window.setInterval(() => {
      const start = startedAt.current;
      if (start !== null) {
        setElapsedSeconds(Math.floor((Date.now() - start) / 1000));
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [pending]);
  const running = pending || serverRunning;
  if (!configured || (editing && !running && attempt === null && failure === null)) {
    return null;
  }
  const ready = executionPermitted && attempt === null && !running;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) {
      return;
    }
    setPending(true);
    setFailure(null);
    try {
      const response = await fetch("/api/design-journeys/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          request_id: plan.requestId,
          request_revision: plan.requestRevision,
          plan_id: plan.planId,
          revision: plan.planRevision,
          digest: plan.digest,
          idempotency_key: idempotencyKey,
        }),
      });
      const body = (await response.json()) as {
        status?: string;
        run?: {
          attempt_id?: string;
          status?: string;
          diagnosis?: string | null;
          launched?: boolean;
          executor_log?: string;
          automatic_retry?: boolean;
          plan_id?: string;
          revision?: number;
          digest?: string;
        };
      };
      if (response.status === 409 || body.status === "plan_revision_conflict") {
        onStale();
        return;
      }
      const run = body.run;
      if (
        !response.ok ||
        !run ||
        run.plan_id !== plan.planId ||
        run.revision !== plan.planRevision ||
        run.digest !== plan.digest ||
        run.automatic_retry !== false ||
        typeof run.attempt_id !== "string"
      ) {
        setFailure(body.status === "execution_not_permitted"
          ? "The server does not allow execution. Generation was not started."
          : "The run was not started.");
        return;
      }
      const recorded = {
        id: run.attempt_id,
        status: run.status || "recorded",
        diagnosis: typeof run.diagnosis === "string" ? run.diagnosis : null,
        launched: run.launched === true,
      };
      setAttempt(recorded);
      onLog(typeof run.executor_log === "string" && run.executor_log.trim() ? run.executor_log : null);
      onNote(
        `Attempt ${recorded.id} is linked to this request. Status ${recorded.status}.`
        + (recorded.diagnosis ? ` Diagnosis ${recorded.diagnosis}.` : "")
        + (recorded.launched ? "" : " Generation was not started.")
        + " This attempt was not repeated.",
      );
    } catch {
      setFailure("The run was not started.");
    } finally {
      setPending(false);
    }
  }

  const elapsed = elapsedSeconds ?? 0;
  if (!executionPermitted && !pending && attempt === null && failure === null) {
    return null;
  }
  return (
    <form aria-label="Run design" onSubmit={(event) => void submit(event)}>
      {executionPermitted && !editing ? (
        <div className="run-design-action">
          <button className="run-design" type="submit" disabled={!ready}>
            {running ? "Running…" : "Run design"}
          </button>
          <p>This action will use the GPU.</p>
        </div>
      ) : null}
      {running ? <p>Status Running.{pending ? ` Elapsed ${elapsed}s.` : " A generation is already running."}</p> : null}
      {running ? (
        <pre ref={liveOutputRef} role="log" aria-label="BindCraft output" className="executor-live">
          {liveOutput || "Waiting for BindCraft output."}
        </pre>
      ) : null}
      {attempt ? (
        <p>
          Status {attempt.status}. Elapsed {elapsed}s.
          {attempt.diagnosis ? ` ${attempt.diagnosis}.` : ""}
          {attempt.launched ? " Result recorded." : " Generation was not started."}
        </p>
      ) : null}
      {failure ? <p>{failure}</p> : null}
    </form>
  );
}

function executionBlockers(plan: DesignPlanRecord): string[] {
  if (plan.integrations.some((item) => item.field === "gpu" && item.support === "unsupported")) {
    return ["A launch without a selected GPU stays blocked."];
  }
  return [];
}

function interpretationLimitations(plan: DesignPlanRecord): string[] {
  const limitations = ["Preparing settings does not start BindCraft."];
  if (hasIntegration(plan, "seed")) {
    limitations.push("Process seed does not set the BindCraft trajectory and does not reproduce it.");
  }
  if (hasIntegration(plan, "binder_length")) {
    limitations.push("A binder length the installed pipeline can accept is not a scientific success guarantee.");
  }
  if (hasIntegration(plan, "filter_policy")) {
    limitations.push("The no_filters file sets every threshold to null, so BindCraft skips every scientific quality condition.");
  }
  return limitations;
}

function preparedStatements(plan: DesignPlanRecord): string[] {
  const statements: string[] = [];
  if (hasIntegration(plan, "binder_length")) {
    statements.push("Binder length is written into the prepared settings.");
  }
  if (hasIntegration(plan, "hotspot_assumption")) {
    statements.push(
      plan.hotspotStrategy === "none"
        ? "Design without predefined hotspots is written as an empty hotspot list."
        : "Hotspot residues are written into the prepared settings.",
    );
  }
  if (hasIntegration(plan, "filter_policy")) {
    statements.push("The technical demonstration filter is named in the prepared settings. Candidates will not be screened using scientific quality filters.");
  }
  if (hasIntegration(plan, "cpu_count")) {
    statements.push("CPU count is an imposed cgroup limit on the Apptainer command.");
  }
  if (hasIntegration(plan, "memory_mib")) {
    statements.push("Host RAM is an imposed cgroup limit on the Apptainer command. It is not GPU memory.");
  }
  if (plan.integrations.some((item) => item.field === "gpu" && item.support !== "unsupported")) {
    statements.push("The selected GPU is passed as CUDA_VISIBLE_DEVICES. Device nodes stay visible, and no VRAM is reserved.");
  }
  if (hasIntegration(plan, "execution_deadline_seconds")) {
    statements.push("Maximum run time is the Apptainer supervisor timeout. On timeout or worker loss the supervisor stops the process tree.");
  }
  return statements;
}

type PlanForm = {
  length: string;
  hotspotMode: string;
  hotspotValue: string;
  filterMode: string;
  seed: string;
  gpu: string;
  cpus: string;
  memory: string;
  deadline: string;
};

function valuesFromPlan(plan: DesignPlanRecord): PlanForm {
  const recorded = (field: string) => plan.parameters.find((item) => item.field === field)?.value ?? "";
  return {
    length: recorded("binder_length"),
    hotspotMode: plan.hotspotStrategy ?? "",
    hotspotValue: plan.hotspotStrategy === "residues" ? recorded("hotspot_assumption") : "",
    filterMode: plan.filterMode ?? "",
    seed: recorded("seed"),
    gpu: recorded("gpu"),
    cpus: recorded("cpu_count"),
    memory: recorded("memory_mib"),
    deadline: recorded("execution_deadline_seconds"),
  };
}

function beginEdit(
  plan: DesignPlanRecord,
  setters: {
    setEditing: (value: boolean) => void;
    setLength: (value: string) => void;
    setHotspotMode: (value: string) => void;
    setHotspotValue: (value: string) => void;
    setFilterMode: (value: string) => void;
    setSeed: (value: string) => void;
    setGpu: (value: string) => void;
    setCpus: (value: string) => void;
    setMemory: (value: string) => void;
    setDeadline: (value: string) => void;
    setFormNotice: (value: string | null) => void;
  },
) {
  const values = valuesFromPlan(plan);
  setters.setLength(values.length);
  setters.setHotspotMode(values.hotspotMode);
  setters.setHotspotValue(values.hotspotValue);
  setters.setFilterMode(values.filterMode);
  setters.setSeed(values.seed);
  setters.setGpu(values.gpu);
  setters.setCpus(values.cpus);
  setters.setMemory(values.memory);
  setters.setDeadline(values.deadline);
  setters.setFormNotice(null);
  setters.setEditing(true);
}

function editsFromInputs(
  input: PlanForm,
  baseline: PlanForm,
  applied: Record<string, { source: string; suggestionId: string; value: string }> = {},
): PlanRevisionEdits | { residueMessage: string } | "invalid-number" {
  const scientific: Record<string, unknown> = {};
  const technical: Record<string, unknown> = {};
  const length = changedInteger(input.length, baseline.length);
  const seed = changedInteger(input.seed, baseline.seed);
  const cpus = changedInteger(input.cpus, baseline.cpus);
  const memory = changedInteger(input.memory, baseline.memory);
  const deadline = changedInteger(input.deadline, baseline.deadline);
  if ([length, seed, cpus, memory, deadline].includes("invalid")) {
    return "invalid-number";
  }
  if (typeof length === "number") {
    scientific.binder_length = length;
  }
  if (input.hotspotMode === "residues") {
    const hotspotText = input.hotspotValue.trim();
    const hotspotChanged = input.hotspotMode !== baseline.hotspotMode || hotspotText !== baseline.hotspotValue.trim();
    if (hotspotChanged) {
      const residues = validateTargetBindingResidues(hotspotText);
      if (residues.status !== "valid") {
        return {
          residueMessage:
            residues.status === "empty"
              ? "Specify hotspot residues needs a residue list. An empty value does not mean design without predefined hotspots."
              : residues.message,
        };
      }
      scientific.hotspot = { mode: "residues", value: hotspotText };
    }
  } else if (input.hotspotMode === "none" && input.hotspotMode !== baseline.hotspotMode) {
    scientific.hotspot = { mode: "none" };
  }
  if (input.filterMode === "technical_demonstration" && input.filterMode !== baseline.filterMode) {
    scientific.filter_policy = { mode: "technical_demonstration", policy: "no_filters" };
  }
  if (typeof seed === "number") {
    technical.seed = seed;
  }
  if (input.gpu !== "" && input.gpu !== baseline.gpu) {
    technical.gpu = input.gpu;
  }
  if (typeof cpus === "number") {
    technical.cpu_count = cpus;
  }
  if (typeof memory === "number") {
    technical.memory_mib = memory;
  }
  if (typeof deadline === "number") {
    technical.execution_deadline_seconds = deadline;
  }
  const adoptedOrigins: NonNullable<PlanRevisionEdits["adoptedOrigins"]> = {};
  const currentValue: Record<string, string> = {
    binder_length: input.length.trim(),
    hotspot_assumption: input.hotspotMode === "residues" ? input.hotspotValue.trim() : input.hotspotMode,
    filter_policy: input.filterMode,
    seed: input.seed.trim(),
    gpu: input.gpu,
    cpu_count: input.cpus.trim(),
    memory_mib: input.memory.trim(),
    execution_deadline_seconds: input.deadline.trim(),
  };
  for (const [field, origin] of Object.entries(applied)) {
    if (currentValue[field] === origin.value) {
      adoptedOrigins[field] = { source: origin.source, suggestionId: origin.suggestionId };
    }
  }
  return { scientific, technical, ...(Object.keys(adoptedOrigins).length > 0 ? { adoptedOrigins } : {}) };
}

function applyChoice(
  choice: SuggestionChoice,
  setters: {
    setLength: (value: string) => void;
    setHotspotMode: (value: string) => void;
    setHotspotValue: (value: string) => void;
    setFilterMode: (value: string) => void;
    setCpus: (value: string) => void;
    setMemory: (value: string) => void;
    setDeadline: (value: string) => void;
  },
): string | null {
  const value = String(choice.suggested);
  if (choice.field === "binder_length") {
    setters.setLength(value);
    return value;
  }
  if (choice.field === "hotspot_assumption") {
    setters.setHotspotMode("residues");
    setters.setHotspotValue(value);
    return value;
  }
  if (choice.field === "filter_policy") {
    setters.setFilterMode("technical_demonstration");
    return "technical_demonstration";
  }
  if (choice.field === "cpu_count") {
    setters.setCpus(value);
    return value;
  }
  if (choice.field === "memory_mib") {
    setters.setMemory(value);
    return value;
  }
  if (choice.field === "execution_deadline_seconds") {
    setters.setDeadline(value);
    return value;
  }
  return null;
}

function changedInteger(current: string, baseline: string): number | null | "invalid" {
  const next = canonicalInteger(current);
  if (next === "invalid") {
    return "invalid";
  }
  if (next === null || next === canonicalInteger(baseline)) {
    return null;
  }
  return next;
}

function canonicalInteger(value: string): number | null | "invalid" {
  const text = value.trim();
  if (text === "") {
    return null;
  }
  if (!/^(0|[1-9][0-9]*)$/.test(text)) {
    return "invalid";
  }
  const number = Number(text);
  if (!Number.isSafeInteger(number)) {
    return "invalid";
  }
  return number;
}

function reviewLines(questions: DesignPlanRecord["questions"]): string[] {
  const lines: string[] = [];
  for (const item of questions) {
    if (EXECUTION_FIELDS.has(item.field)) {
      continue;
    }
    const line = CLEAR_REVIEW[item.question] ?? item.question;
    if (!lines.includes(line)) {
      lines.push(line);
    }
  }
  return lines;
}
