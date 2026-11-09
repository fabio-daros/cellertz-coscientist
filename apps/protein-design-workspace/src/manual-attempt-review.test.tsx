import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import model1Pdb from "../public/manual-review/attempt.2LUTUOX4/model1.pdb?raw";
import model2Pdb from "../public/manual-review/attempt.2LUTUOX4/model2.pdb?raw";
import { LegacyWorkspace as App } from "./App";
import { ManualAttemptReview } from "./ManualAttemptReview";
import {
  asCell,
  caCounts,
  cellLabel,
  manualAttemptReview,
  parseCaAtoms,
  SERVED_PDB_URLS,
  servedPdbUrl,
} from "./manual-attempt-review";

function pdbAtom(chain: string, resSeq: number, x: number): string {
  const seq = String(resSeq).padStart(4);
  const xs = x.toFixed(3).padStart(8);
  return `ATOM      1  CA  ALA ${chain}${seq}    ${xs}   0.000   0.000  1.00  0.00           C`;
}

function reviewPdb(): string {
  const lines = [];
  for (let index = 1; index <= 115; index += 1) {
    lines.push(pdbAtom("A", index, index));
  }
  for (let index = 1; index <= 65; index += 1) {
    lines.push(pdbAtom("B", index, index + 200));
  }
  return lines.join("\n");
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

const servedPdbs: Record<string, string> = {
  [SERVED_PDB_URLS[0]]: model1Pdb,
  [SERVED_PDB_URLS[1]]: model2Pdb,
};

describe("manual attempt review records", () => {
  it("serves only the two copied PDBs and their hashes match the review record", async () => {
    expect(manualAttemptReview.served_pdb_urls).toEqual([...SERVED_PDB_URLS]);
    expect(manualAttemptReview.models.model1.pdb_url).toBe(SERVED_PDB_URLS[0]);
    expect(manualAttemptReview.models.model2.pdb_url).toBe(SERVED_PDB_URLS[1]);
    expect(servedPdbUrl("/Users/darosfabio/Downloads/cellertz-review/attempt.2LUTUOX4/model1.pdb")).toBe(false);
    expect(servedPdbUrl("../attempt.2LUTUOX4/model1.pdb")).toBe(false);
    expect(servedPdbUrl("/api/attempts/attempt.c5c1f13e/structures/model1.pdb")).toBe(true);
    expect(servedPdbUrl("/api/attempts/attempt.c5c1f13e/structures/../model1.pdb")).toBe(false);
    expect(await sha256(model1Pdb)).toBe(manualAttemptReview.models.model1.sha256);
    expect(await sha256(model2Pdb)).toBe(manualAttemptReview.models.model2.sha256);
    expect(manualAttemptReview.models.model2.sha256).toBe(manualAttemptReview.provenance.accepted_pdb_sha256);
    expect(manualAttemptReview.models.model2.sha256).toBe(manualAttemptReview.provenance.ranked_pdb_sha256);
    expect(manualAttemptReview.models.model1.sha256).not.toBe(manualAttemptReview.models.model2.sha256);
  });

  it("links each copied PDB to chain A as target and chain B as binder", () => {
    for (const model of [manualAttemptReview.models.model1, manualAttemptReview.models.model2]) {
      if (!servedPdbUrl(model.pdb_url)) {
        throw new Error(model.pdb_url);
      }
      const counts = caCounts(parseCaAtoms(servedPdbs[model.pdb_url]));
      expect(counts).toEqual({ A: model.target_residues, B: model.binder_residues });
      expect(model.target_chain).toBe("A");
      expect(model.binder_chain).toBe("B");
    }
  });

  it("keeps recorded zero, an empty field, and absent data apart", () => {
    const residues = manualAttemptReview.metrics.find((row) => row.metric === "n_InterfaceResidues");
    const bonds = manualAttemptReview.metrics.find((row) => row.metric === "InterfaceHbondsPercentage");
    expect(residues && cellLabel(asCell(residues.model2))).toBe("0 (recorded zero)");
    expect(bonds && cellLabel(asCell(bonds.model2))).toBe("empty field");
    expect(cellLabel(asCell(manualAttemptReview.interface_residues))).toBe("empty field");
    expect(cellLabel(asCell(manualAttemptReview.absent_models[0].pLDDT))).toBe("absent");
    expect(JSON.stringify(manualAttemptReview)).not.toContain("ranking_score");
  });
});

describe("manual attempt review view", () => {
  it("shows both models, the representative rule, and no execution controls", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      expect(servedPdbUrl(url)).toBe(true);
      return { ok: true, text: async () => reviewPdb() } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ManualAttemptReview />);

    const review = screen.getByRole("region", { name: "Manual review of attempt.2LUTUOX4" });
    expect(within(review).getByRole("article", { name: /model1, chain A target, chain B binder/ })).toBeInTheDocument();
    expect(within(review).getByRole("article", { name: /model2, chain A target, chain B binder/ })).toBeInTheDocument();
    expect(review).toHaveTextContent("representative chosen by BindCraft by global pLDDT");
    expect(review).toHaveTextContent("model2 is not the better interface");
    expect(review).toHaveTextContent("no_filters");
    expect(review).toHaveTextContent("Scientific evaluation is pending");
    expect(review).toHaveTextContent("Manual origin");
    expect(review).toHaveTextContent("Outside the ipSAE ranking queue and outside an approved run");
    expect(review).toHaveTextContent("This review has no ranking_score");
    expect(review).toHaveTextContent(manualAttemptReview.models.model1.sha256);
    expect(review).toHaveTextContent(manualAttemptReview.models.model2.sha256);
    expect(within(review).getByRole("link", { name: "PDB for model1" })).toHaveAttribute("href", SERVED_PDB_URLS[0]);
    expect(within(review).getByRole("link", { name: "PDB for model2" })).toHaveAttribute("href", SERVED_PDB_URLS[1]);

    const residueRow = within(review).getByRole("row", { name: /n_InterfaceResidues/ });
    expect(residueRow).toHaveTextContent("15");
    expect(residueRow).toHaveTextContent("0 (recorded zero)");
    const bondRow = within(review).getByRole("row", { name: /InterfaceHbondsPercentage/ });
    expect(bondRow).toHaveTextContent("empty field");
    expect(bondRow).not.toHaveTextContent("recorded zero");
    expect(review).toHaveTextContent("model3 pLDDT: absent");
    expect(within(review).getByRole("region", { name: /Heavy-atom contacts at 5 angstroms/ })).toHaveTextContent(
      "Independent analysis",
    );
    expect(within(review).queryByRole("button")).not.toBeInTheDocument();

    expect(await within(review).findAllByText("Chain A, target, 115 CA. Chain B, binder, 65 CA.")).toHaveLength(2);
    expect(fetchMock.mock.calls.map((call) => call[0]).sort()).toEqual([...SERVED_PDB_URLS].sort());
  });

  it("opens from the workspace tab without the approved-run control", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, text: async () => reviewPdb() }) as Response),
    );
    const user = userEvent.setup();
    render(<App />);
    expect(screen.queryByRole("region", { name: "Manual review of attempt.2LUTUOX4" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Manual review" }));
    expect(screen.getByRole("region", { name: "Manual review of attempt.2LUTUOX4" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create fixture run" })).not.toBeInTheDocument();
  });
});
