import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DesignRounds } from "./DesignRounds";

const first = {
  round_id: "round_1",
  parent_round_id: null,
  ordinal: 1,
  status: "open",
  plan_id: "plan_1",
  plan_revision: 1,
  digest: "d1",
  outcome_label: "Execution completed without a successful candidate.",
  execution_completed: true,
  execution: { status: "no_accepted_design" },
  post_generation: { status: "blocked" },
  specialist_stages: [],
  seeded_from: null,
  lesson: null,
};

const child = {
  round_id: "round_2",
  parent_round_id: "round_1",
  ordinal: 2,
  status: "open",
  plan_id: "plan_2",
  plan_revision: 3,
  digest: "d2",
  outcome_label: "No launch recorded.",
  execution_completed: false,
  execution: null,
  post_generation: { status: "blocked" },
  specialist_stages: [],
  seeded_from: { design: "t1_binder_1", attempt_id: "attempt.parent" },
  lesson: {
    status: "declared",
    declared: "Reopen CDR3 alone.",
    learned_from: { status: "ranked", design: "t1_binder_1", scoring_protocol: "ENGAGER_TWO_AXIS_2026", ranked_designs: 3 },
  },
};

function journey(rounds: unknown[]) {
  return { journey_id: "journey_1", request_id: "request_1", request_revision: 1, rounds };
}

function answer(calls: Record<string, () => Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const handler = Object.entries(calls).find(([pattern]) => key.endsWith(pattern));
    return handler ? handler[1]() : ({ ok: false, status: 404, json: async () => ({}) } as Response);
  });
}

function json(body: unknown, status = 200): () => Response {
  return () => ({ ok: status < 400, status, json: async () => body }) as Response;
}

function posts(fetchMock: ReturnType<typeof answer>) {
  return fetchMock.mock.calls.filter((call) => (call[1] as RequestInit | undefined)?.method === "POST");
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("design rounds", () => {
  it("shows nothing when the request has no journey", async () => {
    answer({});
    const { container } = render(<DesignRounds requestId="request_1" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing for a record that is not a journey, which is what the results document looks like", async () => {
    answer({ "/by-request/request_1": json({ request_id: "request_1", results: [] }) });
    const { container } = render(<DesignRounds requestId="request_1" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("opens the next round with the lesson the person wrote, and only then", async () => {
    const fetchMock = answer({
      "/by-request/request_1": json(journey([first])),
      "/journey_1/rounds": json(journey([first, child])),
    });
    render(<DesignRounds requestId="request_1" />);
    const box = await screen.findByLabelText(/Why does the next round differ/);
    expect((box as HTMLTextAreaElement).maxLength).toBe(500);

    await userEvent.type(box, "  Reopen CDR3 alone.  ");
    fireEvent.click(screen.getByRole("button", { name: "Prepare next round" }));

    await screen.findByRole("button", { name: "Run next round" });
    expect(JSON.parse(String((posts(fetchMock)[0][1] as RequestInit).body))).toEqual({
      parent_round_id: "round_1",
      lesson: "Reopen CDR3 alone.",
    });
  });

  it("sends no lesson key when the box is empty, because the route refuses an empty one", async () => {
    const fetchMock = answer({
      "/by-request/request_1": json(journey([first])),
      "/journey_1/rounds": json(journey([first, child])),
    });
    render(<DesignRounds requestId="request_1" />);
    await userEvent.type(await screen.findByLabelText(/Why does the next round differ/), "   ");
    fireEvent.click(screen.getByRole("button", { name: "Prepare next round" }));

    await screen.findByRole("button", { name: "Run next round" });
    expect(JSON.parse(String((posts(fetchMock)[0][1] as RequestInit).body))).toEqual({ parent_round_id: "round_1" });
  });

  it("shows a child what it starts from and why it differs, beside what the parent measured", async () => {
    answer({ "/by-request/request_1": json(journey([first, child])) });
    render(<DesignRounds requestId="request_1" />);

    const round = await screen.findByRole("listitem", { name: "Round 2" });
    expect(round).toHaveTextContent("Parent design t1_binder_1 (attempt.parent).");
    expect(round).toHaveTextContent("Reopen CDR3 alone.");
    expect(round).toHaveTextContent("scored under ENGAGER_TWO_AXIS_2026, 3 ranked");
  });

  it("runs the child with its own plan identity, and not the first round's", async () => {
    const fetchMock = answer({
      "/by-request/request_1": json(journey([first, child])),
      "/design-journeys/run": json({ ...journey([first, { ...child, execution: { status: "running" }, post_generation: { status: "queued" } }]) }),
    });
    render(<DesignRounds requestId="request_1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Run next round" }));

    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(JSON.parse(String((posts(fetchMock)[0][1] as RequestInit).body))).toEqual({
      request_id: "request_1",
      request_revision: 1,
      plan_id: "plan_2",
      revision: 3,
      digest: "d2",
      idempotency_key: "run_plan_2_3_d2",
    });
  });

  it("says why the server refused a run, in words", async () => {
    answer({
      "/by-request/request_1": json(journey([first, child])),
      "/design-journeys/run": json({ status: "gpu_not_ready" }, 409),
    });
    render(<DesignRounds requestId="request_1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Run next round" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The GPU is not ready. Nothing was started.");
  });

  it("offers neither action while a round is running, and says so", async () => {
    answer({
      "/by-request/request_1": json(
        journey([first, { ...child, execution: { status: "running" }, post_generation: { status: "running" } }]),
      ),
    });
    render(<DesignRounds requestId="request_1" />);

    expect(await screen.findByText(/Running\. The server is working on it\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run next round" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare next round" })).not.toBeInTheDocument();
  });

  it("follows a running round until the server finishes it, then stops asking", async () => {
    // An mBER trajectory took 19 minutes; a screen that does not refresh would
    // say Running for as long as the person looked at it.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const running = { ...child, execution: { status: "running" }, post_generation: { status: "running" } };
      const finished = {
        ...child,
        outcome_label: "Execution completed without a successful candidate.",
        execution_completed: true,
        execution: { status: "no_accepted_design" },
        post_generation: { status: "blocked" },
      };
      let reads = 0;
      const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
        reads += 1;
        return { ok: true, status: 200, json: async () => journey([first, reads === 1 ? running : finished]) } as Response;
      });
      render(<DesignRounds requestId="request_1" />);
      expect(await screen.findByText(/Running\. The server is working on it\./)).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(4100);

      expect(await screen.findByText(/Execution completed without a successful candidate\./)).toBeInTheDocument();
      expect(screen.queryByText(/Running\. The server is working on it\./)).not.toBeInTheDocument();
      const settled = fetchMock.mock.calls.length;
      await vi.advanceTimersByTimeAsync(20000);
      expect(fetchMock.mock.calls.length).toBe(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the specialists' score under the protocol that produced it", async () => {
    const scored = {
      ...first,
      outcome_label: "Successful candidate generation.",
      execution: { status: "ranked", candidate_identity: { design: "t1_binder_1" } },
      specialist_stages: [
        {
          stage_id: "scoring",
          status: "completed",
          output: { ranking_signal: "ENGAGER_TWO_AXIS_2026", ranking_score: 0.5, candidate_id: "mber_t1_binder_1" },
        },
      ],
    };
    answer({ "/by-request/request_1": json(journey([scored])) });
    render(<DesignRounds requestId="request_1" />);

    const round = await screen.findByRole("listitem", { name: "Round 1" });
    expect(round).toHaveTextContent("Accepted design t1_binder_1.");
    expect(round).toHaveTextContent("Scored under ENGAGER_TWO_AXIS_2026: 0.5 for mber_t1_binder_1.");
  });
});
