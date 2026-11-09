import shutil
from pathlib import Path

import pytest

from cellertz.domains.protein_design.agents.generation import (
    NormalizedCandidate,
)
from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
    BindCraftArtifactResolver,
    BindCraftIpSAEEvidenceProvider,
)
from cellertz.tools.protein_scoring.ipsae import IpSAEConfig

FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "ipsae_gpu4"
)

DESIGN = "PDL1_l65_s985772_mpnn1"
EXECUTION_ID = "exec_pdl1_smoke_gpu4"


def _candidate() -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence="SSLFEKIVKWAMEFTVAMKEYMEKNPELTDEQIRTIQKEVMRRFEAKDFGDPRKLIEEALAKGPP",
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "design": DESIGN,
            "provenance": {
                "execution": {
                    "execution_id": EXECUTION_ID,
                },
            },
        },
    )


def _materialize_execution_layout(root: Path) -> None:
    execution_dir = (
        root
        / EXECUTION_ID
        / "designs"
    )

    pdb_dir = (
        execution_dir
        / "MPNN"
        / "Relaxed"
    )
    pae_dir = (
        execution_dir
        / "CellertzEvidence"
        / "PAE"
    )

    pdb_dir.mkdir(parents=True)
    pae_dir.mkdir(parents=True)

    for model in (1, 2):
        shutil.copyfile(
            FIXTURE_DIR
            / f"{DESIGN}_model{model}.pdb",
            pdb_dir
            / f"{DESIGN}_model{model}.pdb",
        )

        shutil.copyfile(
            FIXTURE_DIR
            / f"{DESIGN}_model{model}_pae.npz",
            pae_dir
            / f"{DESIGN}_model{model}_pae.npz",
        )


def _ipsae_config() -> IpSAEConfig:
    return IpSAEConfig(
        pae_cutoff=10.0,
        distance_cutoff=10.0,
        chain_a="A",
        chain_b="B",
        aggregation="max",
        protocol_id="CARPNN_2026_REFERENCE",
    )


def test_bindcraft_ipsae_evidence_matches_gpu4_ensemble(tmp_path):
    _materialize_execution_layout(tmp_path)

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )
    provider = BindCraftIpSAEEvidenceProvider(
        resolver=resolver,
        ipsae_config=_ipsae_config(),
    )

    result = provider.evaluate(_candidate())

    assert result.candidate_id == f"bindcraft_{DESIGN}"
    assert result.design == DESIGN
    assert result.execution_id == EXECUTION_ID

    assert result.n_models == 2
    assert [item.model_number for item in result.models] == [1, 2]

    assert round(result.models[0].ipsae, 6) == 0.010173
    assert round(result.models[1].ipsae, 6) == 0.016941

    assert round(result.ipsae_mean, 6) == 0.013557
    assert round(result.ipsae_min, 6) == 0.010173
    assert round(result.ipsae_max, 6) == 0.016941

    assert result.model_aggregation == "mean"
    assert result.ranking_value == pytest.approx(
        result.ipsae_mean
    )

    assert result.protocol_id == "CARPNN_2026_REFERENCE"


def test_bindcraft_ipsae_evidence_preserves_artifact_hashes(tmp_path):
    _materialize_execution_layout(tmp_path)

    provider = BindCraftIpSAEEvidenceProvider(
        resolver=BindCraftArtifactResolver(
            output_root=tmp_path,
        ),
        ipsae_config=_ipsae_config(),
    )

    result = provider.evaluate(_candidate())

    assert result.models[0].pae_sha256 == (
        "dbf52bfd7910b49e5c87ec8569965be9"
        "a56ad53d4c65497584afbebd34b4e2a2"
    )
    assert result.models[0].pdb_sha256 == (
        "3b2363eb9bf50840926db0af8acb2abf"
        "d170680d1f51bb8c4b5e75c9c5491a4d"
    )

    assert result.models[1].pae_sha256 == (
        "7b14c58cee82945883adc1e5e5e17e13"
        "dd11a7672b52d9decfa5467a510ce9a2"
    )
    assert result.models[1].pdb_sha256 == (
        "8fef359434a2fbdd7dc13f817295c586"
        "3b67484d830c097bf8f41fc915cfef9b"
    )



def test_bindcraft_artifact_resolver_rejects_wrong_generator(tmp_path):
    candidate = NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence="AAA",
        target_id="PDL1",
        generator="rfdiffusion",
        generator_metadata={
            "design": DESIGN,
            "provenance": {
                "execution": {
                    "execution_id": EXECUTION_ID,
                },
            },
        },
    )

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        ValueError,
        match="requires generator='bindcraft'",
    ):
        resolver.resolve(candidate)


def test_bindcraft_artifact_resolver_requires_design(tmp_path):
    candidate = NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence="AAA",
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "provenance": {
                "execution": {
                    "execution_id": EXECUTION_ID,
                },
            },
        },
    )

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        ValueError,
        match="missing generator_metadata.design",
    ):
        resolver.resolve(candidate)


def test_bindcraft_artifact_resolver_requires_execution_id(tmp_path):
    candidate = NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence="AAA",
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "design": DESIGN,
            "provenance": {
                "execution": {},
            },
        },
    )

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        ValueError,
        match="missing execution_id",
    ):
        resolver.resolve(candidate)


def test_bindcraft_artifact_resolver_requires_real_artifacts(tmp_path):
    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        FileNotFoundError,
        match="No relaxed PDB models found",
    ):
        resolver.resolve(_candidate())


def test_bindcraft_artifact_resolver_rejects_model_set_mismatch(
    tmp_path,
):
    _materialize_execution_layout(tmp_path)

    missing_pae = (
        tmp_path
        / EXECUTION_ID
        / "designs"
        / "CellertzEvidence"
        / "PAE"
        / f"{DESIGN}_model2_pae.npz"
    )
    missing_pae.unlink()

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        ValueError,
        match="PDB and PAE model sets do not match",
    ):
        resolver.resolve(_candidate())


def test_bindcraft_artifact_resolver_orders_models_numerically(
    tmp_path,
):
    _materialize_execution_layout(tmp_path)

    execution_dir = (
        tmp_path
        / EXECUTION_ID
        / "designs"
    )

    pdb_dir = execution_dir / "MPNN" / "Relaxed"
    pae_dir = execution_dir / "CellertzEvidence" / "PAE"

    shutil.copyfile(
        FIXTURE_DIR / f"{DESIGN}_model1.pdb",
        pdb_dir / f"{DESIGN}_model10.pdb",
    )
    shutil.copyfile(
        FIXTURE_DIR / f"{DESIGN}_model1_pae.npz",
        pae_dir / f"{DESIGN}_model10_pae.npz",
    )

    resolved = BindCraftArtifactResolver(
        output_root=tmp_path,
    ).resolve(_candidate())

    assert [
        item.model_number
        for item in resolved.models
    ] == [1, 2, 10]


def test_bindcraft_artifact_resolver_rejects_invalid_provenance_type(
    tmp_path,
):
    candidate = NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence="AAA",
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "design": DESIGN,
            "provenance": "not-a-dict",
        },
    )

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        TypeError,
        match="provenance must be a mapping",
    ):
        resolver.resolve(candidate)


def test_bindcraft_artifact_resolver_rejects_invalid_execution_type(
    tmp_path,
):
    candidate = NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence="AAA",
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "design": DESIGN,
            "provenance": {
                "execution": "not-a-dict",
            },
        },
    )

    resolver = BindCraftArtifactResolver(
        output_root=tmp_path,
    )

    with pytest.raises(
        TypeError,
        match="execution provenance must be a mapping",
    ):
        resolver.resolve(candidate)
