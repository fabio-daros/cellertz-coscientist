from cellertz.domains.protein_design.agents.generation import (
    FixtureGeneratorAdapter,
    GenerationRequest,
    GenerationResult,
    NormalizedCandidate,
    ProteinGenerationAgent,
    request_input_hash,
)


def _request(
    n_candidates: int = 10,
    generator: str = "fixture",
    constraints: dict | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator=generator,
        n_candidates=n_candidates,
        constraints=constraints or {
            "length_min": 50,
            "length_max": 100,
        },
    )


def test_same_request_is_deterministic():
    agent = ProteinGenerationAgent()
    request = _request()

    first = agent.generate(request)
    second = agent.generate(request)

    assert first.status == "complete"
    assert second.status == "complete"
    assert first.input_hash == second.input_hash
    assert first.input_hash == request_input_hash(request)
    assert [
        candidate.to_dict()
        for candidate in first.candidates
    ] == [
        candidate.to_dict()
        for candidate in second.candidates
    ]


def test_candidate_order_is_deterministic():
    first = ProteinGenerationAgent().generate(_request(3))
    second = ProteinGenerationAgent().generate(_request(3))

    assert [
        candidate.candidate_id
        for candidate in first.candidates
    ] == [
        candidate.candidate_id
        for candidate in second.candidates
    ]


def test_n_candidates_is_exact():
    result = ProteinGenerationAgent().generate(_request(10))

    assert len(result.candidates) == 10


def test_candidate_ids_are_unique_and_deterministic():
    result = ProteinGenerationAgent().generate(_request(10))
    ids = [
        candidate.candidate_id
        for candidate in result.candidates
    ]
    digest = request_input_hash(_request(10))[:12]

    assert len(ids) == len(set(ids))
    assert ids == [
        f"fixture_{digest}_{index:03d}"
        for index in range(10)
    ]


def test_candidates_match_normalized_schema():
    result = ProteinGenerationAgent().generate(_request(2))
    expected = {
        "candidate_id",
        "sequence",
        "target_id",
        "generator",
        "generator_metadata",
    }

    for candidate in result.candidates:
        assert isinstance(candidate, NormalizedCandidate)
        assert set(candidate.to_dict()) == expected
        assert candidate.target_id == "BCMA"
        assert candidate.generator == "fixture"
        assert candidate.sequence
        assert "adapter" in candidate.generator_metadata


def test_adapter_metadata_does_not_enter_common_contract():
    result = ProteinGenerationAgent().generate(_request(1))
    payload = result.candidates[0].to_dict()

    assert "adapter_version" in payload["generator_metadata"]
    assert "ranking_score" not in payload
    assert "rank" not in payload
    assert "selected" not in payload
    assert "critic" not in payload


def test_unknown_generator_fails_explicitly():
    result = ProteinGenerationAgent().generate(
        _request(generator="unknown_generator")
    )

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.error is not None
    assert "Unknown generator" in result.error
    assert "unknown_generator" in result.error


def test_duplicate_candidate_ids_are_rejected():
    class DuplicateAdapter:
        name = "dup"
        version = "v1"

        def generate(self, request):
            candidate = NormalizedCandidate(
                candidate_id="same",
                sequence="ACDE",
                target_id=request.target_id,
                generator=self.name,
            )
            return [candidate, candidate]

    agent = ProteinGenerationAgent(
        adapters={"dup": DuplicateAdapter()}
    )
    result = agent.generate(_request(2, generator="dup"))

    assert result.status == "failed"
    assert result.candidates == ()
    assert "duplicate candidate_id" in result.error.lower()


def test_invalid_candidate_is_not_silently_accepted():
    class EmptySequenceAdapter:
        name = "empty"
        version = "v1"

        def generate(self, request):
            return [
                NormalizedCandidate(
                    candidate_id="empty_000",
                    sequence="",
                    target_id=request.target_id,
                    generator=self.name,
                )
            ]

    agent = ProteinGenerationAgent(
        adapters={"empty": EmptySequenceAdapter()}
    )
    result = agent.generate(_request(1, generator="empty"))

    assert result.status == "failed"
    assert result.candidates == ()
    assert "sequence" in result.error.lower()


def test_generation_does_not_add_scoring_or_selection():
    result = ProteinGenerationAgent().generate(_request(2))
    payload = result.to_dict()

    assert isinstance(result, GenerationResult)
    assert "ranking_score" not in payload
    assert "rank" not in payload
    assert "selected" not in payload
    assert "critic" not in payload
    assert not hasattr(result.candidates[0], "ranking_score")
    assert not hasattr(result.candidates[0], "rank")
    assert not hasattr(result.candidates[0], "selected")


def test_two_adapters_share_the_same_downstream_contract():
    adapter_a = FixtureGeneratorAdapter(
        name="fixture_a",
        version="a1",
    )
    adapter_b = FixtureGeneratorAdapter(
        name="fixture_b",
        version="b1",
    )
    agent = ProteinGenerationAgent(
        adapters={
            "fixture_a": adapter_a,
            "fixture_b": adapter_b,
        }
    )
    result_a = agent.generate(
        _request(2, generator="fixture_a")
    )
    result_b = agent.generate(
        _request(2, generator="fixture_b")
    )

    keys_a = set(result_a.candidates[0].to_dict())
    keys_b = set(result_b.candidates[0].to_dict())

    assert result_a.status == "complete"
    assert result_b.status == "complete"
    assert keys_a == keys_b
    assert keys_a == {
        "candidate_id",
        "sequence",
        "target_id",
        "generator",
        "generator_metadata",
    }
    assert result_a.candidates[0].generator == "fixture_a"
    assert result_b.candidates[0].generator == "fixture_b"
    assert result_a.adapter_name == "fixture_a"
    assert result_b.adapter_name == "fixture_b"
