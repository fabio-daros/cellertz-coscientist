import { useEffect, useRef, useState, type ReactNode } from "react";
import { CoordinatorTracePanel } from "./CoordinatorTracePanel";
import { ProteinDesignModule } from "./protein-design/ProteinDesignModule";
import { SupervisorMark } from "./protein-design/DesignRequest";
import { DesignJourney, type DesignJourneyRecord } from "./DesignJourney";
import { RecordedResultsPanel } from "./RecordedResultsPanel";
import { AttemptInspection } from "./AttemptInspection";
import { linkDesignRequest, listCampaigns, type CampaignRecord } from "./campaign-records";
import { askReadOnly, failedReply } from "./conversation";
import { campaignPreview, CELLERTZ_LOGO_PATH } from "./campaign-preview";
import { WorkflowWorkspace } from "./WorkflowWorkspace";
import { HumanReviewPanel } from "./HumanReviewPanel";
import { FixtureRunPanel } from "./FixtureRunPanel";
import { ScientificRunnerReadiness } from "./ScientificRunnerReadiness";
import { ScientificAuthorizationPanel } from "./ScientificAuthorizationPanel";
import { ScientificRunPanel } from "./ScientificRunPanel";
import type { AuthorizationRecord } from "./scientific-authorization";
import type { ReadinessReply } from "./scientific-runner-readiness";
import {
  createApprovedRun,
  failedRunReply,
  readApprovedRun,
  retryApprovedRun,
  selectRunReply,
  type ApprovedRunReply,
} from "./approved-run";
import {
  formatCost,
  type ConversationMessage,
  type ConversationReply,
  type DesignDraftReply,
  type ReadOnlyFact,
} from "./contracts";
import { failedDesignReply, updateDesignDraft } from "./design-draft";
import { dispatchApprovedDemonstration, failedGateReply, recordHumanDecision, type GateReply } from "./approval";
import { prototypeWorkspace } from "./fixture";
import { GenerationPlanReview } from "./GenerationPlanReview";
import type { CoordinatorReply } from "./attempt-coordinator";
import { ManualAttemptReview } from "./ManualAttemptReview";

const panels = ["evidence", "artifacts", "budget"] as const;
type Panel = (typeof panels)[number];
const areas = ["campaign", "design", "conversation", "session", "results", "manual"] as const;
type Area = (typeof areas)[number];

const panelLabel: Record<Panel, string> = {
  evidence: "Evidence",
  artifacts: "Artifacts",
  budget: "Budget",
};

const areaLabel: Record<Area, string> = {
  campaign: "Campaigns",
  design: "Design request",
  conversation: "Conversation",
  session: "Session",
  results: "Results",
  manual: "Manual review",
};

const isoTimestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

function receiptMatchesDraft(
  approval: { request_id: string; request_revision: number } | null | undefined,
  draft: { request_id: string; revision: number } | null | undefined,
): boolean {
  return (
    approval != null &&
    draft != null &&
    approval.request_id === draft.request_id &&
    approval.request_revision === draft.revision
  );
}

function gateReplyForDraft(
  reply: GateReply | null,
  draft: { request_id: string; revision: number } | null | undefined,
): GateReply | null {
  if (!reply) {
    return null;
  }
  if (!reply.approval) {
    return reply;
  }
  return receiptMatchesDraft(reply.approval, draft) ? reply : null;
}

const SAVED_DESIGN_JOURNEY = "cellertz.saved-design-journey";

function readSavedJourney(): { journeyId?: string; requestId?: string; requestText?: string } | null {
  try {
    const raw = window.localStorage.getItem(SAVED_DESIGN_JOURNEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as { journeyId?: string; requestId?: string; requestText?: string };
    if (!parsed.journeyId || !parsed.requestId || !parsed.requestText) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function readSavedJourneyId(): string | null {
  return readSavedJourney()?.journeyId ?? null;
}

function rememberSavedJourney(record: DesignJourneyRecord) {
  const stage = record.stages.find((item) => item.stage_id === "design_request");
  const requestText = stage?.output?.request_text;
  if (!record.journey_id || !record.request_id || typeof requestText !== "string" || !requestText.trim()) {
    return;
  }
  window.localStorage.setItem(
    SAVED_DESIGN_JOURNEY,
    JSON.stringify({
      journeyId: record.journey_id,
      requestId: record.request_id,
      revision: record.request_revision,
      requestText,
    }),
  );
}

const DESIGN_INTENT_EXAMPLE =
  "Design a 65-residue binder against PDL1, focusing on the configured hotspot.";

function configuredDemonstrationJourneyId(): string | null {
  const value = import.meta.env.VITE_CELLERTZ_DEMONSTRATION_JOURNEY_ID;
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

// The Django admin runs as its own service on its own port, so the frontend
// cannot derive its address: the pilot terminates TLS in nginx, where guessing
// a port would publish a broken link. An unset value means this installation
// does not offer the link, and no built-in address is substituted.
function configuredAdminUrl(): string | null {
  const value = import.meta.env.VITE_CELLERTZ_DJANGO_ADMIN_URL;
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  // The value arrives from the build environment. Only an absolute http(s)
  // address becomes a link, so a `javascript:` URL cannot reach the header.
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
}

function pilotWorkflowNotice(serverExecution: string | null): string {
  const workflow = "This pilot supports the configured PDL1 workflow.";
  if (serverExecution === "disabled") {
    return `${workflow} New computational executions are currently disabled; saved results remain available.`;
  }
  if (serverExecution === "apptainer-demonstration") {
    return `${workflow} The server reports demonstration execution as configured. Saving a request does not start a computation.`;
  }
  return workflow;
}

type SavedRequest = {
  request_id: string;
  request_text: string;
  journey_id: string;
};

function isSavedRequest(value: unknown): value is SavedRequest {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Record<string, unknown>;
  return (
    typeof item.request_id === "string" &&
    typeof item.request_text === "string" &&
    typeof item.journey_id === "string"
  );
}

function DesignRequestPanel({
  intent,
  onIntent,
  onSubmit,
  loading,
  reply,
  submittedRequest,
  savedJourneyId,
  openJourneyId,
  onOpenJourney,
  onOpenSaved,
  onRecorded,
}: {
  intent: string;
  onIntent: (value: string) => void;
  onSubmit: () => void;
  loading: boolean;
  reply: DesignDraftReply | null;
  submittedRequest: string;
  savedJourneyId: string | null;
  openJourneyId: string | null;
  onOpenJourney: (journeyId: string | null) => void;
  onOpenSaved: (item: { journeyId: string; requestText: string }) => void;
  onRecorded: (record: DesignJourneyRecord) => void;
}) {
  const [reopenId, setReopenId] = useState(savedJourneyId ?? "");
  const [editingRequest, setEditingRequest] = useState(false);
  const [reportedExecution, setReportedExecution] = useState<string | null>(null);
  const [journeyLoaded, setJourneyLoaded] = useState(false);
  const [campaigns, setCampaigns] = useState<CampaignRecord[]>([]);
  const [campaignId, setCampaignId] = useState("");
  const [savedRequests, setSavedRequests] = useState<SavedRequest[] | null>(null);
  const [boundRequest, setBoundRequest] = useState<{ id: string; revision: number } | null>(null);
  const [openedText, setOpenedText] = useState("");
  const [listNote, setListNote] = useState("");
  const linkedCampaign = useRef(new Set<string>());
  const demonstrationId = configuredDemonstrationJourneyId() ?? savedJourneyId;
  const restoreId = openJourneyId;
  const requestComplete = reply?.draft?.completeness === "complete";
  const journeyOpen = Boolean((reply?.draft && submittedRequest && !openJourneyId) || (restoreId && journeyLoaded));
  const savedRequest = openedText || submittedRequest || readSavedJourney()?.requestText || "";
  const draftIdentity = `${reply?.draft?.request_id ?? ""}:${reply?.draft?.revision ?? ""}:${submittedRequest}`;
  const seenDraft = useRef(draftIdentity);

  useEffect(() => {
    if (seenDraft.current === draftIdentity) {
      return;
    }
    seenDraft.current = draftIdentity;
    setEditingRequest(false);
    setJourneyLoaded(false);
    setOpenedText("");
    setBoundRequest(null);
    onOpenJourney(null);
  }, [draftIdentity, onOpenJourney]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const listed = await listCampaigns();
        if (!cancelled) {
          setCampaigns(listed);
        }
      } catch {
        if (!cancelled) {
          setCampaigns([]);
        }
      }
      try {
        const response = await fetch("/api/design-journeys");
        const body = (await response.json()) as { requests?: unknown };
        const requests = Array.isArray(body.requests) ? body.requests.filter(isSavedRequest) : [];
        if (!cancelled) {
          setSavedRequests(response.ok ? requests : []);
          setListNote(response.ok ? "" : "Saved requests could not be read.");
        }
      } catch {
        if (!cancelled) {
          setSavedRequests([]);
          setListNote("Saved requests could not be read.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reply?.draft?.request_id, reply?.draft?.revision]);

  useEffect(() => {
    setJourneyLoaded(false);
  }, [openJourneyId]);

  function recorded(record: DesignJourneyRecord) {
    const mode = record.execution_review?.server_execution;
    setReportedExecution(typeof mode === "string" && mode ? mode : null);
    setJourneyLoaded(true);
    if (record.request_id && Number.isInteger(record.request_revision) && record.request_revision >= 1) {
      setBoundRequest({ id: record.request_id, revision: record.request_revision });
    }
    onRecorded(record);
    if (!campaignId || !record.request_id) {
      return;
    }
    const key = `${campaignId}:${record.request_id}`;
    if (linkedCampaign.current.has(key)) {
      return;
    }
    linkedCampaign.current.add(key);
    void linkDesignRequest(campaignId, record.request_id).catch(() => {
      linkedCampaign.current.delete(key);
      setListNote("The campaign link was not saved. The request and journey remain saved.");
    });
  }

  return (
    <section aria-label="Conversational design request">
      <h2>Design request</h2>
      <p>Describe the PDL1 binder you want to design, or open the saved demonstration.</p>
      <p>{pilotWorkflowNotice(reportedExecution)}</p>
      {journeyOpen && !editingRequest ? (
        <section className="saved-request" aria-label="Saved request">
          <h3>Saved request</h3>
          <p>{savedRequest || "The saved request is recorded on this journey."}</p>
          <button
            type="button"
            onClick={() => {
              if (!intent.trim() && savedRequest) {
                onIntent(savedRequest);
              }
              setEditingRequest(true);
            }}
          >
            Edit request
          </button>
        </section>
      ) : (
        <>
          <label htmlFor="design-campaign">
            Campaign (optional)
            <select
              id="design-campaign"
              value={campaignId}
              onChange={(event) => setCampaignId(event.target.value)}
            >
              <option value="">No campaign</option>
              {campaigns.map((item) => (
                <option key={item.campaign_id} value={item.campaign_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <p>Leaving this empty still saves the request. A campaign does not approve or launch.</p>
          <label htmlFor="design-intent">Design intent</label>
          <p id="design-intent-help">Describe the target, desired interaction and relevant constraints.</p>
          <textarea
            id="design-intent"
            value={intent}
            placeholder={DESIGN_INTENT_EXAMPLE}
            aria-describedby="design-intent-help"
            onChange={(event) => onIntent(event.target.value)}
          />
          <button type="button" onClick={onSubmit} disabled={loading}>
            {journeyOpen ? "Save changes" : "Save design request"}
          </button>
        </>
      )}
      <button
        type="button"
        disabled={!demonstrationId}
        aria-describedby={demonstrationId ? undefined : "saved-demonstration-note"}
        onClick={() => {
          if (demonstrationId) {
            onOpenJourney(demonstrationId);
          }
        }}
      >
        Open saved demonstration
      </button>
      {demonstrationId ? null : (
        <p id="saved-demonstration-note">
          No demonstration journey is configured for this installation, and none is recorded in this browser.
        </p>
      )}
      <section aria-label="Saved requests">
        <h3>Saved requests</h3>
        {listNote ? <p role="status">{listNote}</p> : null}
        {savedRequests === null ? <p>Loading saved requests.</p> : null}
        {savedRequests && savedRequests.length === 0 ? <p>No requests are saved on this server.</p> : null}
        {savedRequests && savedRequests.length > 0 ? (
          <ul>
            {savedRequests.map((item) => (
              <li key={item.journey_id}>
                <button
                  type="button"
                  onClick={() => {
                    setOpenedText(item.request_text);
                    onOpenSaved({ journeyId: item.journey_id, requestText: item.request_text });
                  }}
                >
                  {item.request_text || item.request_id}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
      <details className="open-journey">
        <summary>Advanced options</summary>
        <form
          aria-label="Open another journey"
          onSubmit={(event) => {
            event.preventDefault();
            const id = reopenId.trim();
            if (id) {
              onOpenJourney(id);
            }
          }}
        >
          <label htmlFor="saved-journey-id">Journey identifier</label>
          <input
            id="saved-journey-id"
            value={reopenId}
            onChange={(event) => setReopenId(event.target.value)}
          />
          <button type="submit">Open journey</button>
        </form>
      </details>
      <details>
        <summary>Technical details</summary>
        <p>A saved revision stays immutable. The server request id and the expected revision travel together.</p>
        <p>This step does not approve, authorize, or launch.</p>
      </details>
      <CoordinatorTracePanel
        requestId={boundRequest?.id ?? null}
        requestRevision={boundRequest?.revision ?? null}
      />
      {loading ? <p role="status">Loading design draft.</p> : null}
      {reply ? (
        <div role="region" aria-label="Design draft review">
          {reply.status === "revision_conflict" ? (
            <p role="alert">Revision conflict (HTTP 409). The draft was not revised.</p>
          ) : null}
          <p role={reply.status === "adapter_failure" ? "alert" : undefined}>{reply.text}</p>
          <p>Status: {reply.status}.</p>
          {reply.draft ? <p>request_id: {reply.draft.request_id}</p> : null}
          {reply.changed_fields.length > 0 ? <p>Changed: {reply.changed_fields.join(", ")}.</p> : null}
          {reply.draft ? (
            <ul>
              {reply.draft.fields.map((field) => (
                <li key={field.name}>
                  {field.name}: {field.value === null ? "absent" : String(field.value)} (
                  {field.provenance})
                </li>
              ))}
            </ul>
          ) : null}
          {reply.issues.length > 0 ? (
            <ul>
              {reply.issues.map((issue) => (
                <li key={`${issue.field}-${issue.code}`}>
                  {issue.field} {issue.code}
                </li>
              ))}
            </ul>
          ) : null}
          {reply.questions.length > 0 ? (
            <ul>
              {reply.questions.map((item) => (
                <li key={item.field}>
                  {item.field}: {item.question}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
          {reply?.draft && reply.draft.completeness !== "complete" ? (
        <p>The journey opens when the interpreted request is complete. Saving again does not approve or launch.</p>
      ) : null}
          {requestComplete && reply?.draft && submittedRequest && !openJourneyId ? (
        <DesignJourney
          requestId={reply.draft.request_id}
          revision={reply.draft.revision}
          requestText={submittedRequest}
          fields={reply.draft.fields}
          onRecorded={recorded}
        />
      ) : null}
      {restoreId ? (
        <DesignJourney mode="restore" journeyId={restoreId} onRecorded={recorded} />
      ) : null}
    </section>
  );
}

function usageLine(instrumentation: ConversationReply["instrumentation"]): string {
  const origin = instrumentation.origin;
  if (!origin) {
    return "No model usage was recorded.";
  }
  const identity = `Provider ${origin.provider}. Model ${origin.model}.`;
  const event = instrumentation.event;
  if (!event) {
    return `${identity} No token usage was reported.`;
  }
  const input = event.input_tokens;
  const output = event.output_tokens;
  const total = instrumentation.total_tokens;
  if (input !== null && output !== null && total !== null) {
    return `${identity} Total tokens: ${total}.`;
  }
  const shown = (value: number | null) => (value === null ? "not reported" : String(value));
  return (
    `${identity} Input tokens: ${shown(input)}. ` +
    `Output tokens: ${shown(output)}. Total tokens: ${shown(total)}.`
  );
}

function EvidenceGroup({ label, items }: { label: string; items: ReadOnlyFact[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section aria-label={label}>
      <h3>{label}</h3>
      <ul>
        {items.map((item, index) => (
          <li key={`${item.entity_kind}-${item.entity_id}-${index}`}>
            {item.entity_kind} {item.entity_id} ({item.source}
            {item.attributes.status ? `; ${item.attributes.status}` : ""})
          </li>
        ))}
      </ul>
    </section>
  );
}

function HeaderIcon({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  const noteId = `unavailable-${label.toLowerCase()}`;
  return (
    <span className="unavailable-control">
      <button type="button" className={`named-tool ${className ?? ""}`} aria-describedby={noteId} disabled>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          {children}
        </svg>
        <span>{label}</span>
      </button>
      <span id={noteId} className="visually-hidden">Not available in this pilot.</span>
    </span>
  );
}

// Unlike the tools beside it, this one is available: it leaves the prototype
// for the Django admin, which is a separate service and opens in its own tab.
function AdminLink() {
  const url = configuredAdminUrl();
  if (!url) {
    return null;
  }
  return (
    <a className="named-tool admin-link" href={url} target="_blank" rel="noreferrer noopener">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <ellipse cx="12" cy="6" rx="7.5" ry="3" />
        <path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" />
        <path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" />
      </svg>
      <span>Admin</span>
    </a>
  );
}

function BrandMark() {
  const [ready, setReady] = useState(false);
  return (
    <span className="brand">
      <img
        src={CELLERTZ_LOGO_PATH}
        alt={ready ? "Cellertz bio" : ""}
        hidden={!ready}
        onLoad={() => setReady(true)}
        onError={() => setReady(false)}
      />
      {ready ? null : <span className="brand-fallback">Cellertz bio</span>}
    </span>
  );
}

export function App() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [theme]);

  return (
    <div className="page">
      <header className="app-header">
        <div className="brand">
          <BrandMark />
          <h1>AI Co-Scientist platform</h1>
          <span className="badge badge-prototype">Frontend Prototype</span>
        </div>
        <div className="header-right">
          <p className="agent-legend">
            <SupervisorMark />
            AI Agent - Supervisor
          </p>
          <div className="header-tools">
            <button
              type="button"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-pressed={theme === "dark"}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                {theme === "dark" ? (
                  <>
                    <circle cx="12" cy="12" r="4" />
                    <path d="M12 2.5v2.2M12 19.3V21.5M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2.5 12h2.2M19.3 12H21.5M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6" />
                  </>
                ) : (
                  <path d="M21 14.5A8.5 8.5 0 1 1 9.5 3 7 7 0 0 0 21 14.5z" />
                )}
              </svg>
            </button>
            <HeaderIcon label="Language">
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3c2.5 2.8 3.8 5.8 3.8 9S14.5 18.2 12 21c-2.5-2.8-3.8-5.8-3.8-9S9.5 5.8 12 3z" />
            </HeaderIcon>
            <AdminLink />
            <HeaderIcon label="Account" className="avatar">
              <circle cx="12" cy="9" r="3.2" />
              <path d="M5.5 19.2c1.2-2.8 3.4-4.2 6.5-4.2s5.3 1.4 6.5 4.2" />
            </HeaderIcon>
          </div>
        </div>
        <nav className="project-nav" aria-label="Project modules">
          <button type="button" aria-current="page">
            Protein Design
          </button>
          <button type="button" disabled>
            Target Discovery
            <span className="project-nav-state">Not available</span>
          </button>
          <button type="button" disabled>
            mRNA Design
            <span className="project-nav-state">Not available</span>
          </button>
        </nav>
      </header>
      <main>
        <ProteinDesignModule />
      </main>
      <p className="disclaimer">© {new Date().getFullYear()} Cellertz Bio</p>
    </div>
  );
}

function initialArea(): Area {
  const value = new URLSearchParams(window.location.search).get("area");
  return areas.find((area) => area === value) ?? "campaign";
}

export function LegacyWorkspace({ legacyConsole = false }: { legacyConsole?: boolean } = {}) {
  const workspace = prototypeWorkspace;
  const [area, setArea] = useState<Area>(initialArea);
  useEffect(() => {
    const sync = () => setArea(initialArea());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);
  const [resultsView, setResultsView] = useState(0);
  const [coordinatorReply, setCoordinatorReply] = useState<CoordinatorReply | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [runId, setRunId] = useState(workspace.runs[0]?.run_id ?? "");
  const [candidateId, setCandidateId] = useState(workspace.candidates[0]?.candidate_id ?? "");
  const [panel, setPanel] = useState<Panel>("evidence");
  const [draftGoal, setDraftGoal] = useState(workspace.draft.design_goal);
  const [draftText, setDraftText] = useState("");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [reply, setReply] = useState<ConversationReply | null>(null);
  const [intent, setIntent] = useState("");
  const [submittedRequest, setSubmittedRequest] = useState("");
  const [savedJourneyId, setSavedJourneyId] = useState<string | null>(readSavedJourneyId);
  const [openJourneyId, setOpenJourneyId] = useState<string | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [designReply, setDesignReply] = useState<DesignDraftReply | null>(null);
  const [reviewerId, setReviewerId] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [decisionLoading, setDecisionLoading] = useState(false);
  const [decisionReply, setDecisionReply] = useState<GateReply | null>(null);
  const [dispatchLoading, setDispatchLoading] = useState(false);
  const [executionReply, setExecutionReply] = useState<GateReply | null>(null);
  const [runReply, setRunReply] = useState<ApprovedRunReply | null>(null);
  const [readinessReply, setReadinessReply] = useState<ReadinessReply | null>(null);
  const [currentAuthorization, setCurrentAuthorization] = useState<AuthorizationRecord | null>(null);
  const [consumedAuthorization, setConsumedAuthorization] = useState<AuthorizationRecord | null>(null);
  const [inspectedReply, setInspectedReply] = useState<ApprovedRunReply | null>(null);
  const inspectedGeneration = useRef(0);
  const inspectedRunId = useRef<string | null>(null);
  const [pollTick, setPollTick] = useState(0);
  const [runLoading, setRunLoading] = useState(false);
  const [refreshLoading, setRefreshLoading] = useState(false);
  const [retryLoading, setRetryLoading] = useState(false);
  const shownDraft = useRef<{ request_id: string; revision: number } | null>(null);
  const deciding = useRef(false);
  const dispatching = useRef(false);
  const runBusy = useRef(false);
  const runGeneration = useRef(0);
  const [messages, setMessages] = useState<ConversationMessage[]>(workspace.messages);
  const [campaignId, setCampaignId] = useState(campaignPreview.campaigns[0]?.campaign_id ?? "");
  const selectedCampaign = campaignPreview.campaigns.find((item) => item.campaign_id === campaignId);

  const selected = workspace.candidates.find((item) => item.candidate_id === candidateId);
  const selectedRun = workspace.runs.find((item) => item.run_id === runId);
  const visibleEvidence = workspace.evidence.filter((item) => item.candidate_id === candidateId);
  const visibleArtifacts = workspace.artifacts.filter((item) => item.candidate_id === candidateId);

  useEffect(() => {
    const run = runReply?.run;
    if (!run || (run.status !== "queued" && run.status !== "running")) {
      return;
    }
    const timer = window.setTimeout(() => {
      void refreshFixtureRun();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [
    runReply?.run?.run_id,
    runReply?.run?.status,
    runReply?.run?.attempt,
    runReply?.run?.updated_at,
    runLoading,
    refreshLoading,
    retryLoading,
    pollTick,
  ]);

  function addLocalDraft() {
    const content = draftText.trim();
    if (!content) {
      return;
    }
    const createdAt = new Date().toISOString();
    if (!isoTimestamp.test(createdAt)) {
      return;
    }
    setMessages((current) => [
      ...current,
      {
        message_id: `local_${current.length + 1}`,
        role: "scientist",
        created_at: createdAt,
        content,
      },
    ]);
    setDraftText("");
  }

  async function askRecordedQuestion() {
    const content = question.trim();
    if (!content || loading) {
      return;
    }
    setLoading(true);
    setReply(null);
    try {
      setReply(
        await askReadOnly({
          session_id: workspace.session_id,
          question: content,
          run_id: runId || null,
          candidate_id: candidateId || null,
        }),
      );
    } catch {
      setReply(failedReply(workspace.session_id, runId || null));
    } finally {
      setLoading(false);
    }
  }

  async function submitDesignIntent() {
    const content = intent.trim();
    if (!content || draftLoading) {
      return;
    }
    setDraftLoading(true);
    try {
      const next = await updateDesignDraft({
        session_id: workspace.session_id,
        message: content,
        request_id: shownDraft.current?.request_id ?? null,
        revision: shownDraft.current?.revision ?? null,
      });
      rememberDraft(next);
      if (next.draft && next.status !== "revision_conflict") {
        setSubmittedRequest(content);
      }
    } catch {
      rememberDraft(failedDesignReply(workspace.session_id));
    } finally {
      setDraftLoading(false);
    }
  }

  function rememberJourney(record: DesignJourneyRecord) {
    rememberSavedJourney(record);
    if (record.journey_id) {
      setSavedJourneyId(record.journey_id);
    }
  }

  function openSavedRequest(item: { journeyId: string; requestText: string }) {
    setIntent(item.requestText);
    setOpenJourneyId(item.journeyId);
  }

  function rememberDraft(next: DesignDraftReply) {
    const incoming = next.draft;
    const shown = shownDraft.current;
    if (
      incoming &&
      shown &&
      incoming.request_id === shown.request_id &&
      incoming.revision < shown.revision
    ) {
      return;
    }
    shownDraft.current = incoming
      ? { request_id: incoming.request_id, revision: incoming.revision }
      : null;
    setDesignReply(next);
    setDecisionReply((current) => (receiptMatchesDraft(current?.approval, incoming) ? current : null));
    setExecutionReply((current) => (receiptMatchesDraft(current?.approval, incoming) ? current : null));
    setRunReply((current) => {
      const run = current?.run;
      if (!incoming || !run) {
        return null;
      }
      if (run.request_id === incoming.request_id && run.request_revision === incoming.revision) {
        return current;
      }
      return null;
    });
  }

  function draftStillCurrent(requestId: string, revision: number) {
    return (
      shownDraft.current?.request_id === requestId && shownDraft.current.revision === revision
    );
  }

  async function recordDecision(decision: "approved" | "rejected" | "changes_requested" | "held") {
    const draft = shownDraft.current;
    if (!draft || deciding.current) {
      return;
    }
    const requestId = draft.request_id;
    const revision = draft.revision;
    deciding.current = true;
    setDecisionLoading(true);
    try {
      const reply = await recordHumanDecision({
        session_id: workspace.session_id,
        request_id: requestId,
        revision,
        decision,
        reviewer_id: reviewerId,
        reason: reviewReason,
        idempotency_key: `decision-${requestId}-${revision}-${decision}`,
      });
      if (!draftStillCurrent(requestId, revision)) {
        return;
      }
      if (reply.approval && !receiptMatchesDraft(reply.approval, shownDraft.current)) {
        return;
      }
      setDecisionReply(reply);
      setExecutionReply(null);
    } catch {
      if (draftStillCurrent(requestId, revision)) {
        setDecisionReply(failedGateReply(workspace.session_id));
      }
    } finally {
      deciding.current = false;
      setDecisionLoading(false);
    }
  }

  async function runDemonstration() {
    const draft = shownDraft.current;
    const approval = decisionReply?.approval;
    if (
      !draft ||
      !approval ||
      approval.decision !== "approved" ||
      !receiptMatchesDraft(approval, draft) ||
      dispatching.current
    ) {
      return;
    }
    const requestId = approval.request_id;
    const revision = approval.request_revision;
    dispatching.current = true;
    setDispatchLoading(true);
    try {
      const reply = await dispatchApprovedDemonstration({
        session_id: workspace.session_id,
        approval_id: approval.approval_id,
        idempotency_key: `dispatch-${approval.approval_id}`,
      });
      if (!draftStillCurrent(requestId, revision)) {
        return;
      }
      if (!receiptMatchesDraft(reply.approval, shownDraft.current)) {
        return;
      }
      setExecutionReply(reply);
    } catch {
      if (draftStillCurrent(requestId, revision)) {
        setExecutionReply(failedGateReply(workspace.session_id));
      }
    } finally {
      dispatching.current = false;
      setDispatchLoading(false);
    }
  }

  function beginRunOperation(): number | null {
    if (runBusy.current) {
      return null;
    }
    runBusy.current = true;
    runGeneration.current += 1;
    return runGeneration.current;
  }

  function finishRunOperation(generation: number) {
    if (runGeneration.current === generation) {
      runBusy.current = false;
    }
  }

  function applyRunReply(
    generation: number,
    requestId: string,
    revision: number,
    reply: ApprovedRunReply,
    matches: (reply: ApprovedRunReply) => boolean,
  ) {
    if (runGeneration.current !== generation || !draftStillCurrent(requestId, revision) || !matches(reply)) {
      return;
    }
    setRunReply((current) => selectRunReply(current, reply, generation, runGeneration.current) ?? current);
  }

  async function createFixtureRun() {
    const draft = shownDraft.current;
    const approval = decisionReply?.approval;
    const demonstration = executionReply?.status === "demonstration_completed" ? executionReply.demonstration : null;
    if (
      !draft ||
      !approval ||
      !demonstration ||
      approval.decision !== "approved" ||
      !receiptMatchesDraft(approval, draft) ||
      demonstration.approval_id !== approval.approval_id ||
      runReply?.run
    ) {
      return;
    }
    const generation = beginRunOperation();
    if (generation === null) {
      return;
    }
    const requestId = draft.request_id;
    const revision = draft.revision;
    const approvalId = approval.approval_id;
    setRunLoading(true);
    try {
      const reply = await createApprovedRun({
        session_id: workspace.session_id,
        approval_id: approvalId,
        idempotency_key: `run-${approvalId}`,
      });
      applyRunReply(
        generation,
        requestId,
        revision,
        reply,
        (incoming) =>
          !incoming.run ||
          (incoming.run.approval_id === approvalId &&
            incoming.run.request_id === requestId &&
            incoming.run.request_revision === revision),
      );
    } catch {
      if (draftStillCurrent(requestId, revision) && runGeneration.current === generation) {
        setRunReply((current) => (current?.run ? current : failedRunReply(workspace.session_id)));
      }
    } finally {
      finishRunOperation(generation);
      setRunLoading(false);
    }
  }

  function closeHistoricalRun() {
    inspectedGeneration.current += 1;
    inspectedRunId.current = null;
    setInspectedReply(null);
  }

  async function openHistoricalRun(summary: { run_id: string; request_id: string; revision: number }) {
    const generation = inspectedGeneration.current + 1;
    inspectedGeneration.current = generation;
    inspectedRunId.current = summary.run_id;
    try {
      const reply = await readApprovedRun(workspace.session_id, summary.run_id);
      if (inspectedGeneration.current !== generation || inspectedRunId.current !== summary.run_id) {
        return;
      }
      const historical = reply.run;
      if (
        !historical ||
        historical.run_id !== summary.run_id ||
        historical.request_id !== summary.request_id ||
        historical.request_revision !== summary.revision
      ) {
        setInspectedReply(reply.run ? failedRunReply(workspace.session_id) : reply);
        return;
      }
      setInspectedReply(reply);
    } catch {
      if (inspectedGeneration.current === generation && inspectedRunId.current === summary.run_id) {
        setInspectedReply(failedRunReply(workspace.session_id));
      }
    }
  }

  async function refreshFixtureRun() {
    const draft = shownDraft.current;
    const run = runReply?.run;
    if (!draft || !run || run.request_revision !== draft.revision) {
      return;
    }
    const generation = beginRunOperation();
    if (generation === null) {
      return;
    }
    const requestId = run.request_id;
    const revision = run.request_revision;
    const runId = run.run_id;
    setRefreshLoading(true);
    try {
      const reply = await readApprovedRun(workspace.session_id, runId);
      applyRunReply(
        generation,
        requestId,
        revision,
        reply,
        (incoming) =>
          incoming.run !== null && incoming.run.run_id === runId && incoming.run.request_revision === revision,
      );
    } catch {
      if (draftStillCurrent(requestId, revision) && runGeneration.current === generation) {
        setRunReply((current) => (current?.run ? current : failedRunReply(workspace.session_id)));
        setPollTick((tick) => tick + 1);
      }
    } finally {
      finishRunOperation(generation);
      setRefreshLoading(false);
    }
  }

  async function retryFixtureRun() {
    const draft = shownDraft.current;
    const run = runReply?.run;
    if (!draft || !run || run.status !== "failed") {
      return;
    }
    const generation = beginRunOperation();
    if (generation === null) {
      return;
    }
    const requestId = run.request_id;
    const revision = run.request_revision;
    const runId = run.run_id;
    const attempt = run.attempt;
    setRetryLoading(true);
    try {
      const reply = await retryApprovedRun({
        session_id: workspace.session_id,
        run_id: runId,
        idempotency_key: `retry-${runId}-${attempt}`,
      });
      applyRunReply(
        generation,
        requestId,
        revision,
        reply,
        (incoming) =>
          incoming.run !== null && incoming.run.run_id === runId && incoming.run.request_revision === revision,
      );
    } catch {
      if (draftStillCurrent(requestId, revision) && runGeneration.current === generation) {
        setRunReply((current) => (current?.run ? current : failedRunReply(workspace.session_id)));
      }
    } finally {
      finishRunOperation(generation);
      setRetryLoading(false);
    }
  }

  function movePanel(key: string) {
    const index = panels.indexOf(panel);
    const next =
      key === "ArrowRight"
        ? panels[(index + 1) % panels.length]
        : key === "ArrowLeft"
          ? panels[(index - 1 + panels.length) % panels.length]
          : null;
    if (next === null) {
      return;
    }
    setPanel(next);
    queueMicrotask(() => document.getElementById(`tab-${next}`)?.focus());
  }

  function moveArea(key: string) {
    const index = areas.indexOf(area);
    const next =
      key === "ArrowRight"
        ? areas[(index + 1) % areas.length]
        : key === "ArrowLeft"
          ? areas[(index - 1 + areas.length) % areas.length]
          : null;
    if (next === null) {
      return;
    }
    setArea(next);
    queueMicrotask(() => document.getElementById(`area-${next}`)?.focus());
  }

  function openResults() {
    if (!new URLSearchParams(window.location.search).get("attempt")) {
      window.history.replaceState({}, "", "/?area=results");
    }
    setResultsView((current) => current + 1);
    setArea("results");
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [theme]);

  const visibleDecision = gateReplyForDraft(decisionReply, designReply?.draft);
  const visibleExecution = gateReplyForDraft(executionReply, designReply?.draft);
  const visibleRun =
    runReply?.run &&
    designReply?.draft &&
    runReply.run.request_id === designReply.draft.request_id &&
    runReply.run.request_revision === designReply.draft.revision
      ? runReply
      : runReply?.run
        ? null
        : runReply;

  return (
    <div className="page">
      <header className="app-header">
        <div className="brand">
          <BrandMark />
          <h1>AI Co-Scientist platform</h1>
          <span className="badge badge-prototype">Frontend Prototype</span>
        </div>
        <div className="header-right">
          <span className="badge">PDL1 demonstration</span>
          <div className="header-tools">
            <button
              type="button"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-pressed={theme === "dark"}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                {theme === "dark" ? (
                  <>
                    <circle cx="12" cy="12" r="4" />
                    <path d="M12 2.5v2.2M12 19.3V21.5M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2.5 12h2.2M19.3 12H21.5M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6" />
                  </>
                ) : (
                  <path d="M21 14.5A8.5 8.5 0 1 1 9.5 3 7 7 0 0 0 21 14.5z" />
                )}
              </svg>
            </button>
            <HeaderIcon label="Language">
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3c2.5 2.8 3.8 5.8 3.8 9S14.5 18.2 12 21c-2.5-2.8-3.8-5.8-3.8-9S9.5 5.8 12 3z" />
            </HeaderIcon>
            <AdminLink />
            <HeaderIcon label="Account" className="avatar">
              <circle cx="12" cy="9" r="3.2" />
              <path d="M5.5 19.2c1.2-2.8 3.4-4.2 6.5-4.2s5.3 1.4 6.5 4.2" />
            </HeaderIcon>
          </div>
        </div>
        <div
          className="area-nav"
          role="tablist"
          aria-label="Workspace areas"
          onKeyDown={(event) => {
            if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
              event.preventDefault();
              moveArea(event.key);
            }
          }}
        >
        {areas.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`area-${id}`}
            aria-selected={area === id}
            aria-controls={`area-panel-${id}`}
            tabIndex={area === id ? 0 : -1}
            onClick={() => {
              if (id === "results" && !new URLSearchParams(window.location.search).get("attempt")) {
                window.history.replaceState({}, "", "/?area=results");
              }
              if (id === "results") {
                setResultsView((current) => current + 1);
              }
              setArea(id);
            }}
          >
            {areaLabel[id]}
          </button>
        ))}
        </div>
      </header>
      <main id={`area-panel-${area}`}>
        {area === "campaign" ? (
            <>
            <WorkflowWorkspace
              campaignId={campaignId}
              onCampaignChange={setCampaignId}
              demonstrationId={configuredDemonstrationJourneyId() ?? readSavedJourneyId()}
              onOpenDemonstration={() => {
                const id = configuredDemonstrationJourneyId() ?? readSavedJourneyId();
                if (!id) {
                  return;
                }
                setOpenJourneyId(id);
                setArea("design");
              }}
              runs={workspace.runs}
              runnerReadiness={
                <ScientificRunnerReadiness onObserved={setReadinessReply} />
              }
            />
            {legacyConsole ? (
              <>
                  <HumanReviewPanel
                    sessionId={workspace.session_id}
                    draftReply={designReply}
                    budget={workspace.budget}
                    reviewerId={reviewerId}
                    reason={reviewReason}
                    onReviewerId={setReviewerId}
                    onReason={setReviewReason}
                    onDecide={recordDecision}
                    decisionLoading={decisionLoading}
                    decisionReply={visibleDecision}
                    executionReply={visibleExecution}
                    onDispatch={runDemonstration}
                    dispatchLoading={dispatchLoading}
                  />
                  <ScientificAuthorizationPanel
                    draftReply={designReply}
                    decisionReply={visibleDecision}
                    executionReply={visibleExecution}
                    runReply={visibleRun}
                    readiness={readinessReply}
                    budget={workspace.budget}
                    onCurrent={setCurrentAuthorization}
                    consumed={consumedAuthorization}
                  />
                  <ScientificRunPanel
                    authorization={currentAuthorization}
                    draftReply={designReply}
                    decisionReply={visibleDecision}
                    executionReply={visibleExecution}
                    runReply={visibleRun}
                    readiness={readinessReply}
                    budget={workspace.budget}
                    onConsumed={setConsumedAuthorization}
                  />
                  <FixtureRunPanel
                    draft={designReply?.draft ?? null}
                    execution={visibleExecution}
                    runReply={visibleRun}
                    busy={runLoading || refreshLoading || retryLoading}
                    onCreate={createFixtureRun}
                    onRefresh={refreshFixtureRun}
                    onRetry={retryFixtureRun}
                    onOpenRun={(summary) => void openHistoricalRun(summary)}
                    inspectedReply={inspectedReply}
                    onCloseInspection={closeHistoricalRun}
                  />
              </>
            ) : null}
            </>
          ) : null}
        {area === "design" ? (
          <DesignRequestPanel
            intent={intent}
            onIntent={setIntent}
            onSubmit={submitDesignIntent}
            loading={draftLoading}
            reply={designReply}
            submittedRequest={submittedRequest}
            savedJourneyId={savedJourneyId}
            openJourneyId={openJourneyId}
            onOpenJourney={setOpenJourneyId}
            onOpenSaved={openSavedRequest}
            onRecorded={rememberJourney}
          />
        ) : null}
        {area === "conversation" ? (
          <section aria-label="Conversation">
            <h2>Conversation</h2>
            <p>Recorded questions stay available. This chat does not approve or launch the PDL1 demonstration.</p>
            <RecordedResultsPanel onReply={setCoordinatorReply} />
            <section aria-label="Legacy read-only conversation">
            <h3>Legacy read-only conversation</h3>
            <p>
              Selected operational reference: {selectedCampaign?.target_id ?? "none"} (
              {selectedCampaign?.campaign_id ?? "none"}). This reference is for presentation only.
            </p>
            <p>
              This chat consults session {workspace.session_id}. The run and candidate sent with a
              question belong to the PDL1 fixture ({runId || "none"}, {candidateId || "none"}).
            </p>
            <p>The selected VHH campaign is not sent and is not the corpus of the answer.</p>
            <p>
              This assistant does not execute tools, approve campaigns, change ranking, or turn
              observations into validated knowledge.
            </p>
            <p>Questions consult available records. This chat does not execute a campaign.</p>
            <p>Read-only conversational access. This cannot execute, approve, or promote.</p>
            <p>
              This conversation phrases supplied records. It is not five specialists and it does not
              start scientific execution. A question may be written in another language. The reply
              is in English.
            </p>
            <ol>
              {messages.map((message) => (
                <li key={message.message_id}>
                  <span>{message.role}</span> <time dateTime={message.created_at}>{message.created_at}</time>{" "}
                  {message.content}
                </li>
              ))}
            </ol>
            <label htmlFor="local-draft">Local draft message</label>
            <textarea
              id="local-draft"
              value={draftText}
              onChange={(event) => setDraftText(event.target.value)}
            />
            <button type="button" onClick={addLocalDraft}>
              Add to local draft
            </button>
            <p>Local draft only. Not sent and not executed.</p>
            <label htmlFor="read-only-question">Read-only question</label>
            <textarea
              id="read-only-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <button type="button" onClick={askRecordedQuestion} disabled={loading}>
              Ask read-only
            </button>
            {loading ? <p role="status">Loading read-only answer.</p> : null}
            {reply?.status === "answered" ? (
              <div role="region" aria-label="Read-only answer">
                <p>Mode: read-only.</p>
                <p>
                  {reply.instrumentation.origin
                    ? "Model explanation."
                    : "Recorded-facts phrasing."}
                </p>
                <p>{reply.text}</p>
                <EvidenceGroup
                  label="Retrieved evidence"
                  items={reply.evidence.filter((item) =>
                    item.source === "second_brain" ||
                    item.source === "design_request" ||
                    item.source === "session" ||
                    item.source === "episodic"
                  )}
                />
                <EvidenceGroup
                  label="Fixture output"
                  items={reply.evidence.filter(
                    (item) => item.source === "fixture" || item.source === "workflow_trace",
                  )}
                />
                <p>{usageLine(reply.instrumentation)}</p>
              </div>
            ) : null}
            {reply && reply.status !== "answered" ? (
              <p role="alert">
                {reply.status === "provider_budget"
                  ? "The language provider reached its credit or completion-token budget. No scientific action was taken."
                  : reply.text}
              </p>
            ) : null}
            </section>
            <GenerationPlanReview incomingReply={coordinatorReply} />
          </section>
        ) : null}
        {area === "session" ? (
          <div className="layout">
            <section aria-label="Session trace">
              <h2>Session</h2>
              <p>This is a local fixture reading. It is not the PDL1 execution path and it does not launch generation.</p>
              <p>Workspace session: {workspace.session_id}.</p>
              <p>
                Reference configuration shown in Campaign: {selectedCampaign?.campaign_id ?? "none"} (
                {selectedCampaign?.target_id ?? "none"}). This local reference is not linked to the
                design request or to recorded results.
              </p>
              <p>
                Draft revision:{" "}
                {designReply?.draft
                  ? `${designReply.draft.request_id}, revision ${designReply.draft.revision}, ${designReply.draft.completeness}`
                  : "No design request revision is recorded."}
              </p>
              <p>
                Human decision:{" "}
                {visibleDecision?.approval
                  ? `${visibleDecision.approval.decision} for ${visibleDecision.approval.request_id} revision ${visibleDecision.approval.request_revision}`
                  : "none"}
                .
              </p>
              <p>
                Approval receipt:{" "}
                {visibleDecision?.approval
                  ? `${visibleDecision.approval.approval_id} for ${visibleDecision.approval.request_id} revision ${visibleDecision.approval.request_revision}`
                  : "none"}
                .
              </p>
              <p>
                Authorization result: {visibleExecution?.authorization_status ?? visibleDecision?.authorization_status ?? "not run"}.
              </p>
              <p>Preflight result: {visibleExecution?.preflight_status ?? "not run"}.</p>
              <p>
                Demonstration dispatch receipt:{" "}
                {visibleExecution?.demonstration
                  ? `${visibleExecution.demonstration.dispatch_id} for ${visibleExecution.approval?.request_id ?? "unknown"} revision ${visibleExecution.approval?.request_revision ?? "unknown"}`
                  : "none"}
                .
              </p>
              <p>
                Fixture run:{" "}
                {visibleRun?.run
                  ? `${visibleRun.run.run_id}, attempt ${visibleRun.run.attempt}, ${visibleRun.run.status}, for ${visibleRun.run.request_id} revision ${visibleRun.run.request_revision}. Not scientific validation.`
                  : "none"}
                .
              </p>
              <p>
                Sources loaded: VHH operational reference projection and the Phase 6A session
                fixture.
              </p>
              <p>Local actions stay in this browser. They do not create a scientific event.</p>
              <p>
                Observed scientific events in this session fixture:{" "}
                {workspace.runs.map((run) => run.run_id).join(", ") || "none"}. Those events belong
                to the session fixture, not to the selected VHH campaign.
              </p>
              <p>
                A missing generator version in the VHH reference is not a task for the PDL1
                demonstration.
              </p>
            </section>
            <nav aria-label="Recorded runs">
              <h2>Runs</h2>
              <ul>
                {workspace.runs.map((run) => (
                  <li key={run.run_id}>
                    <button
                      type="button"
                      aria-pressed={run.run_id === runId}
                      onClick={() => setRunId(run.run_id)}
                    >
                      {run.run_id}
                    </button>
                  </li>
                ))}
              </ul>
              <p>
                Target {workspace.project.target_id}. Memory house{" "}
                {selectedRun?.memory_house ?? "episodic"}. Status {selectedRun?.epistemic_status}.
              </p>
            </nav>
            <div>
              <section aria-label="Design context">
                <p>Target: {workspace.project.target_id}</p>
                <p>Design goal: {workspace.project.design_goal}</p>
              </section>
              <section aria-label="Protein design draft">
                <h2>ProteinDesignDraft</h2>
                <p>Status: {workspace.draft.status}. Executable: no.</p>
                <label htmlFor="draft-goal">Draft preview</label>
                <input
                  id="draft-goal"
                  value={draftGoal}
                  onChange={(event) => setDraftGoal(event.target.value)}
                />
                <p>Preview: {draftGoal}</p>
              </section>
              <section aria-label="Pipeline">
                <h2>Pipeline</h2>
                <ol className="timeline">
                  {workspace.pipeline.map((stage) => (
                    <li key={stage.stage_id}>
                      <strong>{stage.label}</strong> {stage.display_status}. {stage.detail}
                    </li>
                  ))}
                </ol>
              </section>
            </div>
            <aside>
              <section aria-label="Agents">
                <h2>Agents</h2>
                <ul>
                  {workspace.agents.map((agent) => (
                    <li key={agent.agent_id}>
                      {agent.agent_id}: {agent.role} ({agent.display_status})
                    </li>
                  ))}
                </ul>
              </section>
              <section aria-label="Candidates">
                <h2>Candidates</h2>
                <ul>
                  {workspace.candidates.map((candidate) => (
                    <li key={candidate.candidate_id}>
                      <button type="button" onClick={() => setCandidateId(candidate.candidate_id)}>
                        {candidate.candidate_id}
                      </button>
                    </li>
                  ))}
                </ul>
                <div role="region" aria-label="Candidate detail">
                  <h3>{selected?.candidate_id}</h3>
                  <p>Kind: {selected?.candidate_kind}</p>
                  <p>Epistemic status: {selected?.epistemic_status}</p>
                  <p>{selected?.score_label}</p>
                  <p>
                    Score:{" "}
                    {selected?.ranking_score === null ? "not republished" : selected?.ranking_score}
                  </p>
                  <p>{selected?.provenance}</p>
                </div>
              </section>
              <div
                className="tabs"
                role="tablist"
                aria-label="Inspection panels"
                onKeyDown={(event) => {
                  if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                    event.preventDefault();
                    movePanel(event.key);
                  }
                }}
              >
                {panels.map((id) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    id={`tab-${id}`}
                    aria-selected={panel === id}
                    aria-controls={`panel-${id}`}
                    tabIndex={panel === id ? 0 : -1}
                    onClick={() => setPanel(id)}
                  >
                    {panelLabel[id]}
                  </button>
                ))}
              </div>
              {panel === "evidence" ? (
                <section role="tabpanel" id="panel-evidence" aria-labelledby="tab-evidence">
                  {visibleEvidence.length === 0 ? (
                    <p>No observed evidence for this candidate.</p>
                  ) : (
                    <ul>
                      {visibleEvidence.map((item) => (
                        <li key={item.evidence_id}>
                          {item.kind}: {item.summary} ({item.epistemic_status})
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              ) : null}
              {panel === "artifacts" ? (
                <section role="tabpanel" id="panel-artifacts" aria-labelledby="tab-artifacts">
                  {visibleArtifacts.length === 0 ? (
                    <p>No artifacts for this candidate.</p>
                  ) : (
                    <ul>
                      {visibleArtifacts.map((item) => (
                        <li key={item.artifact_id}>
                          {item.kind} {item.uri} {item.media_type}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              ) : null}
              {panel === "budget" ? (
                <section role="tabpanel" id="panel-budget" aria-labelledby="tab-budget">
                  <p>Model usage events: {workspace.usage.length}</p>
                  {workspace.usage.length === 0 ? <p>No model call was recorded.</p> : null}
                  <p>
                    Model cost:{" "}
                    {formatCost(
                      workspace.usage_snapshot.estimated_model_cost,
                      workspace.usage_snapshot.currency,
                    )}
                  </p>
                  <p>
                    Scientific infrastructure cost:{" "}
                    {formatCost(
                      workspace.usage_snapshot.scientific_infrastructure_cost,
                      workspace.usage_snapshot.currency,
                    )}
                  </p>
                  <p>Budget state does not authorize execution.</p>
                </section>
              ) : null}
            </aside>
          </div>
        ) : null}
        {area === "results" ? <AttemptInspection key={resultsView} /> : null}
        {area === "manual" ? <ManualAttemptReview /> : null}
      </main>
      <p className="disclaimer">© {new Date().getFullYear()} Cellertz Bio</p>
    </div>
  );
}
