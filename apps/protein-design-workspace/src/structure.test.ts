import { describe, expect, it } from "vitest";
import model1Pdb from "../public/manual-review/attempt.2LUTUOX4/model1.pdb?raw";
import model2Pdb from "../public/manual-review/attempt.2LUTUOX4/model2.pdb?raw";
import { asCell, manualAttemptReview } from "./manual-attempt-review";
import {
  assignSecondaryStructure,
  boundingRadius,
  catmullRom,
  centroid,
  contactAgreement,
  heavyAtomContacts,
  parseStructure,
  secondaryStructureShare,
} from "./structure";

const review = manualAttemptReview;
const pdbs: Record<string, string> = { model1: model1Pdb, model2: model2Pdb };
const models = [review.models.model1, review.models.model2];

function contactsFor(model: (typeof models)[number]) {
  const structure = parseStructure(pdbs[model.id]);
  return {
    structure,
    contacts: heavyAtomContacts(
      structure.heavyAtoms,
      model.target_chain,
      model.binder_chain,
      model.contacts.cutoff_angstrom,
    ),
  };
}

describe("heavy-atom contacts computed from the served PDB", () => {
  it.each(models)("reproduces every recorded count for $id", (model) => {
    const { contacts } = contactsFor(model);
    for (const item of contactAgreement(contacts, model.contacts)) {
      expect(item.matches, `${model.id} ${item.field}: ${item.computed} vs ${item.recorded}`).toBe(
        true,
      );
    }
  });

  it("finds 37 pairs on model1 and none on model2", () => {
    expect(contactsFor(review.models.model1).contacts.pairs).toHaveLength(37);
    expect(contactsFor(review.models.model2).contacts.pairs).toHaveLength(0);
  });

  it("contacts the recorded target hotspot residue on model1", () => {
    const hotspot = Number(review.provenance.target_hotspot_residues);
    expect(Number.isFinite(hotspot)).toBe(true);
    expect(contactsFor(review.models.model1).contacts.targetResidues).toContain(hotspot);
    expect(contactsFor(review.models.model2).contacts.targetResidues).toHaveLength(0);
  });

  it("keeps the closest atom pair for each residue pair", () => {
    const { contacts } = contactsFor(review.models.model1);
    for (const pair of contacts.pairs) {
      expect(pair.distance).toBeLessThanOrEqual(review.models.model1.contacts.cutoff_angstrom);
      expect(Math.hypot(pair.from.x - pair.to.x, pair.from.y - pair.to.y, pair.from.z - pair.to.z)).toBeCloseTo(
        pair.distance,
        6,
      );
    }
  });

  it("reports a disagreement instead of hiding it", () => {
    const { contacts } = contactsFor(review.models.model1);
    const wrong = { ...review.models.model1.contacts, residue_pairs: 99 };
    const agreement = contactAgreement(contacts, wrong);
    const pairs = agreement.find((item) => item.field === "Residue pairs");
    expect(pairs?.matches).toBe(false);
    expect(pairs?.computed).toBe(37);
    expect(pairs?.recorded).toBe(99);
  });

  it("excludes hydrogen from the heavy-atom count", () => {
    const withHydrogen = [
      "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C",
      "ATOM      2  HA  ALA A   1       1.000   0.000   0.000  1.00  0.00           H",
      "ATOM      3  H   ALA A   1       2.000   0.000   0.000  1.00  0.00            ",
    ].join("\n");
    expect(parseStructure(withHydrogen).heavyAtoms).toHaveLength(1);
  });
});

describe("geometric secondary structure", () => {
  it("assigns a helix to an ideal alpha helix", () => {
    const residues = [];
    for (let index = 0; index < 16; index += 1) {
      const angle = index * 1.745;
      residues.push({
        chain: "B",
        resSeq: index + 1,
        x: 2.3 * Math.cos(angle),
        y: 2.3 * Math.sin(angle),
        z: index * 1.5,
      });
    }
    const assigned = assignSecondaryStructure(residues);
    expect(assigned.filter((item) => item === "H").length).toBeGreaterThan(8);
    expect(assigned).not.toContain("E");
  });

  it("assigns a strand to an extended chain", () => {
    const residues = [];
    for (let index = 0; index < 12; index += 1) {
      residues.push({ chain: "B", resSeq: index + 1, x: index * 3.3, y: 0, z: 0 });
    }
    const assigned = assignSecondaryStructure(residues);
    expect(assigned.filter((item) => item === "E").length).toBeGreaterThan(5);
    expect(assigned).not.toContain("H");
  });

  it("leaves a two-residue chain as loop", () => {
    const assigned = assignSecondaryStructure([
      { chain: "B", resSeq: 1, x: 0, y: 0, z: 0 },
      { chain: "B", resSeq: 2, x: 3.8, y: 0, z: 0 },
    ]);
    expect(assigned).toEqual(["L", "L"]);
  });

  it("stays within three residues of the recorded binder composition", () => {
    for (const model of models) {
      const { structure } = contactsFor(model);
      const share = secondaryStructureShare(structure.secondary[model.binder_chain] ?? []);
      const recordedShare = (name: string) => {
        const row = review.metrics.find((item) => item.metric === name);
        if (!row) {
          throw new Error(`missing metric: ${name}`);
        }
        const cell = asCell(model.id === "model1" ? row.model1 : row.model2);
        if (cell.kind !== "recorded" && cell.kind !== "recorded_zero") {
          throw new Error(`${name} is not recorded for ${model.id}`);
        }
        return Number(cell.text);
      };
      const residues = (percent: number) => Math.round((percent / 100) * model.binder_residues);
      expect(
        Math.abs(residues(share.helix) - residues(recordedShare("Binder_Helix%"))),
      ).toBeLessThanOrEqual(3);
      expect(
        Math.abs(residues(share.sheet) - residues(recordedShare("Binder_BetaSheet%"))),
      ).toBeLessThanOrEqual(3);
    }
  });

  it("agrees that no helix residue touches the interface", () => {
    const model = review.models.model1;
    const { structure, contacts } = contactsFor(model);
    const residues = structure.backbone[model.binder_chain] ?? [];
    const assigned = structure.secondary[model.binder_chain] ?? [];
    const kinds = contacts.binderResidues.map((resSeq) => {
      const index = residues.findIndex((item) => item.resSeq === resSeq);
      return assigned[index];
    });
    expect(kinds).not.toContain("H");
    const helixShare = review.metrics.find((item) => item.metric === "Interface_Helix%");
    expect(helixShare && "text" in helixShare.model1 ? helixShare.model1.text : null).toBe("0.0");
  });
});

describe("cartoon spine", () => {
  it("passes through the first and last control point", () => {
    const points = [
      { x: 0, y: 0, z: 0 },
      { x: 1, y: 2, z: 0 },
      { x: 4, y: 1, z: 1 },
    ];
    const spline = catmullRom(points, 6);
    expect(spline[0]).toEqual(points[0]);
    expect(spline[spline.length - 1]).toEqual(points[2]);
    expect(spline.length).toBeGreaterThan(points.length);
  });

  it("returns the input when there is nothing to interpolate", () => {
    expect(catmullRom([], 5)).toEqual([]);
    expect(catmullRom([{ x: 1, y: 1, z: 1 }], 5)).toEqual([{ x: 1, y: 1, z: 1 }]);
  });

  it("centres and bounds the structure", () => {
    const points = [
      { x: -2, y: 0, z: 0 },
      { x: 2, y: 0, z: 0 },
    ];
    const centre = centroid(points);
    expect(centre).toEqual({ x: 0, y: 0, z: 0 });
    expect(boundingRadius(points, centre)).toBe(2);
  });
});
