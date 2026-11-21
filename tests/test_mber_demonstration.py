"""The mBER demonstration launcher, without a GPU.

What is worth pinning here is the orchestration, because it is the part that
cost three attempts to discover on real hardware: one trajectory per process,
a bound on how many a single launch may ask for, the weights bound into the
image, and a cohort ranked together rather than per trajectory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cellertz.application.apptainer_demonstration import DemonstrationLaunch
from cellertz.application.attempt_evidence import PDL1_PD1_FOOTPRINT
from cellertz.application.mber_demonstration import (
    MAX_DEMONSTRATION_TRAJECTORIES,
    PROFILE_ID,
    WEIGHTS_BIND,
    MberDemonstrationLauncher,
    clinical_epitope_in_output_frame,
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
    target.write_text("ATOM      1  N   ALA A  18       0.000   0.000   0.000\n", encoding="utf-8")
    fields = {"gpu": gpu}
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
