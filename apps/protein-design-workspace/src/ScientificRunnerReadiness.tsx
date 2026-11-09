import { useRef, useState } from "react";
import {
  readScientificRunnerReadiness,
  selectReadiness,
  type ReadinessReply,
  type ReadinessStatus,
} from "./scientific-runner-readiness";

const STATUS_LABEL: Record<ReadinessStatus, string> = {
  disabled: "Disabled",
  blocked: "Blocked",
  ready: "Ready",
  configuration_failure: "Configuration failure",
  adapter_failure: "Adapter failure",
};

function shown(value: string | null): string {
  return value ?? "Not recorded";
}

export function ScientificRunnerReadiness({
  onObserved,
}: {
  onObserved?: (reply: ReadinessReply) => void;
} = {}) {
  const [reply, setReply] = useState<ReadinessReply | null>(null);
  const [phase, setPhase] = useState<"idle" | "loading" | "error">("idle");
  const [errorText, setErrorText] = useState<string | null>(null);
  const generation = useRef(0);
  const visibleReply = useRef<ReadinessReply | null>(null);

  async function refresh() {
    const operation = generation.current + 1;
    generation.current = operation;
    setPhase("loading");
    try {
      const incoming = await readScientificRunnerReadiness();
      if (generation.current !== operation) {
        return;
      }
      const visible = selectReadiness(
        visibleReply.current,
        incoming,
        operation,
        generation.current,
      );
      visibleReply.current = visible;
      setReply(visible);
      if (visible) {
        onObserved?.(visible);
      }
      if (
        visible?.status === "configuration_failure" ||
        visible?.status === "adapter_failure"
      ) {
        setPhase("error");
        setErrorText(visible.text);
        return;
      }
      setPhase("idle");
      setErrorText(null);
    } catch {
      if (generation.current !== operation) {
        return;
      }
      setPhase("error");
      setErrorText(
        "Scientific runner readiness could not be read. No scientific action was taken.",
      );
    }
  }

  const blocked = reply?.checks.filter((check) => check.status === "blocked") ?? [];

  return (
    <section
      className="scientific-readiness"
      aria-label="Scientific runner readiness"
      aria-busy={phase === "loading"}
    >
      <h3>Scientific runner readiness</h3>
      <p>Real scientific execution remains disabled.</p>
      <p>Operational readiness. This is not scientific validation.</p>
      <p>Runner ready is not execution authorization, and authorization is not execution.</p>
      <button type="button" onClick={() => void refresh()}>
        Refresh scientific runner readiness
      </button>
      {phase === "loading" ? <p>Reading scientific runner readiness.</p> : null}
      {phase === "error" && errorText ? <p role="alert">{errorText}</p> : null}
      {!reply && phase === "idle" ? (
        <p>Scientific runner readiness has not been read in this view.</p>
      ) : null}
      {reply ? (
        <>
          <dl>
            <div>
              <dt>Status</dt>
              <dd>{STATUS_LABEL[reply.status]}</dd>
            </div>
            <div>
              <dt>Profile</dt>
              <dd>{shown(reply.profile_id)}</dd>
            </div>
            <div>
              <dt>Tool</dt>
              <dd>{shown(reply.tool_name)}</dd>
            </div>
            <div>
              <dt>Tool version</dt>
              <dd>{shown(reply.tool_version)}</dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>{shown(reply.runtime_name)}</dd>
            </div>
            <div>
              <dt>Runtime version</dt>
              <dd>{shown(reply.runtime_version)}</dd>
            </div>
            <div>
              <dt>Image digest</dt>
              <dd>{shown(reply.image_digest)}</dd>
            </div>
            <div>
              <dt>Target scope</dt>
              <dd>{shown(reply.target_scope)}</dd>
            </div>
            <div>
              <dt>Last observation</dt>
              <dd>
                <time dateTime={reply.observed_at}>{reply.observed_at}</time>
              </dd>
            </div>
          </dl>
          <h4>Readiness checks</h4>
          <ul>
            {reply.checks.map((check) => (
              <li key={check.code}>
                <span>{check.status === "passed" ? "Passed" : "Blocked"}</span>{" "}
                <code>{check.code}</code> {check.text}
              </li>
            ))}
          </ul>
          <h4>Missing requirements</h4>
          {blocked.length === 0 ? (
            <p>No missing readiness requirements in this observation.</p>
          ) : (
            <ul aria-label="Missing requirements">
              {blocked.map((check) => (
                <li key={check.code}>
                  <code>{check.code}</code> {check.text}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </section>
  );
}
