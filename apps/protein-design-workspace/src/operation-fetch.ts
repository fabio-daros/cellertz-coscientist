export const recordedJourney = {
  journey_id: "journey_recorded",
  request_id: "request_1",
  request_revision: 4,
  plan_id: null,
  plan_revision: null,
  gpu_execution: "disabled",
  approved: false,
  specialist_agents_executed: false,
  knowledge: { status: "irrelevant", detail: "No matching source.", sources: [] },
  hermes: { status: "not_applicable", detail: "Hermes was not called." },
  stages: [],
};

type FetchMock = {
  mockImplementation: (impl: (url: RequestInfo | URL, init?: RequestInit) => Promise<Response>) => void;
};

type QueuedFetch = Response | Promise<Response> | (() => Promise<Response>);

export function routeOperationFetches(
  fetchMock: FetchMock,
  respond: (body: unknown) => Response,
): (...items: QueuedFetch[]) => void {
  const queue: QueuedFetch[] = [];
  fetchMock.mockImplementation((url, init) => {
    const path = String(url).split("?")[0];
    const method = String(init?.method ?? "GET").toUpperCase();
    if (path.startsWith("/api/design-journeys")) {
      return Promise.resolve(respond(recordedJourney));
    }
    if (method === "GET" && path === "/api/campaigns") {
      return Promise.resolve(respond({ campaigns: [] }));
    }
    if (method === "GET" && path === "/api/attempt-coordinator") {
      return Promise.resolve(respond({ traces: [], limitations: [] }));
    }
    const next = queue.shift();
    if (!next) {
      return Promise.reject(new Error(`unexpected fetch ${String(url)}`));
    }
    const value = typeof next === "function" ? next() : next;
    return Promise.resolve(value);
  });
  return (...items) => {
    queue.push(...items);
  };
}
