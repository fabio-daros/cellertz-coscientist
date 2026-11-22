"""The mBER demonstration launcher, without a GPU.

What is worth pinning here is the orchestration, because it is the part that
cost three attempts to discover on real hardware: one trajectory per process,
a bound on how many a single launch may ask for, the weights bound into the
image, and a cohort ranked together rather than per trajectory.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cellertz.application.apptainer_demonstration import DemonstrationLaunch
from cellertz.application.attempt_evidence import (
    PDL1_PD1_FOOTPRINT,
    PDL1_PD1_FOOTPRINT_IDENTITIES,
)
from cellertz.application.mber_campaign import ACCEPTED_RULE
from cellertz.application.mber_demonstration import (
    _RECORDED_PDL1,
    MAX_DEMONSTRATION_TRAJECTORIES,
    PROFILE_ID,
    WEIGHTS_BIND,
    MberDemonstrationLauncher,
    TargetNotDeclared,
    clinical_epitope_in_output_frame,
    declared_target,
    demonstration_specification,
    merge_accepted,
    requested_trajectories,
)
from cellertz.application.mber_settings import GENERATOR_VERSION
from cellertz.runtime.base import ExecutionResult

ATTEMPT_ID = "attempt.ab12cd34"
OFFSET = 17
# The real accepted.csv header, read at the pinned commit of mber-open.
HEADER = [
    "trajectory_name",
    "binder_index",
    "binder_seq",
    "i_ptm",
    "plddt",
    "ptm",
    "complex_pdb_path",
    "relaxed_pdb_path",
]
# A recorded attempt structure: chain A renumbered to 1-115 as mBER also writes
# it, chain B the binder. Using a real one keeps the screen, the occlusion pass
# and the frame check on bytes that came off a card.
MODEL = Path("apps/protein-design-workspace/public/manual-review/attempt.2LUTUOX4/model1.pdb")
BINDER = "MTKYMLFRRLRDREHVGPWQEEMLSEEEAPKREAELKAEAEAMRAESEARGGTGRELEYYVWKAP"


def registered_pdl1_target() -> str:
    """The 22 recorded interface residues in the registered file's own frame.

    The launch reads the epitope's identities off this file, so a one-residue
    stub is not a registered PD-L1 target and is refused as one.
    """

    lines = [
        f"ATOM  {serial:>5d}  CA  {code} A{residue:>4d}       0.000   0.000   0.000  1.00  0.00           C"
        for serial, (residue, code) in enumerate(
            sorted(PDL1_PD1_FOOTPRINT_IDENTITIES.items()), start=1
        )
    ]
    return "\n".join(lines) + "\nEND\n"


class _Runtime:
    """Records every argv it is asked to run and writes what mBER would."""

    name = "stub"
    version = "stub_v1"

    def __init__(self, *, accepted_per_trajectory: int = 0, fail_on: int | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.accepted_per_trajectory = accepted_per_trajectory
        self.fail_on = fail_on

    def execute(self, request, *, on_start=None, stdout_path=None, stderr_path=None):
        self.calls.append(request.argv)
        index = len(self.calls)
        if on_start is not None:
            on_start(4242)
        if self.fail_on == index:
            return ExecutionResult("failed", 1, "", "boom", self.name, self.version, "a", "b")
        settings = json.loads(Path(request.argv[-1]).read_text(encoding="utf-8"))
        output = Path(settings["output"]["dir"])
        rows = []
        for n in range(1, self.accepted_per_trajectory + 1):
            trajectory = f"t{index}"
            complex_pdb = output / f"{trajectory}_binder-{n}_complex.pdb"
            shutil.copy(MODEL, complex_pdb)
            rows.append(
                {
                    "trajectory_name": trajectory,
                    "binder_index": str(n),
                    "binder_seq": BINDER,
                    "i_ptm": "0.81",
                    "plddt": "0.92",
                    "ptm": "0.80",
                    "complex_pdb_path": str(complex_pdb),
                    "relaxed_pdb_path": "",
                }
            )
        with (output / "accepted.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(rows)
        return ExecutionResult("complete", 0, "", "", self.name, self.version, "a", "b")


def _launch(
    tmp_path: Path,
    *,
    trajectories: int | None = 1,
    gpu: str = "0",
    n_designs: int | None = None,
) -> DemonstrationLaunch:
    sif = tmp_path / "mber.sif"
    sif.write_bytes(b"not really an image")
    apptainer = tmp_path / "apptainer"
    apptainer.write_text("#!/bin/sh\n", encoding="utf-8")
    target = tmp_path / "target.pdb"
    target.write_text(registered_pdl1_target(), encoding="utf-8")
    # A launch carries the approved plan's fields, and the target is one of
    # them: the specification is no longer PD-L1's whatever was asked for.
    fields = {"gpu": gpu, "target_id": "PDL1"}
    if trajectories is not None:
        fields["mber_trajectories"] = trajectories
    return DemonstrationLaunch(
        fields=fields,
        request_text="",
        approval_source="human_review",
        approved=True,
        input_selection="registered",
        live_input_selection="registered",
        target_path=target,
        settings_path=tmp_path / "unused-settings.json",
        advanced_profile_path=tmp_path / "unused-advanced.json",
        attempt_id=ATTEMPT_ID,
        attempt_directory=tmp_path / "attempts" / ATTEMPT_ID,
        gpu_index=0,
        apptainer_bin=apptainer,
        sif_path=sif,
        expected_sif_sha256=hashlib.sha256(sif.read_bytes()).hexdigest(),
        n_designs=n_designs,
    )


def _launcher(tmp_path: Path, runtime: _Runtime) -> MberDemonstrationLauncher:
    weights = tmp_path / "weights"
    (weights / "af_params").mkdir(parents=True)
    return MberDemonstrationLauncher(
        weights_dir=str(weights), runtime=runtime, check_gpu=False
    )


@pytest.fixture
def attempts(tmp_path: Path) -> Path:
    (tmp_path / "attempts").mkdir()
    return tmp_path


def test_one_process_per_trajectory(attempts: Path) -> None:
    """The fact that cost three attempts on the card: never two in one process."""

    runtime = _Runtime()
    reply = _launcher(attempts, runtime).launch(_launch(attempts, trajectories=3))

    assert reply["status"] == "no_accepted_design"
    assert reply["trajectories"] == 3
    assert len(runtime.calls) == 3
    settings_paths = [call[-1] for call in runtime.calls]
    assert len(set(settings_paths)) == 3, "each trajectory needs its own output directory"


def test_the_weights_are_bound_and_the_card_is_named(attempts: Path) -> None:
    runtime = _Runtime()
    _launcher(attempts, runtime).launch(_launch(attempts))

    argv = runtime.calls[0]
    assert "--nv" in argv
    assert "CUDA_VISIBLE_DEVICES=0" in argv
    assert any(part.endswith(f":{WEIGHTS_BIND}") for part in argv), argv
    assert argv[-2] == "--settings"
    # HOME is the bind mount point in a read-only image, so without these two
    # the first real launch logged "kernel cache directory could not be
    # created" and ran with CUDA kernel caching disabled.
    assert "XDG_CACHE_HOME=/tmp/cache" in argv
    assert "MPLCONFIGDIR=/tmp/matplotlib" in argv


def test_a_launch_cannot_ask_for_a_campaign(attempts: Path) -> None:
    """5000 accepted designs is 6000 GPU-hours. A button does not start that."""

    runtime = _Runtime()
    reply = _launcher(attempts, runtime).launch(
        _launch(attempts, trajectories=MAX_DEMONSTRATION_TRAJECTORIES + 1)
    )

    assert reply["launched"] is False
    assert reply["diagnosis"] == "trajectory_count_rejected"
    assert runtime.calls == [], "nothing may reach the card on a refused count"


def test_an_absent_count_means_one_trajectory() -> None:
    assert requested_trajectories({"gpu": "0"}) == 1
    assert requested_trajectories({"mber_trajectories": 4}) == 4
    for rejected in (0, -1, True, 2.5, "4", MAX_DEMONSTRATION_TRAJECTORIES + 1):
        assert requested_trajectories({"mber_trajectories": rejected}) is None


def test_a_failed_trajectory_stops_and_says_which(attempts: Path) -> None:
    runtime = _Runtime(fail_on=2)
    reply = _launcher(attempts, runtime).launch(_launch(attempts, trajectories=4))

    assert reply["status"] == "launch_failed"
    assert reply["trajectory"] == 2
    assert reply["trajectories_completed"] == 1
    assert len(runtime.calls) == 2, "the launch stops rather than burning the rest"


def test_the_cohort_is_ranked_together(attempts: Path) -> None:
    """Percentiles are computed over a cohort, so the trajectories join."""

    runtime = _Runtime(accepted_per_trajectory=2)
    reply = _launcher(attempts, runtime).launch(_launch(attempts, trajectories=2))

    assert reply["status"] == "ranked", reply
    assert reply["accepted_designs"] == 4
    assert reply["generator_version"] == GENERATOR_VERSION
    assert reply["scientific_validation"] is False
    assert reply["ranking"]["generated"] == 4


def test_the_progress_file_records_the_mber_profile(attempts: Path) -> None:
    """The shared helper stamps BindCraft's id unless it is overridden."""

    _launcher(attempts, _Runtime()).launch(_launch(attempts))

    progress = json.loads(
        (attempts / "attempts" / ATTEMPT_ID / "progress.json").read_text(encoding="utf-8")
    )
    assert progress["profile_id"] == PROFILE_ID
    assert PROFILE_ID != "pdl1-bindcraft-apptainer-demonstration-v1"


def test_a_missing_weights_bind_is_refused_before_the_card(attempts: Path) -> None:
    runtime = _Runtime()
    launcher = MberDemonstrationLauncher(
        weights_dir=str(attempts / "absent"), runtime=runtime, check_gpu=False
    )

    reply = launcher.launch(_launch(attempts))

    assert reply["status"] == "mber_weights_unavailable"
    assert runtime.calls == []


def test_the_specification_records_the_generator_version() -> None:
    """The one field 6C.1 requires, which a readiness manifest may not supply."""

    specification = demonstration_specification(1)

    assert specification.generator_protocol is not None
    assert specification.generator_protocol.generator_version == GENERATOR_VERSION
    assert specification.target_id == "PDL1"
    with pytest.raises(ValueError):
        demonstration_specification(MAX_DEMONSTRATION_TRAJECTORIES + 1)


def test_the_clinical_epitope_is_moved_into_the_output_frame(tmp_path: Path) -> None:
    """A residue number carried across frames without the offset is another residue."""

    cohort = tmp_path / "cohort"
    cohort.mkdir()
    (tmp_path / "t1").mkdir()
    import shutil as _shutil

    _shutil.copy(MODEL, tmp_path / "t1" / "d_complex.pdb")

    found = clinical_epitope_in_output_frame(cohort)

    assert found is not None
    assert found[0] == frozenset(residue - OFFSET for residue in PDL1_PD1_FOOTPRINT)
    assert max(found[0]) < min(PDL1_PD1_FOOTPRINT) + OFFSET + 115


def test_an_unverifiable_frame_yields_no_clinical_epitope(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort"
    cohort.mkdir()
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "d_complex.pdb").write_text(
        "ATOM      1  CA  GLY A   1       0.000   0.000   0.000\n", encoding="utf-8"
    )

    assert clinical_epitope_in_output_frame(cohort) is None


def test_merging_refuses_trajectories_whose_columns_differ(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "accepted.csv").write_text("design_id,i_ptm\nd1,0.8\n", encoding="utf-8")
    (second / "accepted.csv").write_text("design_id,plddt\nd2,0.9\n", encoding="utf-8")

    with pytest.raises(ValueError):
        merge_accepted([first, second], tmp_path / "cohort")


def test_the_displayed_design_count_is_the_target_and_stops_the_loop(attempts: Path) -> None:
    """A run that quietly did less than the screen said is worse than no run."""

    runtime = _Runtime(accepted_per_trajectory=2)
    reply = _launcher(attempts, runtime).launch(
        _launch(attempts, trajectories=6, n_designs=2)
    )

    assert reply["status"] == "ranked", reply
    assert reply["accepted_designs"] == 2
    assert reply["trajectories"] == 1, "the target was met, so the rest is not spent"
    assert reply["trajectory_budget"] == 6
    assert len(runtime.calls) == 1


def test_the_target_reaches_the_settings_mber_reads(attempts: Path) -> None:
    runtime = _Runtime()
    _launcher(attempts, runtime).launch(_launch(attempts, trajectories=2, n_designs=5))

    settings = json.loads(Path(runtime.calls[0][-1]).read_text(encoding="utf-8"))
    assert settings["stopping"]["num_accepted"] == 5
    assert settings["stopping"]["max_trajectories"] == 1, "one trajectory per process"


def test_a_ranked_launch_declares_its_accepted_candidate(attempts: Path) -> None:
    """A round reads accepted_candidate to decide whether specialists may run.

    This launcher declared none, so a run that screened and ranked a real
    cohort still arrived at the journey as "no accepted candidate".
    """

    runtime = _Runtime(accepted_per_trajectory=2)
    reply = _launcher(attempts, runtime).launch(_launch(attempts, trajectories=2))

    assert reply["status"] == "ranked", reply
    assert reply["accepted_candidate"] is True
    assert reply["successful_candidate_generation"] is True
    identity = reply["candidate_identity"]
    assert identity["candidate_id"] == reply["candidate_id"]
    assert identity["execution_id"] == ATTEMPT_ID
    # The target is the one the launcher's own specification declares, which
    # is still the PD-L1 demonstration's whatever the plan asked for.
    assert identity["target_id"] == demonstration_specification(1).target_id
    assert identity["sequence"] and identity["design"]
    panel = reply["ranking"]["ranking"]["designs"]
    chosen = next(row for row in panel if row["design_id"] == reply["candidate_id"])
    assert chosen["selected"] is True
    assert chosen["rank"] == min(row["rank"] for row in panel if row["selected"])
    evidence = reply["acceptance_evidence"]
    assert evidence["rule"] == ACCEPTED_RULE
    assert evidence["protocol_id"] == "ENGAGER_TWO_AXIS_2026"
    assert evidence["rank"] == chosen["rank"]
    assert evidence["score"] == chosen["score"]
    assert evidence["scientific_validation_established"] is False


def test_a_cohort_mber_accepted_nothing_from_declares_no_candidate(attempts: Path) -> None:
    """mBER's own filter rejecting every binder is a legitimate outcome."""

    reply = _launcher(attempts, _Runtime(accepted_per_trajectory=0)).launch(_launch(attempts))

    assert reply["status"] == "no_accepted_design"
    assert reply.get("accepted_candidate") is not True
    assert "candidate_identity" not in reply


# A synthetic complex and the prepared target taken from it: chain U is the
# target, chain H the partner, and residues 11 and 12 are the only ones within
# 5 A of it. Residue 40 is in the complex but was removed by the preparation.
def _complex_pair(tmp_path: Path) -> Path:
    def atom(serial: int, code: str, chain: str, residue: int, x: float) -> str:
        return (
            f"ATOM  {serial:>5d}  CA  {code} {chain}{residue:>4d}    "
            f"{x:>8.3f}   0.000   0.000  1.00  0.00           C"
        )

    root = tmp_path / "inputs"
    root.mkdir(parents=True, exist_ok=True)
    prepared = [atom(index, "GLY", "A", residue, 0.0) for index, residue in enumerate((11, 12, 13), start=1)]
    (root / "target.pdb").write_text("\n".join(prepared) + "\nEND\n", encoding="utf-8")
    source = [
        atom(1, "GLY", "U", 11, 0.0),
        atom(2, "GLY", "U", 12, 0.0),
        atom(3, "GLY", "U", 13, 30.0),
        atom(4, "GLY", "U", 40, 0.0),
        atom(5, "ALA", "H", 90, 3.0),
        atom(6, "ALA", "H", 91, 4.0),
    ]
    (root / "complex.pdb").write_text("\n".join(source) + "\nEND\n", encoding="utf-8")
    return root / "target.pdb"


def test_the_epitope_is_the_measured_partner_footprint(tmp_path: Path) -> None:
    """The surface the ranking scores against is the plan's, not a constant."""

    target_path = _complex_pair(tmp_path)

    declared = declared_target(
        {"target_id": "CD22", "source_structure": "7O52", "target_chain": "U", "partner_chains": "H"},
        target_path,
    )

    assert declared.target_id == "CD22"
    assert declared.epitope == frozenset({11, 12})
    assert declared.identities == {11: "GLY", 12: "GLY"}
    assert declared.epitope_provenance == "measured_partner_footprint"
    # Residue 40 touches the partner in the source and is not in the prepared
    # file, so it is not part of this campaign's epitope.
    assert "1 removed by the preparation" in declared.epitope_note

    specification = demonstration_specification(1, None, declared)
    assert specification.target_id == "CD22"
    assert specification.target_structure.source_identifier == "7O52"
    assert specification.target_structure.source_partner_chain_ids == ("H",)
    assert specification.epitope.derivation_method == "structure_contact"
    assert specification.epitope.contact_cutoff_angstrom == 5.0
    assert [item.residue_number for item in specification.epitope.residues] == [11, 12]


def test_another_target_without_a_complex_is_refused_before_the_card(tmp_path: Path) -> None:
    """A campaign cannot borrow PD-L1's epitope by leaving its own undeclared."""

    target_path = _complex_pair(tmp_path)
    (target_path.parent / "complex.pdb").unlink()

    with pytest.raises(TargetNotDeclared) as refused:
        declared_target({"target_id": "CD22", "target_chain": "U", "partner_chains": "H"}, target_path)

    assert "no_measured_partner_footprint_for:CD22" in str(refused.value)


def test_a_file_that_is_not_pdl1_cannot_be_declared_as_pdl1(tmp_path: Path) -> None:
    target_path = _complex_pair(tmp_path)
    (target_path.parent / "complex.pdb").unlink()

    with pytest.raises(TargetNotDeclared) as refused:
        declared_target({"target_id": "PDL1"}, target_path)

    assert "not_the_recorded_pdl1_reference" in str(refused.value)


def test_the_launch_refuses_a_plan_that_declares_no_target(attempts: Path) -> None:
    """Nothing is spent: the refusal is before the runtime is asked for anything."""

    runtime = _Runtime(accepted_per_trajectory=2)
    request = dataclasses.replace(_launch(attempts), fields={"gpu": "0", "mber_trajectories": 1})

    reply = _launcher(attempts, runtime).launch(request)

    assert reply["status"] == "target_not_declared"
    assert "plan_declares_no_target_id" in reply["diagnosis"]
    assert runtime.calls == []


def test_the_output_frame_is_found_with_the_declared_identities(tmp_path: Path) -> None:
    """The offset check still refuses a different protein; it is just not PD-L1 only."""

    cohort = tmp_path / "attempt" / "cohort"
    cohort.mkdir(parents=True)
    shutil.copy(MODEL, cohort.parent / "t1_binder-1_complex.pdb")

    found = clinical_epitope_in_output_frame(cohort, _RECORDED_PDL1)
    assert found == (frozenset(residue - OFFSET for residue in PDL1_PD1_FOOTPRINT),)

    other = dataclasses.replace(
        _RECORDED_PDL1, identities={18: "TRP", 19: "TRP"}, epitope=frozenset({18, 19})
    )
    assert clinical_epitope_in_output_frame(cohort, other) is None


def _parent_sequence() -> str:
    """A real parent: the scaffold with every design position filled in."""

    from cellertz.application.mber_settings import MASK

    scaffold = demonstration_specification(1).scaffold.masked_sequence
    return "".join("G" if residue == MASK else residue for residue in scaffold)


def test_a_seeded_launch_sends_the_parent_design_to_every_trajectory(attempts: Path) -> None:
    """The atlas's second lever: on albumin, seeding took 2 of 14 to 13 of 14.

    The settings document is the only mechanism — mBER has no seeding flag —
    so this asserts the reseated mask reached the file each trajectory reads.
    """

    runtime = _Runtime(accepted_per_trajectory=1)
    parent = _parent_sequence()
    request = dataclasses.replace(
        _launch(attempts, trajectories=2),
        seed_design={
            "parent_round_id": "round_j_1",
            "attempt_id": "attempt.parent",
            "design": "parent_design",
            "sequence": parent,
        },
    )

    reply = _launcher(attempts, runtime).launch(request)

    assert reply["status"] == "ranked", reply
    scaffold = demonstration_specification(1).scaffold.masked_sequence
    for index in (1, 2):
        settings = json.loads(
            (attempts / "attempts" / ATTEMPT_ID / f"t{index}" / "settings.json").read_text(
                encoding="utf-8"
            )
        )
        sent = settings["binder"]["masked_sequence"]
        assert sent != scaffold, "the trajectory started from the bare framework"
        assert sent.count("*") < scaffold.count("*")
    assert reply["seeded_from"]["design_id"] == "parent_design"
    assert reply["seeded_from"]["parent_attempt_id"] == "attempt.parent"
    assert reply["seeded_from"]["redesign_spans"], "a seed that redesigns nothing is not a seed"


def test_an_unseeded_launch_still_sends_the_bare_scaffold(attempts: Path) -> None:
    reply = _launcher(attempts, _Runtime(accepted_per_trajectory=1)).launch(_launch(attempts))

    settings = json.loads(
        (attempts / "attempts" / ATTEMPT_ID / "t1" / "settings.json").read_text(encoding="utf-8")
    )
    assert settings["binder"]["masked_sequence"] == demonstration_specification(1).scaffold.masked_sequence
    assert reply["seeded_from"] is None


def test_a_seed_that_cannot_be_reseated_is_refused_before_the_card(attempts: Path) -> None:
    """Falling back to the bare framework would run a different experiment."""

    runtime = _Runtime(accepted_per_trajectory=1)
    launcher = _launcher(attempts, runtime)
    for seed, expected in (
        ({"design": "d", "sequence": "TOOSHORT"}, "seed_length_differs_from_scaffold"),
        ({"design": "d"}, "seed_carries_no_sequence_for:d"),
        ({"sequence": _parent_sequence()}, "seed_names_no_design"),
        ("not a record", "seed_is_not_a_record"),
    ):
        request = dataclasses.replace(_launch(attempts), seed_design=seed)
        reply = launcher.launch(request)
        assert reply["status"] == "seed_rejected", seed
        assert expected in reply["diagnosis"], seed
    assert runtime.calls == [], "nothing was spent on a seed that does not fit"
