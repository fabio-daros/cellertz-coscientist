from pathlib import Path

import pytest

from cellertz.domains.protein_design.adapters.bindcraft import (
    BindCraftAdapter,
    FixtureBindCraftExecutor,
)
from cellertz.domains.protein_design.adapters.mber import FixtureMBERExecutor, MBERAdapter
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
)
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.domains.protein_design.adapters.rfdiffusion import (
    FixtureRFdiffusionExecutor,
    RFdiffusionAdapter,
)
from cellertz.tools.protein_scoring.scoring import RANKING_METHOD


FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACT_KEYS = {
    "candidate_id",
    "sequence",
    "target_id",
    "generator",
    "generator_metadata",
}


def _agent(generator: str) -> ProteinGenerationAgent:
    if generator == "bindcraft":
        adapter = BindCraftAdapter(
            executor=FixtureBindCraftExecutor(FIXTURES / "bindcraft")
        )
    elif generator == "rfdiffusion":
        adapter = RFdiffusionAdapter(
            executor=FixtureRFdiffusionExecutor(
                FIXTURES / "rfdiffusion" / "sequenced"
            )
        )
    elif generator == "mber":
        adapter = MBERAdapter(
            executor=FixtureMBERExecutor(FIXTURES / "mber")
        )
    else:
        raise AssertionError(generator)
    return ProteinGenerationAgent(adapters={generator: adapter})


def _request(generator: str) -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator=generator,
        n_candidates=3,
        constraints={"length_min": 30, "length_max": 140},
    )


@pytest.mark.parametrize(
    "generator",
    ("bindcraft", "rfdiffusion", "mber"),
)
def test_three_generators_share_the_same_supervisor_path(
    generator,
    tmp_path,
):
    supervisor = ProteinSupervisorV1(
        request=_request(generator),
        artifact_dir=tmp_path,
        run_id=f"protein_run_{generator}_shared",
        generation_agent=_agent(generator),
    )
    run = supervisor.run()
    generation = supervisor.generation_result

    assert run.generation.status == "complete"
    assert run.scoring.status == "complete"
    assert run.ranking.status == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"
    assert generation is not None
    assert generation.adapter_name == generator
    assert len(generation.candidates) == 3
    for candidate in generation.candidates:
        assert set(candidate.to_dict()) == CONTRACT_KEYS
        assert candidate.generator == generator
        assert candidate.sequence
        assert "ranking_score" not in candidate.to_dict()
    for record in supervisor.ranking_records:
        assert record.ranking_signal == RANKING_METHOD
        assert record.ranking_score == record.structural_evidence["ipsae"]
        assert record.is_probability is False


def test_supervisor_does_not_branch_on_generator():
    source = Path(
        "src/cellertz/domains/protein_design/agents/supervisor.py"
    ).read_text()

    assert "if generator" not in source
    assert "generator ==" not in source
    assert "request.generator" not in source
    assert "BindCraftAdapter" not in source
    assert "RFdiffusionAdapter" not in source
    assert "MBERAdapter" not in source
