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
