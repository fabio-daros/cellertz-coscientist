from pathlib import Path

from cellertz.evaluation.datasets.bcma_features import PRIMARY_LABEL
from cellertz.tools.protein_design.chow2026 import (
    load_chow2026_candidates,
)
from cellertz.tools.protein_scoring.boltz2_yaml import (
    FROZEN_BCMA_MSA,
    write_boltz2_yaml,
)


DATASET = (
    "data/external/chow_2026/"
    "41551_2026_1790_MOESM5_ESM.xlsx"
)

OUTPUT_DIR = Path("outputs/boltz2_yamls")


candidates = load_chow2026_candidates(DATASET)

bcma = [
    candidate
    for candidate in candidates
    if candidate.target == "BCMA"
    and PRIMARY_LABEL in candidate.experimental_labels
    and candidate.metadata.get("target_sequence")
    and candidate.sequence
]

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

written = [
    write_boltz2_yaml(
        candidate,
        OUTPUT_DIR,
        target_msa=FROZEN_BCMA_MSA,
    )
    for candidate in bcma
]

print("=== BOLTZ2 YAML GENERATION ===")
print(f"Candidates: {len(bcma)}")
print(f"YAML files: {len(written)}")
print(f"Output: {OUTPUT_DIR}")
print(
    "Inputs are protein-protein complexes "
    "(chain A = BCMA target, chain B = binder)."
)
print(
    "Chain A (BCMA) uses the frozen MSA "
    f"{FROZEN_BCMA_MSA}."
)
print(
    "Chain B (de novo binder) uses msa: empty "
    "(single-sequence mode)."
)
