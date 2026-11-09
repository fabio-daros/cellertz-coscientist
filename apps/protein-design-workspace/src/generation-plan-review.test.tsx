import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { LegacyWorkspace as App } from "./App";
import { GenerationPlanReview } from "./GenerationPlanReview";

test("plan approval and authorization binding stay separate human actions", async () => {
  const user = userEvent.setup();
  const plan = {
    plan_id: "plan_test",
    revision: 1,
    digest: "ab".repeat(32),
    approved: false,
    authorization_bound: false,
    plan_approval_grants_execution: false,
    execution_connected: false as const,
    questions: [
      {
        field: "input_artifacts",
        question: "Registered input files are not verified. A stated SHA-256 is not verification.",
      },
    ],
    fields: { input_hashes_verified_against_files: false },
    historical_manual_attempt: "attempt.2LUTUOX4",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path === "/api/attempt-coordinator") {
        return { ok: true, json: async () => ({ text: "The draft is not approved.", plan }) };
      }
      if (path === "/api/generation-plan/decision") {
        return { ok: true, json: async () => ({ ...plan, status: "approval_rejected" }) };
      }
      return {
        ok: true,
        json: async () => ({
          ...plan,
          blockers: ["scientific_execution_disabled", "pdl1_worker_loss_deadline_unresolved"],
        }),
      };
    }),
  );
  render(<GenerationPlanReview />);
  expect(screen.getByLabelText("Generation plan review")).toHaveTextContent(
    "Plan approval records that a person reviewed one revision.",
  );
  expect(screen.getByLabelText("Generation plan review")).toHaveTextContent(
    "a chat answer grants neither",
  );
  await user.type(screen.getByLabelText("Generation request"), "Generate another binder.");
  await user.click(screen.getByRole("button", { name: "Draft a plan" }));
  expect(screen.getByText("plan_test · revision 1 · unapproved · authorization not bound")).toBeInTheDocument();
  expect(screen.getByText(/stated SHA-256 is not verification/)).toBeInTheDocument();
  await user.type(screen.getByLabelText("Reviewer"), "professor");
  await user.type(screen.getByLabelText("Reason for this revision"), "Reviewed.");
  await user.click(screen.getByRole("button", { name: "Record approval" }));
  expect(screen.getByRole("status")).toHaveTextContent("This revision stays unapproved.");
  await user.click(screen.getByRole("button", { name: "Check dispatch" }));
  expect(screen.getByLabelText("Dispatch blockers")).toHaveTextContent("scientific_execution_disabled");
  expect(screen.getByLabelText("Dispatch blockers")).toHaveTextContent(
    "pdl1_worker_loss_deadline_unresolved",
  );
});

const recordedPlan = {
  plan_id: "plan_recorded",
  revision: 3,
  digest: "cd".repeat(32),
  status: "draft",
  approved: false,
  authorization_bound: false,
  plan_approval_grants_execution: false,
  execution_connected: false as const,
  conversation_id: "coord_recorded",
  historical_manual_attempt: "attempt.2LUTUOX4",
  fields: {
    target_identity: "PDL1",
    seed: 1,
    input_hashes_verified_against_files: true,
    input_artifacts: [{ role: "target_structure", sha256: "ab".repeat(32) }],
  },
  questions: [{ field: "binder_length", question: "What binder length should the plan use?" }],
};

test("a coordinator plan is shown without drafting it again", async () => {
  render(
    <GenerationPlanReview
      incomingReply={{
        text: "I drafted a binder of length 80 with no open questions.",
        steps: [{ specialist: "generation", mode: "deterministic", tool: "propose_generation_plan", summary: "Drafts a plan.", status: "draft" }],
        plan: recordedPlan,
      }}
    />,
  );
  const review = screen.getByRole("region", { name: "Recorded generation plan" });
  expect(review).toHaveTextContent("plan_recorded · revision 3 · draft · unapproved");
  expect(review).toHaveTextContent("Input files are verified against the registered copies: yes");
  expect(review).toHaveTextContent("PDL1");
  expect(review).toHaveTextContent("seed");
  expect(review).toHaveTextContent("1");
  expect(review).toHaveTextContent("target_structure");
  expect(review).toHaveTextContent("What binder length should the plan use?");
  expect(review).toHaveTextContent("Approval launches execution: no");
  expect(review).not.toHaveTextContent("length 80");
});

test("a proposal without a plan field is reported and does not invent settings", () => {
  render(
    <GenerationPlanReview
      incomingReply={{
        text: "A plan exists with binder length 65 and seed 7.",
        steps: [{ specialist: "generation", mode: "deterministic", tool: "propose_generation_plan", summary: "Drafts a plan.", status: "ready_for_review" }],
        plan: null,
      }}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("did not return a structured plan");
  expect(screen.queryByRole("region", { name: "Recorded generation plan" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Generation plan review")).not.toHaveTextContent("binder length 65");
});

test("an empty draft reports an error and keeps the displayed plan", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  render(<GenerationPlanReview incomingReply={{ text: "Recorded.", plan: recordedPlan }} />);
  await user.click(screen.getByRole("button", { name: "Draft a plan" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Enter a generation request");
  expect(screen.getByRole("region", { name: "Recorded generation plan" })).toHaveTextContent("plan_recorded");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("approval posts the displayed plan id and revision and does not launch", async () => {
  const user = userEvent.setup();
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      calls.push({ path, body: JSON.parse(String(init?.body ?? "{}")) });
      return { ok: true, json: async () => ({ ...recordedPlan, approved: false, execution_connected: false }) };
    }),
  );
  render(<GenerationPlanReview incomingReply={{ text: "Recorded.", plan: recordedPlan }} />);
  await user.type(screen.getByLabelText("Reviewer"), "professor");
  await user.type(screen.getByLabelText("Reason for this revision"), "Reviewed the displayed revision.");
  await user.click(screen.getByRole("button", { name: "Record approval" }));
  expect(calls.map((call) => call.path)).toEqual(["/api/generation-plan/decision"]);
  expect(calls[0]?.body.plan_id).toBe("plan_recorded");
  expect(calls[0]?.body.revision).toBe(3);
  expect(screen.getByRole("region", { name: "Recorded generation plan" })).toHaveTextContent("unapproved");
});

test("the same draft request is not submitted twice", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async () => ({
    ok: true,
    json: async () => ({ text: "The draft is not approved.", plan: recordedPlan, conversation_id: "coord_recorded" }),
  }));
  vi.stubGlobal("fetch", fetchMock);
  render(<GenerationPlanReview />);
  await user.type(screen.getByLabelText("Generation request"), "Generate another binder.");
  await user.click(screen.getByRole("button", { name: "Draft a plan" }));
  expect(await screen.findByRole("region", { name: "Recorded generation plan" })).toHaveTextContent("plan_recorded");
  await user.click(screen.getByRole("button", { name: "Draft a plan" }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("alert")).toHaveTextContent("existing plan was kept");
});

test("drafting shows progress until the coordinator answers", async () => {
  const user = userEvent.setup();
  let finish: (value: { ok: boolean; json: () => Promise<unknown> }) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<{ ok: boolean; json: () => Promise<unknown> }>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  render(<GenerationPlanReview />);
  await user.type(screen.getByLabelText("Generation request"), "Generate another binder.");
  await user.click(screen.getByRole("button", { name: "Draft a plan" }));
  expect(screen.getByRole("status")).toHaveTextContent("Drafting a plan.");
  finish({ ok: true, json: async () => ({ text: "The draft is not approved.", plan: recordedPlan }) });
  expect(await screen.findByRole("region", { name: "Recorded generation plan" })).toHaveTextContent("plan_recorded");
});

test("the conversation question renders its plan in the review panel", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        text: "A plan was recorded.",
        conversation_id: "coord_recorded",
        steps: [{ specialist: "generation", mode: "deterministic", tool: "propose_generation_plan", summary: "Drafts a plan.", status: "draft" }],
        plan: recordedPlan,
      }),
    })),
  );
  render(<App />);
  await user.click(screen.getByRole("tab", { name: "Conversation" }));
  await user.type(screen.getByLabelText("Question about recorded results"), "Generate another binder.");
  await user.click(screen.getByRole("button", { name: "Ask about recorded results" }));
  expect(await screen.findByRole("region", { name: "Recorded generation plan" })).toHaveTextContent(
    "plan_recorded · revision 3",
  );
  expect(screen.getByRole("button", { name: "Draft a plan" })).toBeInTheDocument();
});
