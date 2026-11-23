"""Where the complex comes from, and what happens when it does not come.

The registered ``target.pdb`` is the prepared target: one chain, no partner.
Both roots on this server hold exactly that, so the partner a block has to be
classified against is not in the file the campaign runs on. The complex is read
from ``complex.pdb`` beside it and is deliberately not part of the input
identity, because mBER never opens it and the manifest digest is what makes a
run reproducible.
"""

from __future__ import annotations

import hashlib

import pytest

from cellertz.application.generation_plan import open_registered_input_catalog
from cellertz.application.mber_demonstration import MberDemonstrationLauncher
from cellertz.application.plan_configuration_suggestion import _partner_measurement

TARGET = (
    "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
    "ATOM      2  CA  ALA A   2       0.000   0.000  10.000  1.00  0.00           C\n"
)
COMPLEX = (
    "ATOM      1  CA  ALA U   1       0.000   0.000   0.000  1.00  0.00           C\n"
    "ATOM      2  CA  ALA U   2       0.000   0.000  10.000  1.00  0.00           C\n"
    "ATOM      3  CA  ALA H  50       3.000   0.000   0.000  1.00  0.00           C\n"
)
PREPARED = {("A", 1), ("A", 2)}
DECLARED = {
    "target_chain": "U",
    "partner_chains": "H",
    "partner_direction": "avoid",
}


def _root(tmp_path, *, complex_pdb: str | None = COMPLEX):
    root = tmp_path / "inputs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "target.pdb").write_text(TARGET, encoding="utf-8")
    if complex_pdb is not None:
        (root / "complex.pdb").write_text(complex_pdb, encoding="utf-8")
    return root


def _measure(monkeypatch, root, request=None):
    monkeypatch.setenv("CELLERTZ_PDL1_INPUT_ROOT", str(root))
    return _partner_measurement(DECLARED if request is None else request, PREPARED)


def test_the_complex_beside_the_target_carries_the_partner(monkeypatch, tmp_path) -> None:
    found = _measure(monkeypatch, _root(tmp_path))

    assert found["status"] == "measured"
    assert found["contact_residues"] == [1]
    assert found["complex_sha256"] == hashlib.sha256(COMPLEX.encode("utf-8")).hexdigest()
    assert found["declared"] == DECLARED


def test_a_root_without_a_complex_says_so_rather_than_measuring_the_target_alone(
    monkeypatch, tmp_path
) -> None:
    """Both roots on this server are in this state, so it is the normal reply."""

    found = _measure(monkeypatch, _root(tmp_path, complex_pdb=None))

    assert found["status"] == "complex_not_registered"
    assert "no complex.pdb" in found["limitation"]


def test_an_undeclared_complex_is_not_guessed_at(monkeypatch, tmp_path) -> None:
    for request in (
        {},
        {"target_chain": "U"},
        {"partner_chains": "H", "partner_direction": "avoid"},
    ):
        found = _measure(monkeypatch, _root(tmp_path), request)
        assert found["status"] == "partner_not_declared", request


def test_a_complex_that_is_not_this_target_s_source_is_refused(monkeypatch, tmp_path) -> None:
    """A preparation only removes, so the prepared numbers must all be in the source."""

    short = COMPLEX.replace(
        "ATOM      2  CA  ALA U   2       0.000   0.000  10.000  1.00  0.00           C\n", ""
    )

    found = _measure(monkeypatch, _root(tmp_path, complex_pdb=short))

    assert found["status"] == "complex_does_not_contain_the_prepared_target"
    assert "starting at 2" in found["limitation"]


def test_a_chain_the_complex_does_not_hold_is_reported_not_raised(monkeypatch, tmp_path) -> None:
    found = _measure(
        monkeypatch, _root(tmp_path), {**DECLARED, "partner_chains": "Q"}
    )

    assert found["status"] == "complex_refused"
    assert "chain_absent:Q" in found["limitation"]


def test_the_complex_does_not_change_the_input_identity(tmp_path) -> None:
    """The run's manifest digest is over the bytes the generator opens.

    If adding a measurement file moved it, every registered root would need a
    new authorization over a file no execution reads.
    """

    bare = _root(tmp_path / "bare", complex_pdb=None)
    withit = _root(tmp_path / "withit")
    roles = MberDemonstrationLauncher.required_input_roles

    def identity(root):
        catalog = open_registered_input_catalog(
            {"CELLERTZ_PDL1_INPUT_ROOT": str(root)}, required_roles=roles
        )
        return catalog.registration_status(), catalog.identities()

    assert identity(bare) == identity(withit)


def test_bytes_that_change_between_the_two_reads_are_refused(tmp_path) -> None:
    root = _root(tmp_path)
    catalog = open_registered_input_catalog(
        {"CELLERTZ_PDL1_INPUT_ROOT": str(root)},
        required_roles=MberDemonstrationLauncher.required_input_roles,
    )

    loaded = catalog.verified_complex_bytes()

    assert loaded is not None
    assert loaded[0] == hashlib.sha256(COMPLEX.encode("utf-8")).hexdigest()
    (root / "complex.pdb").unlink()
    assert catalog.verified_complex_bytes() is None


def test_a_complex_that_is_not_the_recorded_source_is_refused(monkeypatch, tmp_path) -> None:
    """The root's own preparation.json names the bytes it ran on.

    The CD22 root records source_sha256 a25c467d..., and the complex placed
    beside it hashes to exactly that. A complex that does not is refused before
    it can steer an approval, which the numbering alone would not catch.
    """

    import json

    root = _root(tmp_path)
    (root / "preparation.json").write_text(
        json.dumps({"source_sha256": "0" * 64}), encoding="utf-8"
    )

    found = _measure(monkeypatch, root)

    assert found["status"] == "complex_is_not_the_recorded_source"
    assert "did not come from this file" in found["limitation"]


def test_the_recorded_source_matching_lets_the_measurement_through(monkeypatch, tmp_path) -> None:
    import json

    root = _root(tmp_path)
    (root / "preparation.json").write_text(
        json.dumps({"source_sha256": hashlib.sha256(COMPLEX.encode("utf-8")).hexdigest()}),
        encoding="utf-8",
    )

    assert _measure(monkeypatch, root)["status"] == "measured"


def test_a_preparation_record_that_names_no_source_is_not_itself_a_refusal(
    monkeypatch, tmp_path
) -> None:
    """The PD-L1 root records no preparation at all, and that is not an error."""

    root = _root(tmp_path)
    (root / "preparation.json").write_text("not json", encoding="utf-8")

    assert _measure(monkeypatch, root)["status"] == "measured"


# --- the shape a REAL plan has, which the first version of declared_target never saw ---


def test_a_real_plan_carries_the_target_as_target_identity_and_nothing_else_of_the_complex() -> None:
    """The fact the launcher has to be built around.

    declared_target first read ``target_id``, ``target_chain`` and
    ``partner_chains`` out of the plan's fields, and every test hand-built a
    dictionary with those keys. A plan has ``target_identity`` and none of the
    rest, so every real launch would have been refused as target_not_declared.
    """

    from cellertz.application.generation_plan import GenerationPlanService

    plan = GenerationPlanService().draft(
        "design-request:request_1:revision:1",
        {
            "request_text": "Design a CD22 binder.",
            "target_identity": "CD22",
            "target_identity_provenance": "user",
            "target_identity_source": "professor",
        },
    )

    assert plan["fields"]["target_identity"] == "CD22"
    for absent in ("target_id", "target_chain", "partner_chains", "partner_direction", "source_structure"):
        assert absent not in plan["fields"], absent


def test_a_plan_shaped_request_launches_when_the_saved_draft_declares_the_complex(tmp_path) -> None:
    from cellertz.application.mber_demonstration import declared_target

    root = _root(tmp_path)
    plan_fields = {"target_identity": "CD22", "gpu": "0"}
    declaration = {"source_structure": "7O52", "target_chain": "U", "partner_chains": "H", "partner_direction": "avoid"}

    found = declared_target({**plan_fields, **declaration}, root / "target.pdb")

    assert found.target_id == "CD22"
    assert found.epitope == frozenset({1})
    assert found.partner_chains == ("H",)


def test_a_plan_shaped_request_without_a_declaration_is_refused_for_a_target_it_cannot_measure(
    tmp_path,
) -> None:
    from cellertz.application.mber_demonstration import TargetNotDeclared, declared_target

    with pytest.raises(TargetNotDeclared) as refused:
        declared_target({"target_identity": "CD22", "gpu": "0"}, _root(tmp_path) / "target.pdb")

    assert "no_measured_partner_footprint_for:CD22" in str(refused.value)


def test_the_saved_draft_declares_only_what_a_person_stated() -> None:
    """The direction and the chains are never filled in."""

    from types import SimpleNamespace

    from cellertz.application.design_journey import _saved_declaration

    def field(name: str, value: object, provenance: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, value=value, provenance=provenance)

    draft = SimpleNamespace(
        fields=(
            field("target_id", "CD22", "user"),
            field("source_structure", "7O52", "preserved"),
            field("target_chain", "U", "user"),
            field("partner_chains", "H,L", "documented_default"),
            field("partner_direction", None, "absent"),
            field("clinical_anchor", "  ", "user"),
            field("n_candidates", "5", "user"),
        )
    )
    drafter = SimpleNamespace(recorded_at=lambda request_id, revision: (draft, "message"))

    assert _saved_declaration(drafter, "request_1", 1) == {
        "target_id": "CD22",
        "source_structure": "7O52",
        "target_chain": "U",
    }


def test_a_request_with_no_saved_draft_declares_nothing() -> None:
    from types import SimpleNamespace

    from cellertz.application.design_journey import _saved_declaration

    drafter = SimpleNamespace(recorded_at=lambda request_id, revision: None)

    assert _saved_declaration(drafter, "request_1", 1) == {}
