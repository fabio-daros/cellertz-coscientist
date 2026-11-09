import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";

test("the conversation tab sends the recorded-results question to the coordinator", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        status: "answered",
        text: "model2 is the recorded representative. ranking_score was not computed.",
        attempt_id: "attempt.2LUTUOX4",
        execution_connected: false,
        steps: [
          {
            specialist: "ranking",
            mode: "deterministic",
            tool: "get_attempt_evidence",
            summary: "Reads the verified selection rule and provenance.",
          },
          {
            specialist: "prioritization",
            mode: "language_model",
            tool: null,
            summary: "Interpreted the tool evidence in English. This is not a new measurement.",
          },
        ],
      }),
    })),
  );
  render(<App />);
  await user.click(screen.getByRole("tab", { name: "Conversation" }));
  expect(screen.getByRole("region", { name: "Ask about recorded results" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Legacy read-only conversation" })).toBeInTheDocument();
  await user.type(
    screen.getByLabelText("Question about recorded results"),
    "Why was model 2 selected?",
  );
  await user.click(screen.getByRole("button", { name: "Ask about recorded results" }));
  expect(fetch).toHaveBeenCalledWith("/api/attempt-coordinator", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question: "Why was model 2 selected?" }),
  });
  const answer = await screen.findByRole("region", { name: "Coordinator answer" });
  expect(answer).toHaveTextContent("model2 is the recorded representative.");
  const trace = screen.getByRole("list", { name: "Tool trace" });
  expect(trace).toHaveTextContent("get_attempt_evidence");
  expect(trace).toHaveTextContent("deterministic");
  expect(trace).toHaveTextContent("language_model");
  expect(screen.getByRole("button", { name: "Ask read-only" })).toBeInTheDocument();
});
