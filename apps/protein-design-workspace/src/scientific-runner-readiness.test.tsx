import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";
import { ScientificRunnerReadiness } from "./ScientificRunnerReadiness";
import {
  selectReadiness,
  type ReadinessReply,
  type ReadinessStatus,
} from "./scientific-runner-readiness";

const READY_CODES = [
  "argv_template",
  "entrypoint",
  "execution_profile",
  "expected_inputs",
  "expected_outputs",
  "image_identity",
  "output_limit",
  "readiness_probe",
  "resource_envelope",
  "runner_disabled",
  "runtime_identity",
  "target_scope",
  "timeout_limit",
  "tool_identity",
  "tool_version",
] as const;

function reply(status: ReadinessStatus, observedAt: string): ReadinessReply {
  const checks =
    status === "ready"
      ? READY_CODES.map((code) => ({
          code,
          status: "passed" as const,
          text: code === "readiness_probe" ? "The readiness probe succeeded." : "Recorded.",
        }))
      : status === "disabled"
        ? [
            {
              code: "runner_disabled" as const,
              status: "blocked" as const,
              text: "Scientific runner readiness is disabled.",
            },
          ]
        : status === "blocked"
          ? [
              {
                code: "tool_version" as const,
                status: "blocked" as const,
                text: "Tool version is not recorded.",
              },
            ]
          : [
              {
                code: "readiness_adapter_failed" as const,
                status: "blocked" as const,
                text: "No scientific action was taken.",
              },
            ];
  return {
    status,
    text:
      status === "configuration_failure" || status === "adapter_failure"
        ? "Scientific runner configuration was rejected. No scientific action was taken."
        : "Operational readiness. This is not scientific validation.",
    session_id: "session_pdl1_prototype",
    profile_id: "pdl1-bindcraft-historical-v1",
    tool_name: "bindcraft",
    tool_version: status === "ready" ? "recorded-tool-1" : null,
    runtime_name: status === "ready" ? "recorded-runtime" : null,
    runtime_version: status === "ready" ? "recorded-runtime-1" : null,
    image_digest: status === "ready" ? `sha256:${"ab".repeat(32)}` : null,
    target_scope: "PDL1",
    manifest_digest: status === "disabled" ? null : "cd".repeat(32),
    observed_at: observedAt,
    checks,
    execution_authorized: false,
    scientific_execution: false,
    scientific_validation: false,
  };
}

function jsonResponse(body: unknown, ok = true): Response {
  return { ok, json: async () => body } as Response;
}

describe("scientific runner readiness", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("shows loading, then a blocked observation, without an execution action", async () => {
    let resolveRead: (value: Response) => void = () => undefined;
    vi.mocked(fetch).mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveRead = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<ScientificRunnerReadiness />);
    const region = screen.getByRole("region", { name: "Scientific runner readiness" });
    expect(region).toHaveTextContent("Real scientific execution remains disabled.");
    expect(region).toHaveTextContent("Operational readiness. This is not scientific validation.");
    expect(region).toHaveTextContent("has not been read");
    expect(
      within(region).queryByRole("button", { name: /execute|run bindcraft|generate candidates/i }),
    ).not.toBeInTheDocument();
    await user.click(within(region).getByRole("button", { name: "Refresh scientific runner readiness" }));
    expect(region).toHaveTextContent("Reading scientific runner readiness.");
    resolveRead(jsonResponse(reply("blocked", "2026-10-12T15:00:00+00:00")));
    expect(await within(region).findByText("Blocked", { selector: "dd" })).toBeInTheDocument();
    expect(within(region).getByRole("list", { name: "Missing requirements" })).toHaveTextContent(
      "tool_version",
    );
    expect(within(region).getByRole("list", { name: "Missing requirements" })).toHaveTextContent(
      "Tool version is not recorded.",
    );
    expect(fetch).toHaveBeenCalledWith("/api/scientific-runner-readiness", {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  });

  it("shows disabled, ready, and error observations", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<ScientificRunnerReadiness />);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply("disabled", "2026-10-12T15:00:00+00:00")));
    await user.click(screen.getByRole("button", { name: "Refresh scientific runner readiness" }));
    expect(await screen.findByText("Disabled", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);

    rerender(<ScientificRunnerReadiness />);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply("ready", "2026-10-12T15:00:02+00:00")));
    await user.click(screen.getAllByRole("button", { name: "Refresh scientific runner readiness" }).at(-1)!);
    expect(await screen.findByText("Ready", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("recorded-tool-1")).toBeInTheDocument();
    expect(screen.getByText("Real scientific execution remains disabled.")).toBeInTheDocument();
    expect(screen.getByText("No missing readiness requirements in this observation.")).toBeInTheDocument();

    vi.mocked(fetch).mockRejectedValueOnce(new Error("network"));
    await user.click(screen.getAllByRole("button", { name: "Refresh scientific runner readiness" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read");

    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(reply("configuration_failure", "2026-10-12T15:00:03+00:00")),
    );
    await user.click(screen.getAllByRole("button", { name: "Refresh scientific runner readiness" }).at(-1)!);
    expect(await screen.findByText("Configuration failure")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("No scientific action was taken.");
  });

  it("ignores a stale refresh that resolves after a newer one", async () => {
    let resolveLate: (value: Response) => void = () => undefined;
    const late = new Promise<Response>((resolve) => {
      resolveLate = resolve;
    });
    vi.mocked(fetch)
      .mockImplementationOnce(() => late)
      .mockResolvedValueOnce(jsonResponse(reply("ready", "2026-10-12T15:00:02+00:00")));
    const user = userEvent.setup();
    render(<ScientificRunnerReadiness />);
    const button = screen.getByRole("button", { name: "Refresh scientific runner readiness" });
    await user.click(button);
    await user.click(button);
    expect(await screen.findByText("Ready", { selector: "dd" })).toBeInTheDocument();
    await act(async () => {
      resolveLate(jsonResponse(reply("blocked", "2026-10-12T15:00:01+00:00")));
    });
    expect(screen.getByText("Ready", { selector: "dd" })).toBeInTheDocument();
    expect(screen.queryByText("Blocked", { selector: "dd" })).not.toBeInTheDocument();
  });

  it("shows a ready docker profile", async () => {
    const user = userEvent.setup();
    const ready = reply("ready", "2026-10-12T15:00:00+00:00");
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ ...ready, profile_id: "pdl1-bindcraft-docker-v1" }),
    );
    render(<ScientificRunnerReadiness />);
    await user.click(screen.getByRole("button", { name: "Refresh scientific runner readiness" }));
    expect(await screen.findByText("pdl1-bindcraft-docker-v1")).toBeInTheDocument();
  });

  it("rejects a non-canonical observation time", async () => {
    const user = userEvent.setup();
    render(<ScientificRunnerReadiness />);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(reply("ready", "2026-10-12T15:00:00Z")));
    await user.click(screen.getByRole("button", { name: "Refresh scientific runner readiness" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read");
    expect(screen.queryByText("Ready", { selector: "dd" })).not.toBeInTheDocument();
  });

  it("shows the fixed failure for a semantically impossible snapshot", async () => {
    const user = userEvent.setup();
    const ready = reply("ready", "2026-10-12T15:00:00+00:00");
    const impossible = [
      { ...ready, checks: [ready.checks[0]] },
      {
        ...ready,
        checks: ready.checks.map((check) =>
          check.code === "tool_version" ? { ...check, status: "blocked" as const } : check,
        ),
      },
      { ...ready, profile_id: "cd22-mber-v1", tool_name: "mber", target_scope: "CD22" },
      { ...ready, tool_version: null },
      { ...ready, tool_version: "latest" },
      { ...ready, image_digest: null },
      { ...ready, manifest_digest: null },
      { ...ready, status: "blocked" as const },
      {
        ...reply("disabled", "2026-10-12T15:00:00+00:00"),
        checks: [{ code: "tool_version", status: "blocked" as const, text: "Tool version is not recorded." }],
      },
      {
        ...reply("configuration_failure", "2026-10-12T15:00:00+00:00"),
        checks: [{ code: "tool_version", status: "passed" as const, text: "Recorded." }],
      },
      {
        ...reply("adapter_failure", "2026-10-12T15:00:00+00:00"),
        checks: [{ code: "readiness_adapter_failed", status: "passed" as const, text: "Recorded." }],
      },
      { ...ready, checks: [ready.checks[0], ...ready.checks] },
      { ...ready, checks: [] },
      { ...ready, text: "see /tmp/cellertz-secret-path" },
      { ...ready, text: "see C:\\cellertz-secret-windows" },
      { ...ready, text: "see `cellertz-secret-command`" },
      { ...ready, text: "control \u0001cellertz-secret" },
      { ...ready, text: "token=cellertz-secret" },
      { ...ready, text: "Traceback cellertz-secret" },
      { ...ready, text: "SELECT cellertz_secret_table" },
    ];
    for (const body of impossible) {
      vi.mocked(fetch).mockReset();
      vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(body));
      const view = render(<ScientificRunnerReadiness />);
      await user.click(screen.getByRole("button", { name: "Refresh scientific runner readiness" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Scientific runner readiness could not be read. No scientific action was taken.",
      );
      expect(screen.queryByText("Ready", { selector: "dd" })).not.toBeInTheDocument();
      expect(view.container.textContent).not.toContain("cellertz-secret");
      expect(view.container.textContent).not.toContain("/tmp/");
      expect(view.container.textContent).not.toContain("Traceback");
      view.unmount();
    }
  });

  it("rejects a payload that uses integers in place of booleans", async () => {
    const user = userEvent.setup();
    render(<ScientificRunnerReadiness />);
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ ...reply("ready", "2026-10-12T15:00:00+00:00"), execution_authorized: 0 }),
    );
    await user.click(screen.getByRole("button", { name: "Refresh scientific runner readiness" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read");
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
  });

  it("keeps the refresh control in the tab order", async () => {
    const user = userEvent.setup();
    render(<ScientificRunnerReadiness />);
    await user.tab();
    expect(screen.getByRole("button", { name: "Refresh scientific runner readiness" })).toHaveFocus();
  });

  it("keeps the section separate from fixture runs and placeholder controls", () => {
    render(<App />);
    const region = screen.getByRole("region", { name: "Scientific runner readiness" });
    expect(region).toHaveTextContent("Real scientific execution remains disabled.");
    expect(screen.getByText(`© ${new Date().getFullYear()} Cellertz Bio`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Language" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Account" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Settings" })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual(["/api/campaigns"]);
    expect(screen.queryByRole("button", { name: "Create fixture run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve request" })).not.toBeInTheDocument();
    expect(within(region).queryByRole("button", { name: "Create fixture run" })).not.toBeInTheDocument();
  });
});

describe("selectReadiness", () => {
  it("drops an older observation and an out-of-order operation", () => {
    const current = reply("ready", "2026-10-12T15:00:02+00:00");
    const older = reply("blocked", "2026-10-12T15:00:01+00:00");
    expect(selectReadiness(current, older, 2, 2)).toBe(current);
    expect(selectReadiness(current, reply("blocked", "2026-10-12T15:00:03+00:00"), 1, 2)).toBe(
      current,
    );
  });

  it("orders canonical instants and ignores a later-sorting non-canonical time", () => {
    const current = reply("ready", "2026-10-12T15:30:00.000200+00:00");
    const olderMicros = reply("blocked", "2026-10-12T15:30:00.000100+00:00");
    const newerMicros = reply("blocked", "2026-10-12T15:30:00.000300+00:00");
    const laterAsText = reply("blocked", "2026-10-12T16:00:00+01:00");
    expect(selectReadiness(current, olderMicros, 2, 2)).toBe(current);
    expect(selectReadiness(current, laterAsText, 2, 2)).toBe(current);
    expect(selectReadiness(current, newerMicros, 2, 2)).toBe(newerMicros);
  });
});
