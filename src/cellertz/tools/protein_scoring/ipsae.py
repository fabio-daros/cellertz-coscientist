from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class IpSAEConfig:
    pae_cutoff: float
    distance_cutoff: float
    chain_a: str
    chain_b: str
    aggregation: str
    protocol_id: str

    def __post_init__(self) -> None:
        if self.pae_cutoff <= 0:
            raise ValueError("pae_cutoff must be positive.")

        if self.distance_cutoff <= 0:
            raise ValueError("distance_cutoff must be positive.")

        if not self.chain_a or not self.chain_b:
            raise ValueError("Chain identifiers must be non-empty.")

        if self.chain_a == self.chain_b:
            raise ValueError("chain_a and chain_b must be different.")

        if self.aggregation != "max":
            raise ValueError(
                "Only aggregation='max' is currently supported."
            )

        if not self.protocol_id:
            raise ValueError("protocol_id must be non-empty.")


@dataclass(frozen=True)
class IpSAEResult:
    chain_a: str
    chain_b: str
    ipsae_ab: float
    ipsae_ba: float
    ipsae_max: float
    score: float
    pae_cutoff: float
    distance_cutoff: float
    protocol_id: str
    is_probability: bool = False
    recommended_as_hard_filter: bool = False


class ProteinIpSAECalculator:
    """Calculate primary protein-protein ipSAE from PAE evidence.

    The primary ipSAE calculation follows the CARPNN/Dunbrack
    d0-per-residue formulation.

    distance_cutoff is preserved in the configuration/provenance because
    it is part of the reference protocol, but it is not used by the
    primary ipSAE/d0res calculation.
    """

    def calculate(
        self,
        *,
        pae_path: Path,
        structure_path: Path,
        config: IpSAEConfig,
    ) -> IpSAEResult:
        pae = _load_pae(pae_path)
        chains = _load_pdb_ca_chains(structure_path)

        if pae.ndim != 2 or pae.shape[0] != pae.shape[1]:
            raise ValueError(
                f"PAE matrix must be square; got shape {pae.shape}."
            )

        if not np.isfinite(pae).all():
            raise ValueError("PAE matrix contains non-finite values.")

        if pae.shape[0] != len(chains):
            raise ValueError(
                "PAE/residue count mismatch: "
                f"{pae.shape[0]} PAE residues versus "
                f"{len(chains)} PDB CA residues."
            )

        available_chains = set(chains.tolist())

        if config.chain_a not in available_chains:
            raise ValueError(
                f"chain_a {config.chain_a!r} is absent from structure."
            )

        if config.chain_b not in available_chains:
            raise ValueError(
                f"chain_b {config.chain_b!r} is absent from structure."
            )

        ipsae_ab = _directional_ipsae(
            pae=pae,
            chains=chains,
            chain1=config.chain_a,
            chain2=config.chain_b,
            pae_cutoff=config.pae_cutoff,
        )

        ipsae_ba = _directional_ipsae(
            pae=pae,
            chains=chains,
            chain1=config.chain_b,
            chain2=config.chain_a,
            pae_cutoff=config.pae_cutoff,
        )

        ipsae_max = max(ipsae_ab, ipsae_ba)

        return IpSAEResult(
            chain_a=config.chain_a,
            chain_b=config.chain_b,
            ipsae_ab=ipsae_ab,
            ipsae_ba=ipsae_ba,
            ipsae_max=ipsae_max,
            score=ipsae_max,
            pae_cutoff=config.pae_cutoff,
            distance_cutoff=config.distance_cutoff,
            protocol_id=config.protocol_id,
        )


def _load_pae(path: Path) -> np.ndarray:
    with np.load(path, allow_pickle=False) as payload:
        if "pae_angstrom" not in payload.files:
            raise ValueError(
                "PAE artifact is missing 'pae_angstrom'."
            )

        return np.asarray(
            payload["pae_angstrom"],
        )


def _load_pdb_ca_chains(path: Path) -> np.ndarray:
    chains: list[str] = []

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith(("ATOM", "HETATM")):
                continue

            if line[12:16].strip() != "CA":
                continue

            chain = line[21].strip()

            if not chain:
                raise ValueError(
                    "Encountered CA residue without a chain identifier."
                )

            chains.append(chain)

    if not chains:
        raise ValueError(
            "Structure contains no CA residues."
        )

    return np.asarray(chains)


def _calc_d0_array(lengths: np.ndarray) -> np.ndarray:
    lengths = np.asarray(
        lengths,
        dtype=float,
    )

    lengths = np.maximum(
        lengths,
        26.523,
    )

    return (
        1.24
        * (lengths - 15.0) ** (1.0 / 3.0)
        - 1.8
    )


def _directional_ipsae(
    *,
    pae: np.ndarray,
    chains: np.ndarray,
    chain1: str,
    chain2: str,
    pae_cutoff: float,
) -> float:
    chain2_mask = chains == chain2

    valid_pairs_matrix = (
        chain2_mask
        & (pae < pae_cutoff)
    )

    n0res_by_residue = np.sum(
        valid_pairs_matrix,
        axis=1,
    )

    d0_by_residue = _calc_d0_array(
        n0res_by_residue,
    )

    values = np.zeros(
        pae.shape[0],
        dtype=float,
    )

    for index in np.where(chains == chain1)[0]:
        valid_pairs = valid_pairs_matrix[index]

        if not valid_pairs.any():
            continue

        d0 = d0_by_residue[index]

        ptm_row = 1.0 / (
            1.0
            + (pae[index] / d0) ** 2.0
        )

        values[index] = float(
            ptm_row[valid_pairs].mean()
        )

    return float(values.max())
