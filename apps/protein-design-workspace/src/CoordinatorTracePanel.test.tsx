import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CoordinatorTracePanel } from "./CoordinatorTracePanel";

describe("CoordinatorTracePanel", () => {
  it("shows a rejected trace without publishing it as a synthesis", async () => {
    vi.mocked(fetch).mockResolvedValue(
      Response.json({
        traces: [
          {
            trace_id: "trace_rejected",
            source_query: "What is the score?",
            model: null,
            model_calls: 2,
            usage: { reported_calls: 2, total_tokens: 8, input_tokens: 5, output_tokens: 3 },
            limits: {
              max_model_calls: 6,
              max_output_tokens_per_call: 1500,
              token_budget: 20,
              call_timeout_seconds: 30,
              retries: 0,
              output_token_limit_is_per_call: true,
            },
            validation: { status: "rejected", findings: [{ code: "divergent_score", value: "0.91" }] },
            termination: { status: "synthesis_rejected", stop: "synthesis_rejected" },
            conclusion_accepted: false,
            synthesis: null,
            rejected_output: "The recorded ranking_score is 0.91.",
            limitations: ["This check does not establish scientific correctness."],
            agent_executed: false,
            steps: [
              {
                tool: "get_attempt_evidence",
                specialist_role: "ranking",
                execution: "deterministic_tool",
                agent_executed: false,
                status: "ok",
                summary: "Returned the recorded attempt.",
                resolved_attempt_id: "attempt.2LUTUOX4",
                evidence_references: [
                  { tool: "get_attempt_evidence", status: "ok", resolved_attempt_id: "attempt.2LUTUOX4" },
                ],
              },
              {
                tool: null,
                specialist_role: "prioritization",
                execution: "language_model_synthesis",
                agent_executed: false,
                status: "synthesis_rejected",
                summary: "The harness rejected this synthesis.",
              },
            ],
          },
        ],
        limitations: [],
      }),
    );
    render(<CoordinatorTracePanel requestId="request_demo1" requestRevision={2} />);
    expect(await screen.findByRole("region", { name: "Coordinator trace" })).toHaveTextContent("What is the score?");
    const events = screen.getByRole("list", { name: "Coordinator events" });
    expect(events).toHaveTextContent("Python function get_attempt_evidence");
    expect(events).toHaveTextContent("Role label ranking");
    expect(events).toHaveTextContent("Status ok");
    expect(events).toHaveTextContent("Resolved attempt attempt.2LUTUOX4");
    expect(screen.getByText(/Evidence get_attempt_evidence status ok/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Harness validation" })).toBeInTheDocument();
    expect(screen.getByText(/divergent_score: 0.91/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Rejected output" })).toBeInTheDocument();
    expect(screen.getByText("The recorded ranking_score is 0.91.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Synthesis" })).not.toBeInTheDocument();
    expect(screen.getByText(/token budget 20/)).toBeInTheDocument();
    expect(screen.getByText(/It is not a total cost ceiling/)).toBeInTheDocument();
    expect(screen.getByText("This check does not establish scientific correctness.")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/api/attempt-coordinator?request_id=request_demo1&request_revision=2");
  });

  it("shows the validation limits when no trace is recorded", async () => {
    vi.mocked(fetch).mockResolvedValue(
      Response.json({
        traces: [],
        limitations: ["This check does not establish scientific correctness."],
      }),
    );
    render(<CoordinatorTracePanel requestId="request_demo1" requestRevision={2} />);
    expect(await screen.findByText("No coordinator trace is recorded for this request.")).toBeInTheDocument();
    expect(screen.getByText("This check does not establish scientific correctness.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Synthesis" })).not.toBeInTheDocument();
  });

  it("does not fetch before a request revision is open", () => {
    render(<CoordinatorTracePanel requestId={null} requestRevision={null} />);
    expect(screen.queryByRole("region", { name: "Coordinator trace" })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});
