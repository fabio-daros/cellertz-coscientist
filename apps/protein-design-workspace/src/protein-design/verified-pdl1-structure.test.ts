import { describe, expect, it } from "vitest";
import { validateTargetBindingResidues } from "./verified-pdl1-structure";

describe("verified PDL1 residue numbers", () => {
  it("accepts the PDB number and rejects a number the file does not contain", () => {
    expect(validateTargetBindingResidues("")).toEqual({ status: "empty" });
    expect(validateTargetBindingResidues("A56")).toEqual({ status: "valid" });
    expect(validateTargetBindingResidues("56")).toEqual({ status: "valid" });
    expect(validateTargetBindingResidues("18-20")).toEqual({ status: "valid" });
    const missing = validateTargetBindingResidues("1");
    expect(missing.status).toBe("invalid");
    if (missing.status === "invalid") {
      expect(missing.message).toContain("A1");
      expect(missing.message).toContain("18 through 132");
    }
  });
});
