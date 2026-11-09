from pathlib import Path

from cellertz.schemas import ProteinCandidate


TARGET_CHAIN_ID = "A"
BINDER_CHAIN_ID = "B"

FROZEN_BCMA_MSA = Path("data/processed/boltz2/bcma_target.csv")


def build_boltz2_complex_yaml(
    target_sequence: str,
    binder_sequence: str,
    target_id: str = TARGET_CHAIN_ID,
    binder_id: str = BINDER_CHAIN_ID,
    target_msa: str | Path | None = None,
) -> str:
    if not target_sequence or not binder_sequence:
        raise ValueError("Target and binder sequences must not be empty.")

    if target_msa is None:
        target_msa_block = ""
    else:
        target_msa_block = f"      msa: {Path(target_msa).as_posix()}\n"

    return (
        "version: 1\n"
        "sequences:\n"
        "  - protein:\n"
        f"      id: {target_id}\n"
        f"      sequence: {target_sequence}\n"
        f"{target_msa_block}"
        "  - protein:\n"
        f"      id: {binder_id}\n"
        f"      sequence: {binder_sequence}\n"
        "      msa: empty\n"
    )


def yaml_filename(candidate_id: str) -> str:
    safe_id = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in candidate_id
    )
    return f"{safe_id}.yaml"


def write_boltz2_yaml(
    candidate: ProteinCandidate,
    output_dir: str | Path,
    target_msa: str | Path | None = None,
) -> Path:
    target_sequence = candidate.metadata.get("target_sequence")

    if not target_sequence:
        raise ValueError(
            f"Candidate {candidate.id} is missing target_sequence."
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / yaml_filename(candidate.id)
    path.write_text(
        build_boltz2_complex_yaml(
            target_sequence=str(target_sequence),
            binder_sequence=candidate.sequence,
            target_msa=target_msa,
        )
    )

    return path
