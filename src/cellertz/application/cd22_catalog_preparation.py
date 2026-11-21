"""The CD22 target, prepared for the catalog, and why three more steps ran.

``cd22_operational_reference()`` records four steps: remove solvent, extract
chain U from PDB 7O52, relabel it to A, set its segid. Running exactly those
leaves **30 heteroatoms** in the result — one glycerol, one N-acetylglucosamine
and two sulfates, numbered 701 to 704 — because the recorded preparation never
removed heteroatoms.

That is not a target the generator can use. AlphaFold2 and ColabDesign, which
are mBER's trajectory and evaluation engine, model protein; a ``HETATM`` is
outside their vocabulary. And the one target this platform has already
verified, the professor's PD-L1 input, is 921 ``ATOM`` records and **zero**
heteroatoms. So the removal below is a format requirement of the generator,
not a scientific opinion about CD22.

The recorded specification is left alone. Editing it would change what the
record says someone did; these three steps are declared here instead, and
``preparation_manifest`` writes the whole executed sequence beside the file.

**What the professor should see.** Measured on the prepared structure, nearest
heavy atom to any of the 24 recorded epitope residues:

| species | atoms | nearest epitope atom |
| --- | --- | --- |
| SO4 | 10 | **2.7 A** |
| NAG | 14 | **4.1 A** |
| GOL | 6 | 10.2 A |

Two of those are not incidental. A crystallisation sulfate sitting 2.7 A from
the epitope is inside hydrogen-bonding distance of it, which bears on how that
24-residue list was derived from contacts in the first place. And the glycan
at 4.1 A is real: CD22 carries it in vivo, so a binder aimed at this face has
to contend with occlusion that the stripped structure no longer shows. The
occlusion pass in ``attempt_evidence`` is what would measure that, against a
declared ligand footprint, once someone declares the glycan as one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cellertz.application.target_preparation import (
    preparation_manifest,
    prepare_from_specification,
)
from cellertz.application.vhh_campaign import (
    ScientificProvenance,
    TargetPreparationStep,
)
from cellertz.application.vhh_campaign_fixtures import cd22_operational_reference

CATALOG_TARGET_FILENAME = "target.pdb"
MANIFEST_FILENAME = "preparation.json"
SOURCE_SHA256 = "a25c467de71c4492bc8d3261b304f5ca893e13f04a77eaa0019fdeeb2976a50c"
# Measured on the structure produced by the recorded four steps alone.
EPITOPE_PROXIMITY_ANGSTROM = {"SO4": 2.7, "NAG": 4.1, "GOL": 10.2}
REMOVED_HETEROATOMS = ("GOL", "NAG", "SO4")
# Why no preparation of 7O52 produces a campaign target. The atlas raises this
# itself, as an open question on its own CD22 panel, and ties it to the two
# campaigns it had already invalidated.
FRAGMENT_CAVEAT = {
    "source": "PDB:7O52",
    "contains": "CD22 domains D6-D7",
    "ectodomain_domains": 7,
    "reason": (
        "7O52 holds two of CD22's seven ectodomain domains. D5 sits above D6 "
        "in the intact receptor and the consensus footprint reaches the "
        "membrane-proximal 667-679, so faces that are covered on a B cell are "
        "bare in this file. That is the defect that invalidated the atlas's "
        "HSA campaign B and its CD19 campaigns: a surface exposed in the "
        "input and occupied in vivo, which the designs then took. No "
        "preparation fixes it, because the domains are absent from the "
        "crystal rather than cut away by us. The atlas says answering it "
        "needs a full-length CD22 model."
    ),
    "atlas_lesson": "Never hand-truncate on sequence. mBER already truncates on distance.",
    "usable_for": "measurement against the m971 epitope on D6-D7, not a campaign",
}


def _provenance() -> ScientificProvenance:
    return ScientificProvenance(
        "operational_reference",
        "Heteroatom removal added for the catalog target: AlphaFold2 and "
        "ColabDesign model protein only, and the platform's verified PD-L1 "
        "input carries no heteroatoms. Not part of the recorded CD22 "
        "preparation. The glycan removed here sits 4.1 A from the recorded "
        "epitope and is present in vivo.",
        ("PDB:7O52",),
    )


def catalog_steps() -> tuple[TargetPreparationStep, ...]:
    """The three removals, one per species, so the record names each one."""

    provenance = _provenance()
    return tuple(
        TargetPreparationStep(
            index,
            "remove_selected_heteroatoms",
            (("resname", resname),),
            "pdb:7O52-chain-a",
            "pdb:7O52-chain-a-protein",
            provenance,
        )
        for index, resname in enumerate(REMOVED_HETEROATOMS, start=1)
    )


class FragmentRefused(Exception):
    """The source is a fragment, so this target cannot carry a campaign."""


def write_catalog_target(
    source_pdb: str, out_dir: Path, allow_fragment: bool = False
) -> dict[str, object]:
    """Prepare and write ``target.pdb`` plus the manifest of what ran.

    Refuses by default. 7O52 is two domains of seven, and the atlas's own
    rule — "Never hand-truncate on sequence. mBER already truncates on
    distance." — cannot be satisfied by any preparation of it, because the
    missing domains are missing from the crystal rather than cut by us.
    ``allow_fragment=True`` is how a caller says it wants the file anyway,
    and the manifest then records that it is not campaign material.
    """

    specification = cd22_operational_reference()
    extra = catalog_steps()
    prepared = prepare_from_specification(specification, source_pdb, extra)
    if prepared.heteroatom_residues:
        raise AssertionError(
            f"heteroatoms survived: {prepared.heteroatom_residues}"
        )
    if not allow_fragment:
        raise FragmentRefused(FRAGMENT_CAVEAT["reason"])
    manifest = preparation_manifest(specification, source_pdb, prepared, extra)
    manifest["epitope_proximity_angstrom"] = EPITOPE_PROXIMITY_ANGSTROM
    manifest["source_pdb_sha256_expected"] = SOURCE_SHA256
    manifest["campaign_usable"] = False
    manifest["fragment"] = dict(FRAGMENT_CAVEAT)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CATALOG_TARGET_FILENAME).write_text(prepared.pdb, encoding="utf-8")
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) not in {2, 3} or (len(arguments) == 3 and arguments[2] != "--allow-fragment"):
        print(
            "usage: cd22_catalog_preparation <7O52.pdb> <catalog-dir> [--allow-fragment]\n"
            f"\n{FRAGMENT_CAVEAT['reason']}",
            file=sys.stderr,
        )
        return 2
    source = Path(arguments[0]).read_text(encoding="utf-8")
    try:
        manifest = write_catalog_target(
            source, Path(arguments[1]), allow_fragment=len(arguments) == 3
        )
    except FragmentRefused as refused:
        print(f"refused: {refused}", file=sys.stderr)
        print("pass --allow-fragment to write it anyway, marked unusable.", file=sys.stderr)
        return 1
    if manifest["source_sha256"] != SOURCE_SHA256:
        print(
            f"warning: source digest {manifest['source_sha256']} is not the "
            f"recorded {SOURCE_SHA256}",
            file=sys.stderr,
        )
    print(json.dumps(manifest["result"], indent=2, sort_keys=True))
    print(f"prepared_sha256 {manifest['prepared_sha256']}")
    print(f"campaign_usable {manifest['campaign_usable']} — {FRAGMENT_CAVEAT['usable_for']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
