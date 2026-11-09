import json
import os
import subprocess
import sys

import numpy as np

from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    materialize_seed_bootstrap,
)


def test_bootstrap_captures_final_prediction_pae(tmp_path):
    fake_root = tmp_path / "fake_bindcraft"
    fake_root.mkdir()

    functions_path = fake_root / "functions.py"
    functions_path.write_text(
        '''
def predict_binder_complex(
    prediction_model,
    binder_sequence,
    mpnn_design_name,
    target_pdb,
    chain,
    length,
    trajectory_pdb,
    prediction_models,
    advanced_settings,
    filters,
    design_paths,
    failure_csv,
    seed=None,
):
    del (
        target_pdb,
        chain,
        length,
        trajectory_pdb,
        advanced_settings,
        filters,
        failure_csv,
        seed,
    )

    for model_num in prediction_models:
        prediction_model.predict(
            seq=binder_sequence,
            models=[model_num],
            num_recycles=1,
            verbose=False,
        )

    return {}, True
'''.lstrip()
    )

    script_path = fake_root / "bindcraft.py"
    script_path.write_text(
        '''
import os
from pathlib import Path

import numpy as np

from functions import *


class FakePredictionModel:
    def __init__(self):
        self.aux = {}
        self._lengths = [2, 1]

    def predict(self, **kwargs):
        model_num = kwargs["models"][0]
        self.aux["pae"] = (
            np.arange(9, dtype=float).reshape(3, 3)
            + model_num
        )
        self.aux["aatype"] = np.array([0, 0, 0])
        self.aux["residue_index"] = np.array([10, 11, 61])


output = Path(os.environ["CELLERTZ_TEST_OUTPUT"])
mpnn = output / "MPNN"
relaxed = mpnn / "Relaxed"
relaxed.mkdir(parents=True, exist_ok=True)
pdb = (
    "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\\n"
    "ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00  0.00           C\\n"
    "ATOM      3  CA  ALA B   1       2.000   0.000   0.000  1.00  0.00           C\\n"
)
for model in (1, 2):
    (relaxed / f"candidate_1_model{model}.pdb").write_text(pdb, encoding="utf-8")

predict_binder_complex(
    FakePredictionModel(),
    "AAA",
    "candidate_1",
    "target.pdb",
    "A",
    3,
    "trajectory.pdb",
    [0, 1],
    {},
    {},
    {"MPNN": str(mpnn)},
    str(output / "failure.csv"),
)
'''.lstrip()
    )

    bootstrap = materialize_seed_bootstrap(
        tmp_path / "runtime"
    )

    env = dict(os.environ)
    env["CELLERTZ_TEST_OUTPUT"] = str(
        tmp_path / "designs"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(bootstrap),
            "--cellertz-seed",
            "0",
            "--bindcraft-script",
            str(script_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

    evidence_dir = (
        tmp_path
        / "designs"
        / "CellertzEvidence"
        / "PAE"
    )

    model_1 = (
        evidence_dir
        / "candidate_1_model1_pae.npz"
    )
    model_2 = (
        evidence_dir
        / "candidate_1_model2_pae.npz"
    )

    assert model_1.is_file()
    assert model_2.is_file()

    with np.load(model_1, allow_pickle=False) as payload:
        assert payload["pae_angstrom"].shape == (3, 3)
        assert payload["candidate_id"].item() == "candidate_1"
        assert payload["model_number"].item() == 1
        assert payload["pae_angstrom"][0, 0] == 0.0

    with np.load(model_2, allow_pickle=False) as payload:
        assert payload["model_number"].item() == 2
        assert payload["pae_angstrom"][0, 0] == 1.0

    order = json.loads(
        (evidence_dir / "candidate_1_model1_residue_order.json").read_text(encoding="utf-8")
    )
    assert order["established"] is True
    assert order["contract"] == "pae_index_equals_capture_time_residue_order"
    assert order["source"] == "prediction_model.aux"
    assert order["candidate_id"] == "candidate_1"
    assert order["model_number"] == 1
    assert [item["chain"] for item in order["residues"]] == ["A", "A", "B"]
    assert [item["residue_name"] for item in order["residues"]] == ["ALA", "ALA", "ALA"]
    assert [item["model_residue_index"] for item in order["residues"]] == [10, 11, 61]
    assert "ca_chains" not in order


def _run_invalid_pae_bootstrap(
    tmp_path,
    pae_expression,
):
    fake_root = tmp_path / "fake_invalid_bindcraft"
    fake_root.mkdir()

    functions_path = fake_root / "functions.py"
    functions_path.write_text(
        '''
def predict_binder_complex(
    prediction_model,
    binder_sequence,
    mpnn_design_name,
    target_pdb,
    chain,
    length,
    trajectory_pdb,
    prediction_models,
    advanced_settings,
    filters,
    design_paths,
    failure_csv,
    seed=None,
):
    for model_num in prediction_models:
        prediction_model.predict(
            seq=binder_sequence,
            models=[model_num],
            num_recycles=1,
            verbose=False,
        )

    return {}, True
'''.lstrip()
    )

    script_path = fake_root / "bindcraft.py"
    script_path.write_text(
        f'''
import os
from pathlib import Path

import numpy as np

from functions import *


class FakePredictionModel:
    def __init__(self):
        self.aux = {{}}

    def predict(self, **kwargs):
        self.aux["pae"] = {pae_expression}


output = Path(os.environ["CELLERTZ_TEST_OUTPUT"])
mpnn = output / "MPNN"
mpnn.mkdir(parents=True, exist_ok=True)

predict_binder_complex(
    FakePredictionModel(),
    "AAA",
    "candidate_invalid",
    "target.pdb",
    "A",
    3,
    "trajectory.pdb",
    [0],
    {{}},
    {{}},
    {{"MPNN": str(mpnn)}},
    str(output / "failure.csv"),
)
'''.lstrip()
    )

    bootstrap = materialize_seed_bootstrap(
        tmp_path / "runtime"
    )

    env = dict(os.environ)
    env["CELLERTZ_TEST_OUTPUT"] = str(
        tmp_path / "designs"
    )

    return subprocess.run(
        [
            sys.executable,
            str(bootstrap),
            "--cellertz-seed",
            "0",
            "--bindcraft-script",
            str(script_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_bootstrap_rejects_non_square_pae(tmp_path):
    completed = _run_invalid_pae_bootstrap(
        tmp_path,
        "np.zeros((2, 3), dtype=float)",
    )

    assert completed.returncode != 0
    assert (
        "Cellertz PAE capture requires "
        "a square 2D matrix."
        in completed.stderr
    )


def test_bootstrap_keeps_capture_order_when_relaxed_pdb_differs(tmp_path):
    fake_root = tmp_path / "fake_bindcraft"
    fake_root.mkdir()
    (fake_root / "functions.py").write_text(
        """
def predict_binder_complex(
    prediction_model, binder_sequence, mpnn_design_name, target_pdb, chain,
    length, trajectory_pdb, prediction_models, advanced_settings, filters,
    design_paths, failure_csv, seed=None,
):
    for model_num in prediction_models:
        prediction_model.predict(seq=binder_sequence, models=[model_num])
    return {}, True
""".lstrip(),
        encoding="utf-8",
    )
    (fake_root / "bindcraft.py").write_text(
        """
import os
from pathlib import Path
import numpy as np
from functions import *


class FakePredictionModel:
    def __init__(self):
        self.aux = {}
        self._lengths = [2, 1]

    def predict(self, **kwargs):
        self.aux["pae"] = np.zeros((3, 3), dtype=float)
        self.aux["aatype"] = np.array([0, 0, 0])
        self.aux["residue_index"] = np.array([10, 11, 61])


output = Path(os.environ["CELLERTZ_TEST_OUTPUT"])
relaxed = output / "MPNN" / "Relaxed"
relaxed.mkdir(parents=True, exist_ok=True)
pdb = (
    "ATOM      1  CA  ALA B   1       0.000   0.000   0.000  1.00  0.00           C\\n"
    "ATOM      2  CA  ALA A   1       1.000   0.000   0.000  1.00  0.00           C\\n"
    "ATOM      3  CA  ALA A   2       2.000   0.000   0.000  1.00  0.00           C\\n"
)
(relaxed / "candidate_1_model1.pdb").write_text(pdb, encoding="utf-8")
predict_binder_complex(
    FakePredictionModel(), "AAA", "candidate_1", "target.pdb", "A", 3,
    "trajectory.pdb", [0], {}, {}, {"MPNN": str(output / "MPNN")},
    str(output / "failure.csv"),
)
""".lstrip(),
        encoding="utf-8",
    )
    bootstrap = materialize_seed_bootstrap(tmp_path / "runtime")
    env = dict(os.environ)
    env["CELLERTZ_TEST_OUTPUT"] = str(tmp_path / "designs")
    completed = subprocess.run(
        [sys.executable, str(bootstrap), "--cellertz-seed", "0", "--bindcraft-script", str(fake_root / "bindcraft.py")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    order = json.loads(
        (tmp_path / "designs" / "CellertzEvidence" / "PAE" / "candidate_1_model1_residue_order.json").read_text(
            encoding="utf-8"
        )
    )
    assert order["established"] is False
    assert order["reason"] == "relaxed_pdb_residue_order_differs"
    assert [item["chain"] for item in order["residues"]] == ["A", "A", "B"]
    assert [item["model_residue_index"] for item in order["residues"]] == [10, 11, 61]


def test_bootstrap_rejects_non_finite_pae(tmp_path):
    completed = _run_invalid_pae_bootstrap(
        tmp_path,
        (
            "np.array([[0.0, np.nan], "
            "[1.0, 2.0]], dtype=float)"
        ),
    )

    assert completed.returncode != 0
    assert (
        "Cellertz PAE capture received "
        "non-finite values."
        in completed.stderr
    )
