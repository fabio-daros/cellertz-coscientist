/**
 * Closed badge vocabulary for the presentation shell.
 *
 * Each badge carries one epistemic qualification that the prose used to
 * repeat. The note is the long form, shown on hover and to screen readers.
 * Adding a state means adding it here, not writing another paragraph.
 */

export const EPISTEMIC_STATES = [
  "real-execution",
  "fixture",
  "observed",
  "pending-evaluation",
  "not-authorized",
  "not-validated",
  "planned",
  "blocked",
] as const;

export type EpistemicState = (typeof EPISTEMIC_STATES)[number];

type BadgeSpec = {
  label: string;
  note: string;
};

export const EPISTEMIC_BADGES: Record<EpistemicState, BadgeSpec> = {
  "real-execution": {
    label: "Real execution",
    note: "A real scientific tool ran on a host. Execution is not a validated scientific claim.",
  },
  fixture: {
    label: "Fixture",
    note: "Recorded sample data. No scientific tool ran for this value.",
  },
  observed: {
    label: "Observed",
    note: "Recorded as it happened. Not promoted to validated knowledge.",
  },
  "pending-evaluation": {
    label: "Evaluation pending",
    note: "Outputs exist and no scientific evaluation has been recorded for them yet.",
  },
  "not-authorized": {
    label: "Not authorized",
    note: "No explicit permission exists for this run. Readiness is not authorization.",
  },
  "not-validated": {
    label: "Not validated",
    note: "Not a scientific claim and not experimental proof of binding.",
  },
  planned: {
    label: "Planned",
    note: "Planned capability. This prototype does not open a working module.",
  },
  blocked: {
    label: "Blocked",
    note: "Required information or infrastructure is missing, so this cannot proceed.",
  },
};

export function Badge({ state }: { state: EpistemicState }) {
  const spec = EPISTEMIC_BADGES[state];
  return (
    <span className={`chip chip-${state}`} title={spec.note}>
      {spec.label}
      <span className="chip-note">{spec.note}</span>
    </span>
  );
}

export function BadgeRow({ states }: { states: readonly EpistemicState[] }) {
  if (states.length === 0) {
    return null;
  }
  return (
    <span className="chip-row">
      {states.map((state) => (
        <Badge key={state} state={state} />
      ))}
    </span>
  );
}
