import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DesignSuggestion, defaultPicked } from "./DesignSuggestion";
import { blockingQuestions } from "./design-plan";
import type { HotspotBlock, PlanSuggestion } from "./design-plan";

const BLOCKS: HotspotBlock[] = [
  {
    id: "B",
    label: "A66-A125",
    chain: "A",
    lo: 66,
    hi: 125,
    n: 3,
    contiguous: false,
    residues: ["A66", "A124", "A125"],
    priority: 2,
    selectivity: "MEDIUM",
    rationale: "Secondary patch on the same face.",
    risks: "Partly buried in the full-length receptor.",
  },
  {
    id: "A",
    label: "A19-A56",
    chain: "A",
    lo: 19,
    hi: 56,
    n: 2,
    contiguous: false,
    residues: ["A19", "A56"],
    priority: 1,
    selectivity: "HIGH",
    rationale: "Core contact region of the retrieved note.",
    risks: "Competes with the native ligand.",
  },
];

function suggestion(blocks: HotspotBlock[]): PlanSuggestion {
  return {
    suggestionId: "suggestion_1",
    status: "complete",
    applicable: true,
    stale: false,
    requestId: "request_1",
    planId: "plan_1",
    planRevision: 3,
    digest: "d".repeat(64),
    summary: "Two supported regions.",
    suggestions: [
      {
        field: "hotspot_assumption",
        label: "Binding site (hotspots)",
        current: "none",
        suggested: "A19,A56,A66,A124,A125",
        source: "hotspot_identification_agent",
        kind: "agent_recommendation",
        applicable: true,
        blocks,
      },
    ],
    uncertainties: [],
    agent: "Hotspot Identification Agent",
    model: "openai/gpt-4.1-mini",
    tokens: "954",
    tools: [],
    sources: [],
    limitations: [],
    structureSha256: "ab".repeat(32),
    literature: [],
    literatureFromCache: false,
    literatureRetrievedAt: "",
    region: "",
    estimate: "",
    specialistSummary: "",
    specialistLines: [],
    specialistState: "text",
    systemMessages: [],
    residueMapping: "mapped",
    recordComplete: true,
  };
}

function view(blocks: HotspotBlock[]) {
  return {
    current: suggestion(blocks),
    stale: [],
    picked: {},
    setPicked: vi.fn(),
  } as unknown as Parameters<typeof DesignSuggestion>[0]["suggestion"];
}

describe("hotspot blocks in the suggestion panel", () => {
  it("shows each block with its measured range, ordered by priority", () => {
    render(<DesignSuggestion suggestion={view(BLOCKS)} onApply={vi.fn()} />);
    const items = screen.getByRole("list", { name: "Hotspot blocks" }).querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("2 residues spread between A19 and A56, not a continuous stretch (A19, A56).");
    expect(items[0].textContent).toContain("Priority 1.");
    expect(items[0].textContent).toContain("Selectivity HIGH.");
    expect(items[0].textContent).toContain("Risk: Competes with the native ligand.");
    expect(items[1].textContent).toContain("3 residues spread between A66 and A125");
  });

  it("says contact is not measured, so a block is not read as a result", () => {
    render(<DesignSuggestion suggestion={view(BLOCKS)} onApply={vi.fn()} />);
    expect(screen.getAllByText(/Contact with this block is not measured yet/)).toHaveLength(2);
  });

  it("shows the plain residue list unchanged when a suggestion carries no blocks", () => {
    render(<DesignSuggestion suggestion={view([])} onApply={vi.fn()} />);
    expect(screen.queryByRole("list", { name: "Hotspot blocks" })).toBeNull();
    expect(screen.getByText(/A19,A56,A66,A124,A125/)).toBeTruthy();
  });
});

describe("what is ticked before the person touches anything", () => {
  function withKinds(kinds: { field: string; kind: string; applicable?: boolean }[]): PlanSuggestion {
    const base = suggestion([]);
    return {
      ...base,
      suggestions: kinds.map((item) => ({
        field: item.field,
        label: item.field,
        current: "none",
        suggested: "x",
        source: "s",
        kind: item.kind,
        applicable: item.applicable !== false,
      })),
    };
  }

  it("ticks the server's own numbers, which are not a scientific choice", () => {
    const picked = defaultPicked(
      withKinds([
        { field: "cpu_count", kind: "server_configuration" },
        { field: "memory_mib", kind: "server_configuration" },
      ]),
    );
    expect(picked).toEqual({ cpu_count: true, memory_mib: true });
  });

  it("leaves an agent recommendation untouched, so adopting it stays a person's act", () => {
    const picked = defaultPicked(
      withKinds([
        { field: "hotspot_assumption", kind: "agent_recommendation" },
        { field: "binder_length", kind: "alternative" },
        { field: "cpu_count", kind: "server_configuration" },
      ]),
    );
    expect(picked).toEqual({ cpu_count: true });
  });

  it("does not tick a suggestion the server marked inapplicable", () => {
    const picked = defaultPicked(withKinds([{ field: "cpu_count", kind: "server_configuration", applicable: false }]));
    expect(picked).toEqual({});
  });

  it("ticks nothing when there is no suggestion", () => {
    expect(defaultPicked(null)).toEqual({});
  });
});

describe("a note does not hold up review", () => {
  const note = { field: "hotspot_assumption", question: "The stated hotspot was kept.", blocking: false };
  const real = { field: "filter_policy", question: "Which filter policy?", blocking: true };

  it("keeps only the questions that must be answered", () => {
    expect(blockingQuestions([note, real])).toEqual([real]);
  });

  it("treats a question with no mark as blocking, which is what older plans carry", () => {
    const old = { field: "gpu", question: "Which GPU?", blocking: true };
    expect(blockingQuestions([old])).toEqual([old]);
  });
});
