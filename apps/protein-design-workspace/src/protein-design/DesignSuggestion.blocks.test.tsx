import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DesignSuggestion } from "./DesignSuggestion";
import type { HotspotBlock, PlanSuggestion } from "./design-plan";

const BLOCKS: HotspotBlock[] = [
  {
    id: "B",
    label: "A66-A125",
    chain: "A",
    lo: 66,
    hi: 125,
    n: 3,
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
    expect(items[0].textContent).toContain("A A19-A56 — 2 residues (A19, A56).");
    expect(items[0].textContent).toContain("Priority 1.");
    expect(items[0].textContent).toContain("Selectivity HIGH.");
    expect(items[0].textContent).toContain("Risk: Competes with the native ligand.");
    expect(items[1].textContent).toContain("B A66-A125 — 3 residues");
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
