import json
from pathlib import Path

import numpy as np
import pytest

from cellertz.tools.protein_scoring.ipsae import (
    IpSAEConfig,
    ProteinIpSAECalculator,
)

FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "ipsae_gpu4"
)


def _manifest() -> dict:
    return json.loads(
        (
            FIXTURE_DIR
            / "reference_manifest.json"
        ).read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("model_name", "expected_ab", "expected_ba", "expected_max"),
    [
        ("model1", 0.010173, 0.000000, 0.010173),
        ("model2", 0.014648, 0.016941, 0.016941),
    ],
)
def test_ipsae_matches_carpnn_gpu4_golden(
    model_name,
    expected_ab,
    expected_ba,
    expected_max,
):
    manifest = _manifest()
    protocol = manifest["protocol"]
    model = manifest["models"][model_name]

    config = IpSAEConfig(
        pae_cutoff=protocol["pae_cutoff"],
        distance_cutoff=protocol["distance_cutoff"],
        chain_a=protocol["chain_a"],
        chain_b=protocol["chain_b"],
        aggregation=protocol["aggregation"],
        protocol_id=protocol["protocol_id"],
    )

    result = ProteinIpSAECalculator().calculate(
        pae_path=FIXTURE_DIR / model["pae_file"],
        structure_path=FIXTURE_DIR / model["pdb_file"],
        config=config,
    )

    assert result.chain_a == "A"
    assert result.chain_b == "B"

    assert round(result.ipsae_ab, 6) == expected_ab
    assert round(result.ipsae_ba, 6) == expected_ba
    assert round(result.ipsae_max, 6) == expected_max

    assert result.score == result.ipsae_max

    assert result.pae_cutoff == 10.0
    assert result.distance_cutoff == 10.0
    assert result.protocol_id == "CARPNN_2026_REFERENCE"

    assert result.is_probability is False
    assert result.recommended_as_hard_filter is False



def _config(
    *,
    pae_cutoff: float = 10.0,
    distance_cutoff: float = 10.0,
    chain_a: str = "A",
    chain_b: str = "B",
) -> IpSAEConfig:
    return IpSAEConfig(
        pae_cutoff=pae_cutoff,
        distance_cutoff=distance_cutoff,
        chain_a=chain_a,
        chain_b=chain_b,
        aggregation="max",
        protocol_id="TEST",
    )


def _write_two_chain_pdb(path: Path) -> None:
    path.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C\n"
        "ATOM      2  CA  GLY B   1       1.000   0.000   0.000  1.00 90.00           C\n",
        encoding="utf-8",
    )


def test_ipsae_uses_strict_pae_cutoff(tmp_path):
    pae_path = tmp_path / "pae.npz"
    pdb_path = tmp_path / "complex.pdb"

    np.savez_compressed(
        pae_path,
        pae_angstrom=np.array(
            [
                [0.0, 9.0],
                [10.0, 0.0],
            ],
            dtype=np.float32,
        ),
    )
    _write_two_chain_pdb(pdb_path)

    result = ProteinIpSAECalculator().calculate(
        pae_path=pae_path,
        structure_path=pdb_path,
        config=_config(),
    )

    reference_d0 = (
        1.24 * (26.523 - 15.0) ** (1.0 / 3.0)
        - 1.8
    )
    expected_ab = 1.0 / (
        1.0 + (9.0 / reference_d0) ** 2.0
    )

    assert result.ipsae_ab == pytest.approx(expected_ab)
    assert result.ipsae_ba == 0.0
    assert result.ipsae_max == pytest.approx(expected_ab)


def test_primary_ipsae_is_independent_of_distance_cutoff():
    manifest = _manifest()
    model = manifest["models"]["model1"]

    calculator = ProteinIpSAECalculator()

    low_distance = calculator.calculate(
        pae_path=FIXTURE_DIR / model["pae_file"],
        structure_path=FIXTURE_DIR / model["pdb_file"],
        config=_config(distance_cutoff=5.0),
    )

    high_distance = calculator.calculate(
        pae_path=FIXTURE_DIR / model["pae_file"],
        structure_path=FIXTURE_DIR / model["pdb_file"],
        config=_config(distance_cutoff=30.0),
    )

    assert low_distance.ipsae_ab == high_distance.ipsae_ab
    assert low_distance.ipsae_ba == high_distance.ipsae_ba
    assert low_distance.ipsae_max == high_distance.ipsae_max


def test_ipsae_rejects_missing_pae_angstrom(tmp_path):
    pae_path = tmp_path / "bad.npz"

    np.savez_compressed(
        pae_path,
        wrong_key=np.zeros((2, 2), dtype=np.float32),
    )

    model = _manifest()["models"]["model1"]

    with pytest.raises(
        ValueError,
        match="missing 'pae_angstrom'",
    ):
        ProteinIpSAECalculator().calculate(
            pae_path=pae_path,
            structure_path=FIXTURE_DIR / model["pdb_file"],
            config=_config(),
        )


def test_ipsae_rejects_pae_structure_size_mismatch(tmp_path):
    pae_path = tmp_path / "bad_size.npz"

    np.savez_compressed(
        pae_path,
        pae_angstrom=np.zeros((2, 2), dtype=np.float32),
    )

    model = _manifest()["models"]["model1"]

    with pytest.raises(
        ValueError,
        match="PAE/residue count mismatch",
    ):
        ProteinIpSAECalculator().calculate(
            pae_path=pae_path,
            structure_path=FIXTURE_DIR / model["pdb_file"],
            config=_config(),
        )


def test_ipsae_rejects_missing_requested_chain():
    model = _manifest()["models"]["model1"]

    with pytest.raises(
        ValueError,
        match="chain_b 'Z' is absent",
    ):
        ProteinIpSAECalculator().calculate(
            pae_path=FIXTURE_DIR / model["pae_file"],
            structure_path=FIXTURE_DIR / model["pdb_file"],
            config=_config(chain_b="Z"),
        )


def test_ipsae_rejects_nonfinite_pae(tmp_path):
    pae_path = tmp_path / "nonfinite.npz"
    pdb_path = tmp_path / "complex.pdb"

    np.savez_compressed(
        pae_path,
        pae_angstrom=np.array(
            [
                [0.0, np.nan],
                [1.0, 0.0],
            ],
            dtype=np.float32,
        ),
    )
    _write_two_chain_pdb(pdb_path)

    with pytest.raises(
        ValueError,
        match="non-finite",
    ):
        ProteinIpSAECalculator().calculate(
            pae_path=pae_path,
            structure_path=pdb_path,
            config=_config(),
        )


def test_ipsae_rejects_nonsquare_pae(tmp_path):
    pae_path = tmp_path / "nonsquare.npz"
    pdb_path = tmp_path / "complex.pdb"

    np.savez_compressed(
        pae_path,
        pae_angstrom=np.zeros((2, 3), dtype=np.float32),
    )
    _write_two_chain_pdb(pdb_path)

    with pytest.raises(
        ValueError,
        match="must be square",
    ):
        ProteinIpSAECalculator().calculate(
            pae_path=pae_path,
            structure_path=pdb_path,
            config=_config(),
        )
