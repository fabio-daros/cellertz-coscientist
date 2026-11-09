import reviewData from "./manual-attempt-2LUTUOX4.json";

export const SERVED_PDB_URLS = [
  "/manual-review/attempt.2LUTUOX4/model1.pdb",
  "/manual-review/attempt.2LUTUOX4/model2.pdb",
] as const;

export type ServedPdbUrl = (typeof SERVED_PDB_URLS)[number];

export type RecordedCell =
  | { kind: "recorded"; text: string }
  | { kind: "recorded_zero"; text: string }
  | { kind: "empty_field" }
  | { kind: "absent" };

export type CaAtom = {
  chain: string;
  resSeq: number;
  x: number;
  y: number;
  z: number;
};

const CONTAINED_PDB =
  /^\/api\/attempts\/attempt\.[A-Za-z0-9]+\/structures\/model[1-9][0-9]*\.pdb$/;

export function servedPdbUrl(url: string): boolean {
  if (!url.startsWith("/") || url.includes("..") || url.includes("\\") || url.includes("?")) {
    return false;
  }
  return (SERVED_PDB_URLS as readonly string[]).includes(url) || CONTAINED_PDB.test(url);
}

export function asCell(value: { kind: string; text?: string }): RecordedCell {
  if (value.kind === "recorded" && typeof value.text === "string") {
    return { kind: "recorded", text: value.text };
  }
  if (value.kind === "recorded_zero" && typeof value.text === "string") {
    return { kind: "recorded_zero", text: value.text };
  }
  if (value.kind === "empty_field") {
    return { kind: "empty_field" };
  }
  if (value.kind === "absent") {
    return { kind: "absent" };
  }
  throw new Error(`unknown recorded cell: ${value.kind}`);
}

export function cellLabel(cell: RecordedCell): string {
  if (cell.kind === "recorded") {
    return cell.text;
  }
  if (cell.kind === "recorded_zero") {
    return `${cell.text} (recorded zero)`;
  }
  if (cell.kind === "empty_field") {
    return "empty field";
  }
  return "absent";
}

export function countLabel(value: number): string {
  return value === 0 ? "0 (recorded zero)" : String(value);
}

export function parseCaAtoms(pdb: string): CaAtom[] {
  const seen = new Set<string>();
  const atoms: CaAtom[] = [];
  for (const line of pdb.split(/\r?\n/)) {
    if (!line.startsWith("ATOM") && !line.startsWith("HETATM")) {
      continue;
    }
    if (line.slice(12, 16).trim() !== "CA") {
      continue;
    }
    const chain = line.slice(21, 22).trim();
    const resSeq = Number(line.slice(22, 26));
    const x = Number(line.slice(30, 38));
    const y = Number(line.slice(38, 46));
    const z = Number(line.slice(46, 54));
    if (chain === "" || !Number.isFinite(resSeq) || !Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
      continue;
    }
    const key = `${chain}:${resSeq}:${line.slice(26, 27)}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    atoms.push({ chain, resSeq, x, y, z });
  }
  return atoms;
}

export function caCounts(atoms: CaAtom[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const atom of atoms) {
    counts[atom.chain] = (counts[atom.chain] ?? 0) + 1;
  }
  return counts;
}

export const manualAttemptReview = reviewData;
