/** Residue numbers written in the verified PDL1 PDB, chain A. */

export const VERIFIED_PDL1_CHAIN = "A";
export const VERIFIED_PDL1_FIRST_RESIDUE = 18;
export const VERIFIED_PDL1_LAST_RESIDUE = 132;

const NUMBERING =
  "Verified PDB chain A. Residue numbers are the numbers written in that file, from 18 through 132. The file does not map them to a reference protein.";

export function verifiedPdl1Numbering(): string {
  return NUMBERING;
}

export function validateTargetBindingResidues(
  raw: string,
): { status: "empty" } | { status: "valid" } | { status: "invalid"; message: string } {
  const text = raw.trim();
  if (text === "") {
    return { status: "empty" };
  }
  const missing: string[] = [];
  for (const part of text.split(",")) {
    const token = part.trim();
    const parsed = parseToken(token);
    if (parsed === null) {
      return {
        status: "invalid",
        message:
          "The residue list could not be read. Use the numbers written in the verified PDB, such as A20 or 18-20.",
      };
    }
    missing.push(...parsed.filter((item) => !residueExists(item.chain, item.residue)).map(formatResidue));
  }
  if (missing.length > 0) {
    return {
      status: "invalid",
      message: `${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} not in chain A of the verified PDB. ${NUMBERING}`,
    };
  }
  return { status: "valid" };
}

function residueExists(chain: string, residue: number): boolean {
  return (
    chain === VERIFIED_PDL1_CHAIN
    && Number.isInteger(residue)
    && residue >= VERIFIED_PDL1_FIRST_RESIDUE
    && residue <= VERIFIED_PDL1_LAST_RESIDUE
  );
}

function formatResidue(item: { chain: string; residue: number }): string {
  return `${item.chain}${item.residue}`;
}

function parseToken(token: string): { chain: string; residue: number }[] | null {
  if (token === "") {
    return null;
  }
  const range = token.split("-");
  if (range.length > 2) {
    return null;
  }
  const start = parseEndpoint(range[0], true);
  if (start === null) {
    return null;
  }
  if (start.wholeChain) {
    return Array.from(
      { length: VERIFIED_PDL1_LAST_RESIDUE - VERIFIED_PDL1_FIRST_RESIDUE + 1 },
      (_, index) => ({ chain: start.chain, residue: VERIFIED_PDL1_FIRST_RESIDUE + index }),
    );
  }
  const end = range.length === 2 ? parseEndpoint(range[1], false) : start;
  if (end === null || end.wholeChain || end.chain !== start.chain || end.residue < start.residue) {
    return null;
  }
  return Array.from({ length: end.residue - start.residue + 1 }, (_, index) => ({
    chain: start.chain,
    residue: start.residue + index,
  }));
}

function parseEndpoint(
  token: string,
  allowWholeChain: boolean,
): { chain: string; residue: number; wholeChain: boolean } | null {
  if (/^[A-Za-z]$/.test(token)) {
    return allowWholeChain ? { chain: token.toUpperCase(), residue: 0, wholeChain: true } : null;
  }
  const match = /^([A-Za-z])?(\d+)$/.exec(token);
  if (match === null) {
    return null;
  }
  return {
    chain: (match[1] ?? VERIFIED_PDL1_CHAIN).toUpperCase(),
    residue: Number(match[2]),
    wholeChain: false,
  };
}
