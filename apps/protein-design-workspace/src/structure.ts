/**
 * Geometry for the cartoon renderer.
 *
 * Everything here is computed from the served PDB with the criterion the
 * review record states: non-hydrogen atoms, 5.0 angstrom cutoff. Computed
 * values are never presented as recorded values. `agreement` reports the
 * comparison so a mismatch is visible instead of hidden.
 */

export type Vec3 = { x: number; y: number; z: number };

export type HeavyAtom = Vec3 & {
  name: string;
  chain: string;
  resSeq: number;
};

export type Residue = Vec3 & {
  chain: string;
  resSeq: number;
};

export type SecondaryStructure = "H" | "E" | "L";

export type ContactPair = {
  targetResSeq: number;
  binderResSeq: number;
  distance: number;
  from: Vec3;
  to: Vec3;
};

export type Contacts = {
  cutoff: number;
  pairs: ContactPair[];
  targetResidues: number[];
  binderResidues: number[];
  minimumDistance: number | null;
  targetHeavyAtoms: number;
  binderHeavyAtoms: number;
};

export type Structure = {
  heavyAtoms: HeavyAtom[];
  backbone: Record<string, Residue[]>;
  secondary: Record<string, SecondaryStructure[]>;
};

const HYDROGEN_ELEMENT = "H";

export function parseHeavyAtoms(pdb: string): HeavyAtom[] {
  const seen = new Set<string>();
  const atoms: HeavyAtom[] = [];
  for (const line of pdb.split(/\r?\n/)) {
    if (!line.startsWith("ATOM") && !line.startsWith("HETATM")) {
      continue;
    }
    const name = line.slice(12, 16).trim();
    const element = line.slice(76, 78).trim();
    if (element === HYDROGEN_ELEMENT) {
      continue;
    }
    if (element === "" && name.startsWith(HYDROGEN_ELEMENT)) {
      continue;
    }
    const chain = line.slice(21, 22).trim();
    const resSeq = Number(line.slice(22, 26));
    const x = Number(line.slice(30, 38));
    const y = Number(line.slice(38, 46));
    const z = Number(line.slice(46, 54));
    if (chain === "" || !Number.isFinite(resSeq)) {
      continue;
    }
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
      continue;
    }
    const key = `${chain}:${resSeq}:${line.slice(26, 27)}:${name}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    atoms.push({ name, chain, resSeq, x, y, z });
  }
  return atoms;
}

export function backboneByChain(atoms: HeavyAtom[]): Record<string, Residue[]> {
  const chains: Record<string, Residue[]> = {};
  for (const atom of atoms) {
    if (atom.name !== "CA") {
      continue;
    }
    const list = chains[atom.chain] ?? [];
    list.push({ chain: atom.chain, resSeq: atom.resSeq, x: atom.x, y: atom.y, z: atom.z });
    chains[atom.chain] = list;
  }
  for (const chain of Object.keys(chains)) {
    chains[chain].sort((left, right) => left.resSeq - right.resSeq);
  }
  return chains;
}

function distance(left: Vec3, right: Vec3): number {
  return Math.hypot(left.x - right.x, left.y - right.y, left.z - right.z);
}

/**
 * Assignment from alpha-carbon geometry alone, in the spirit of P-SEA.
 * It is an approximation. It is not DSSP and not the BindCraft column.
 */
export function assignSecondaryStructure(residues: Residue[]): SecondaryStructure[] {
  const count = residues.length;
  const assigned: SecondaryStructure[] = new Array(count).fill("L");
  const span = (index: number, step: number): number | null => {
    const other = index + step;
    if (other < 0 || other >= count) {
      return null;
    }
    if (residues[other].resSeq - residues[index].resSeq !== step) {
      return null;
    }
    return distance(residues[index], residues[other]);
  };
  for (let index = 0; index < count; index += 1) {
    const d2 = span(index, 2);
    const d3 = span(index, 3);
    const d4 = span(index, 4);
    if (d3 !== null && d4 !== null && d3 >= 4.8 && d3 <= 6.9 && d4 >= 5.2 && d4 <= 7.6) {
      assigned[index] = "H";
      continue;
    }
    if (
      d2 !== null &&
      d3 !== null &&
      d4 !== null &&
      d2 >= 6.0 &&
      d2 <= 7.6 &&
      d3 >= 9.0 &&
      d3 <= 10.8 &&
      d4 >= 11.6 &&
      d4 <= 13.8
    ) {
      assigned[index] = "E";
    }
  }
  for (let index = 1; index < count - 1; index += 1) {
    if (
      assigned[index] !== assigned[index - 1] &&
      assigned[index] !== assigned[index + 1] &&
      assigned[index - 1] === assigned[index + 1]
    ) {
      assigned[index] = assigned[index - 1];
    }
  }
  for (let index = 1; index < count - 1; index += 1) {
    if (assigned[index] !== "L" && assigned[index - 1] === "L" && assigned[index + 1] === "L") {
      assigned[index] = "L";
    }
  }
  return assigned;
}

export function parseStructure(pdb: string): Structure {
  const heavyAtoms = parseHeavyAtoms(pdb);
  const backbone = backboneByChain(heavyAtoms);
  const secondary: Record<string, SecondaryStructure[]> = {};
  for (const [chain, residues] of Object.entries(backbone)) {
    secondary[chain] = assignSecondaryStructure(residues);
  }
  return { heavyAtoms, backbone, secondary };
}

/**
 * Minimum heavy-atom distance per residue pair, keeping the closest pair of
 * atoms so the contact can be drawn where it actually is.
 */
export function heavyAtomContacts(
  atoms: HeavyAtom[],
  targetChain: string,
  binderChain: string,
  cutoff: number,
): Contacts {
  const target = atoms.filter((atom) => atom.chain === targetChain);
  const binder = atoms.filter((atom) => atom.chain === binderChain);
  const closest = new Map<string, ContactPair>();
  const targetResidues = new Set<number>();
  const binderResidues = new Set<number>();
  let minimumDistance: number | null = null;
  for (const left of target) {
    for (const right of binder) {
      const separation = distance(left, right);
      if (minimumDistance === null || separation < minimumDistance) {
        minimumDistance = separation;
      }
      if (separation > cutoff) {
        continue;
      }
      targetResidues.add(left.resSeq);
      binderResidues.add(right.resSeq);
      const key = `${left.resSeq}:${right.resSeq}`;
      const current = closest.get(key);
      if (!current || separation < current.distance) {
        closest.set(key, {
          targetResSeq: left.resSeq,
          binderResSeq: right.resSeq,
          distance: separation,
          from: { x: left.x, y: left.y, z: left.z },
          to: { x: right.x, y: right.y, z: right.z },
        });
      }
    }
  }
  const pairs = [...closest.values()].sort(
    (left, right) =>
      left.targetResSeq - right.targetResSeq || left.binderResSeq - right.binderResSeq,
  );
  return {
    cutoff,
    pairs,
    targetResidues: [...targetResidues].sort((left, right) => left - right),
    binderResidues: [...binderResidues].sort((left, right) => left - right),
    minimumDistance,
    targetHeavyAtoms: target.length,
    binderHeavyAtoms: binder.length,
  };
}

export type Agreement = {
  field: string;
  computed: number | null;
  recorded: number;
  matches: boolean;
};

export function contactAgreement(
  contacts: Contacts,
  recorded: {
    residue_pairs: number;
    target_residues_in_contact: number;
    binder_residues_in_contact: number;
    minimum_heavy_atom_distance_angstrom: number;
    target_heavy_atoms: number;
    binder_heavy_atoms: number;
  },
): Agreement[] {
  const rounded =
    contacts.minimumDistance === null ? null : Number(contacts.minimumDistance.toFixed(3));
  return [
    {
      field: "Residue pairs",
      computed: contacts.pairs.length,
      recorded: recorded.residue_pairs,
      matches: contacts.pairs.length === recorded.residue_pairs,
    },
    {
      field: "Target residues in contact",
      computed: contacts.targetResidues.length,
      recorded: recorded.target_residues_in_contact,
      matches: contacts.targetResidues.length === recorded.target_residues_in_contact,
    },
    {
      field: "Binder residues in contact",
      computed: contacts.binderResidues.length,
      recorded: recorded.binder_residues_in_contact,
      matches: contacts.binderResidues.length === recorded.binder_residues_in_contact,
    },
    {
      field: "Minimum heavy-atom distance",
      computed: rounded,
      recorded: recorded.minimum_heavy_atom_distance_angstrom,
      matches: rounded === recorded.minimum_heavy_atom_distance_angstrom,
    },
    {
      field: "Target heavy atoms",
      computed: contacts.targetHeavyAtoms,
      recorded: recorded.target_heavy_atoms,
      matches: contacts.targetHeavyAtoms === recorded.target_heavy_atoms,
    },
    {
      field: "Binder heavy atoms",
      computed: contacts.binderHeavyAtoms,
      recorded: recorded.binder_heavy_atoms,
      matches: contacts.binderHeavyAtoms === recorded.binder_heavy_atoms,
    },
  ];
}

/** Catmull-Rom through the residue centres, for a smooth cartoon spine. */
export function catmullRom(points: Vec3[], samplesPerSegment: number): Vec3[] {
  if (points.length < 2) {
    return [...points];
  }
  const at = (index: number) => points[Math.min(Math.max(index, 0), points.length - 1)];
  const out: Vec3[] = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const p0 = at(index - 1);
    const p1 = at(index);
    const p2 = at(index + 1);
    const p3 = at(index + 2);
    for (let step = 0; step < samplesPerSegment; step += 1) {
      const t = step / samplesPerSegment;
      const t2 = t * t;
      const t3 = t2 * t;
      out.push({
        x: 0.5 * (2 * p1.x + (-p0.x + p2.x) * t + (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 + (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3),
        y: 0.5 * (2 * p1.y + (-p0.y + p2.y) * t + (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 + (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3),
        z: 0.5 * (2 * p1.z + (-p0.z + p2.z) * t + (2 * p0.z - 5 * p1.z + 4 * p2.z - p3.z) * t2 + (-p0.z + 3 * p1.z - 3 * p2.z + p3.z) * t3),
      });
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

export function subtract(left: Vec3, right: Vec3): Vec3 {
  return { x: left.x - right.x, y: left.y - right.y, z: left.z - right.z };
}

export function cross(left: Vec3, right: Vec3): Vec3 {
  return {
    x: left.y * right.z - left.z * right.y,
    y: left.z * right.x - left.x * right.z,
    z: left.x * right.y - left.y * right.x,
  };
}

export function normalize(value: Vec3): Vec3 {
  const length = Math.hypot(value.x, value.y, value.z);
  if (length === 0) {
    return { x: 0, y: 0, z: 0 };
  }
  return { x: value.x / length, y: value.y / length, z: value.z / length };
}

export function centroid(points: Vec3[]): Vec3 {
  if (points.length === 0) {
    return { x: 0, y: 0, z: 0 };
  }
  let x = 0;
  let y = 0;
  let z = 0;
  for (const point of points) {
    x += point.x;
    y += point.y;
    z += point.z;
  }
  return { x: x / points.length, y: y / points.length, z: z / points.length };
}

export function boundingRadius(points: Vec3[], centre: Vec3): number {
  let radius = 1;
  for (const point of points) {
    radius = Math.max(radius, distance(point, centre));
  }
  return radius;
}

export function secondaryStructureShare(
  assigned: SecondaryStructure[],
): { helix: number; sheet: number; loop: number } {
  const count = assigned.length;
  if (count === 0) {
    return { helix: 0, sheet: 0, loop: 0 };
  }
  const share = (kind: SecondaryStructure) =>
    Number(((assigned.filter((item) => item === kind).length / count) * 100).toFixed(2));
  return { helix: share("H"), sheet: share("E"), loop: share("L") };
}
