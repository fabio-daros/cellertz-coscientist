import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DesignDraftReply, ExecutionBudget } from "./contracts";
import { HumanReviewPanel } from "./HumanReviewPanel";

const budget: ExecutionBudget = {
  token_ceiling: 1,
  model_cost_ceiling: null,
  scientific_tool_cost_ceiling: null,
  warning_threshold: null,
  action_on_limit: "stop",
  human_override_required: true,
  currency: null,
  authorizes_execution: false,
};

const draftReply: DesignDraftReply = {
  status: "request_complete",
  text: "The request is structurally complete. It is not authorized and not executable.",
  session_id: "session_pdl1_prototype",
  execution_authorized: false,
  executable: false,
  changed_fields: [],
  questions: [],
  issues: [],
  draft: {
    request_id: "request_1",
    session_id: "session_pdl1_prototype",
    revision: 2,
    completeness: "complete",
    execution_authorized: false,
    executable: false,
    fields: [],
    issues: [],
    input_selection: {
      schema: "cellertz.pdl1_input_selection.v1",
      manifest_digest: "ab".repeat(32),
      target_structure_sha256: "cd".repeat(32),
      settings_sha256: "ef".repeat(32),
      advanced_profile_sha256: "12".repeat(32),
    },
  },
  request: null,
  instrumentation: {
    interaction_id: "interaction_1",
    recorded: false,
    total_tokens: null,
    observation: "no_model_usage",
    event: null,
  },
};

describe("verified input inspection", () => {
  it("shows server digests and does not send them with the decision", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn();
    render(
      <HumanReviewPanel
        sessionId="session_pdl1_prototype"
        draftReply={draftReply}
        budget={budget}
        reviewerId="scientist"
        reason="reviewed the digests"
        onReviewerId={() => undefined}
        onReason={() => undefined}
        onDecide={onDecide}
        decisionLoading={false}
        decisionReply={null}
        executionReply={null}
        onDispatch={() => undefined}
        dispatchLoading={false}
      />,
    );
    expect(screen.getByText(/manifest abab/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/path/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve request" }));
    expect(onDecide).toHaveBeenCalledWith("approved");
  });
});
