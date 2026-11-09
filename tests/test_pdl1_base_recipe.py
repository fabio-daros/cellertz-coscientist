"""Base-image recipe stays fail-closed. No conda, Docker, or download."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from cellertz.application.pdl1_image_readiness import (
    UNRESOLVED_BLOCKERS,
    ImageReadinessError,
    dependency_gaps,
    missing_conda_lock_names,
    parse_exact_lock,
    required_conda_names,
)

_ROOT = Path(__file__).resolve().parents[1]
_DEPLOY = _ROOT / "deploy" / "pdl1-bindcraft"
_RECORDED = {
    "jax": "0.6.0=pyhd8ed1ab_0",
    "jaxlib": "0.6.0=cuda126py310hec873cc_200",
    "cuda-version": "12.9=h4f385c5_3",
    "cuda-nvcc": "12.9.86=hcdd1206_6",
    "cudnn": "9.10.2.21=hbcb9cd8_0",
    "libgfortran5": "16.2.0=h6b99dfc_5",
    "python": "3.10.21=h267e890_0_cpython",
}


def _inventory():
    path = _DEPLOY / "inventory-remaining-environment.py"
    spec = importlib.util.spec_from_file_location("inventory_remaining_environment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observed_builds_are_exact_and_do_not_clear_readiness_gates() -> None:
    lock_text = (_DEPLOY / "conda-observed.lock").read_text(encoding="utf-8")
    pins = parse_exact_lock(lock_text)
    assert len(pins) == 431
    for name, spec in _RECORDED.items():
        assert pins[name] == spec
    missing = missing_conda_lock_names(pins)
    assert missing == ()
    assert "python=3.10.21=h267e890_0_cpython" in lock_text
    assert "python=3.14.7=" not in lock_text
    assert "anyio=4.14.2=pyhcf101f3_0" in lock_text
    assert "anyio=4.15.1=" not in lock_text
    assert "libmambapy=" not in lock_text
    assert "host_cuda_compatibility_unverified" in UNRESOLVED_BLOCKERS
    assert "jaxlib_cuda_build_unverified" in UNRESOLVED_BLOCKERS
    observations = json.loads((_DEPLOY / "historical-observations.json").read_text(encoding="utf-8"))
    assert observations["approved_pin"] is False
    assert observations["driver_compatibility"] == "unresolved"
    assert observations["python"]["version"] == "3.10.21"
    assert observations["python"]["conda_build"] == "h267e890_0_cpython"
    assert observations["bootstrap_python"]["version"] == "3.14.7"
    assert observations["bootstrap_python"]["belongs_in_bindcraft_lock"] is False
    assert observations["colabdesign"]["pip_section_absence_does_not_erase_commit"] is True
    assert observations["colabdesign"]["commit_in_pip_inventory"] is False
    assert observations["colabdesign"]["installer"] == "pip"
    assert observations["conda_cli_version"] == "26.7.2"
    assert observations["miniforge_release"] == "26.7.2-0"
    assert observations["miniforge_release_verified_by_inventory"] is False
    assert observations["installer_filename"] == "Miniforge3-26.7.2-0-Linux-x86_64.sh"
    assert observations["installer_sha256"] == "281b0ac7d550802efc81af633225a5e6116d29ae72f3ab4eae7168c3931a4c05"
    assert observations["installer_sha256_verified_against_official_checksum"] is True
    assert observations["installer_bytes_compared_to_sif"] is False
    installer = _DEPLOY / "installer" / "miniforge.sh"
    installer_sha256 = hashlib.sha256(installer.read_bytes()).hexdigest()
    assert installer_sha256 == observations["installer_sha256"]
    assert observations["installed_metadata_is_a_reproducible_build"] is False
    pyrosetta = observations["pyrosetta"]
    assert pyrosetta["version"] == "2026.29+releasequarterly.80a0635615"
    assert pyrosetta["installer"] == "pip"
    assert pyrosetta["wheel_filename"] == (
        "pyrosetta-2026.29+releasequarterly.80a0635615-cp310-cp310-linux_x86_64.whl"
    )
    assert pyrosetta["wheel_size_bytes"] == 1667097173
    assert pyrosetta["wheel_sha256_label"] == "locally_computed"
    assert pyrosetta["upstream_sha256_published"] is False
    assert pyrosetta["wheel_tag"] == "cp310-cp310-linux_x86_64"
    assert pyrosetta["wheel_abi_verified"] is False
    assert pyrosetta["license_accepted"] is False
    assert pyrosetta["required_by_dockerfile_base"] is False
    assert pyrosetta["required_by_governed_image"] is True
    wheel = _DEPLOY / "vendor" / pyrosetta["wheel_filename"]
    assert wheel.stat().st_size == pyrosetta["wheel_size_bytes"]
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == pyrosetta["wheel_sha256"]
    params = observations["alphafold_params"]
    assert params["filename"] == "alphafold_params_2022-12-06.tar"
    assert params["size_bytes"] == 5587968000
    assert params["sha256_label"] == "locally_computed"
    assert params["upstream_sha256_published"] is False
    assert params["md5_matches_published_etag_and_x_goog_hash"] is True
    assert params["required_by_dockerfile_base"] is False
    assert params["required_by_governed_image"] is True
    archive = _DEPLOY / "vendor" / params["filename"]
    assert archive.stat().st_size == params["size_bytes"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == params["sha256"]
    assert observations["colabdesign"]["commit"] == "e31a56fe1d9b4de25c8697f3a28b75892941cc72"
    assert observations["colabdesign"]["commit_approved_as_pin"] is False
    assert observations["apptainer_success_establishes_docker_compatibility"] is False
    bootstrap = json.loads((_DEPLOY / "bootstrap-observations.json").read_text(encoding="utf-8"))
    assert bootstrap["belongs_in_bindcraft_lock"] is False
    assert bootstrap["conda"]["version"] == "26.7.2"
    assert bootstrap["conda"]["build"] == "py314h9e666f3_1"
    assert bootstrap["conda_cli_version"] == "26.7.2"
    assert bootstrap["conda_package_build_is_not_the_cli_version"] is True
    assert bootstrap["miniforge_release_label"] == "26.7.2-0"
    assert bootstrap["installer_filename"] == "Miniforge3-26.7.2-0-Linux-x86_64.sh"
    assert bootstrap["installer_sha256"] == observations["installer_sha256"]
    assert bootstrap["installer_bytes_compared_to_sif"] is False


def test_a_floating_conda_spec_is_rejected() -> None:
    with pytest.raises(ImageReadinessError) as error:
        parse_exact_lock("jax=0.6.0\n")
    assert error.value.code == "base_lock_unconstrained"


def test_required_names_file_matches_the_install_script_names() -> None:
    recorded = [
        line.strip()
        for line in (_DEPLOY / "required-conda-names.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert recorded == list(required_conda_names())


def test_lock_check_stops_before_an_unconstrained_solve() -> None:
    script = _DEPLOY / "check-conda-lock.sh"
    syntax = subprocess.run(["sh", "-n", str(script)], check=False, capture_output=True, text=True)
    assert syntax.returncode == 0
    checked = subprocess.run(
        [
            "sh",
            str(script),
            str(_DEPLOY / "conda-observed.lock"),
            str(_DEPLOY / "required-conda-names.txt"),
            str(_DEPLOY / "conda-bindcraft.depends.tsv"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert "unresolved_virtual\t__cuda\t1" in checked.stdout
    assert "unresolved_virtual\t__glibc\t230" in checked.stdout
    assert "metadata_is_not_a_reproducible_build" in checked.stdout
    assert "pip_observation\tcolabdesign\t1.1.3\tcommit_not_in_pip_row" in checked.stdout
    assert "base_lock_pip_replacement" not in checked.stderr
    depends = (_DEPLOY / "conda-bindcraft.depends.tsv").read_text(encoding="utf-8")
    for spec in (
        "__cuda",
        "__glibc >=2.17,<3.0.a0",
        "__glibc >=2.28,<3.0.a0",
        "__glibc >=2.34",
        "__linux",
        "__unix",
    ):
        assert any(line.endswith(f"\t{spec}") for line in depends.splitlines())


def test_a_direct_name_lock_without_the_closure_still_stops(tmp_path: Path) -> None:
    lines = [f"{name}=1.0.0=hab_0" for name in required_conda_names()]
    lock = tmp_path / "lock"
    names = tmp_path / "names"
    lock.write_text("# transitive_closure: recorded\n" + "\n".join(lines) + "\n", encoding="utf-8")
    names.write_text("\n".join(required_conda_names()) + "\n", encoding="utf-8")
    marker = tmp_path / "depends.tsv"
    marker.write_text("# transitive_closure: recorded\n", encoding="utf-8")
    completed = subprocess.run(
        ["sh", str(_DEPLOY / "check-conda-lock.sh"), str(lock), str(names), str(marker)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert completed.stderr.startswith("base_lock_dependencies_unverified:")
    both = tmp_path / "both-python.lock"
    both.write_text(
        "python=3.10.21=h267e890_0_cpython\npython=3.14.7=hcd007b5_106_cp314\n",
        encoding="utf-8",
    )
    conflict = subprocess.run(
        ["sh", str(_DEPLOY / "check-conda-lock.sh"), str(both), str(names), str(marker)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert conflict.returncode == 1
    assert conflict.stderr.strip() == "base_lock_conflict:python"


def test_dockerfile_base_does_not_solve_and_keeps_digest_identity_separate() -> None:
    text = (_DEPLOY / "Dockerfile.base").read_text(encoding="utf-8")
    assert "--no-deps" in text
    assert "conda install" not in text
    assert "conda update" not in text
    assert "RepoDigests is empty" in text
    assert "Scientific execution stays disabled." in text
    assert "conda-bindcraft.depends.tsv" in text
    assert "check-conda-constraints.py" in text
    assert "python3 runs the lock constraint checker only" in text
    ubuntu = json.loads((_DEPLOY / "ubuntu-22.04-identity.json").read_text(encoding="utf-8"))
    assert ubuntu["platform"] == "linux/amd64"
    assert ubuntu["tag_media_type"] == "application/vnd.oci.image.index.v1+json"
    assert ubuntu["platform_manifest_media_type"] == "application/vnd.oci.image.manifest.v1+json"
    assert ubuntu["digest_pinned_in_dockerfile"] == "platform_manifest"
    assert f"ubuntu:22.04@{ubuntu['platform_manifest_digest']}" in text
    assert ubuntu["index_digest"] not in text.split("FROM", 1)[1].split("\n", 1)[0]
    observations = json.loads((_DEPLOY / "historical-observations.json").read_text(encoding="utf-8"))
    assert f"MINIFORGE_SHA256={observations['installer_sha256']}" in text
    assert "PYROSETTA" not in text
    assert "alphafold" not in text
    governed = (_DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "assert_intended_runtime_layout" in governed
    assert "assert_dalphaball_libraries" in governed
    assert "A local image ID is" in governed
    assert "PYROSETTA_WHEEL_FILENAME" in governed
    assert "ALPHAFOLD_PARAMS_SHA256" in governed
    assert "COPY deploy/pdl1-bindcraft/vendor/" in governed


def _constraints():
    path = _DEPLOY / "check-conda-constraints.py"
    spec = importlib.util.spec_from_file_location("check_conda_constraints", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_and_build_constraints_reject_a_name_only_match() -> None:
    module = _constraints()
    assert module.spec_holds("0.6.0", "cuda126py310hec873cc_200", "jaxlib >=0.6.0,<=0.6.0")
    assert not module.spec_holds("0.6.1", "cuda126py310hec873cc_200", "jaxlib >=0.6.0,<=0.6.0")
    assert module.spec_holds("3.10", "9_cp310", "python_abi 3.10.* *_cp310")
    assert not module.spec_holds("3.10", "9_cp311", "python_abi 3.10.* *_cp310")
    assert module.spec_holds("1!164.3095", "h7cc23a3_3", "x264 >=1!164.3095,<1!165")
    assert not module.spec_holds("1!165", "h7cc23a3_3", "x264 >=1!164.3095,<1!165")
    assert module.spec_holds("2.0.1", "pyhcf101f3_0", "cached_property >=2.0.1,<2.0.2.0a0")
    depends = (_DEPLOY / "conda-bindcraft.depends.tsv").read_text(encoding="utf-8")
    assert "conda\tjax\tjaxlib >=0.6.0,<=0.6.0" in depends
    _findings, failures = module.check_constraints("numpy=1.26.4=hab_0\n", "conda\tnumpy\tnumpy >=2\n")
    assert failures == ["base_lock_constraint_unsatisfied:numpy:numpy >=2"]
    _pip_findings, pip_failures = module.check_constraints(
        "pdbfixer=1.12=hab_0\n",
        "conda\tpdbfixer\tpyrosetta\npip\tpyrosetta\t2026.29+releasequarterly.80a0635615\t\n",
    )
    assert pip_failures == ["base_lock_pip_replacement:pyrosetta"]
    virtuals, virtual_failures = module.check_constraints(
        "libgcc=16.2.0=ha9f2e26_5\n_openmp_mutex=4.5=20_gnu\n",
        "conda\tlibgcc\t__glibc >=2.17,<3.0.a0\nconda\tlibgcc\t_openmp_mutex >=4.5\nconda\t_openmp_mutex\t\n",
    )
    assert virtual_failures == []
    assert "unresolved_virtual\t__glibc\t1" in virtuals
    assert "metadata_is_not_a_reproducible_build" in virtuals


def test_dependency_names_are_checked_and_a_marker_is_not_closure() -> None:
    assert dependency_gaps({"python": ["libffi >=3.4", "__glibc >=2.17"], "libffi": []}) == ()
    assert dependency_gaps({"python": ["openssl >=3"]}) == ("openssl",)
    assert dependency_gaps({"python": ["pyrosetta"]}, pip_names=frozenset({"pyrosetta"})) == ()


def _conda_record(directory: Path, filename: str, payload: dict[str, object]) -> None:
    (directory / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_inventory_rejects_duplicate_records_in_one_prefix(tmp_path: Path) -> None:
    module = _inventory()
    bindcraft = tmp_path / "conda-meta"
    bindcraft.mkdir()
    payload = {"name": "python", "version": "3.10.21", "build": "h267e890_0_cpython", "schannel": "conda-forge"}
    _conda_record(bindcraft, "python.json", payload)
    _conda_record(bindcraft, "python-again.json", {**payload, "version": "9", "build": "other"})
    with pytest.raises(module.InventoryConflict) as conflict:
        module.read_prefix(bindcraft, "/opt/conda/envs/BindCraft")
    assert conflict.value.prefix == "/opt/conda/envs/BindCraft"
    assert conflict.value.name == "python"


def test_inventory_reports_shared_names_without_merging(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _inventory()
    bindcraft = tmp_path / "envs" / "BindCraft" / "conda-meta"
    bootstrap = tmp_path / "conda-meta"
    bindcraft.mkdir(parents=True)
    bootstrap.mkdir()
    _conda_record(
        bindcraft,
        "python.json",
        {
            "name": "python",
            "version": "3.10.21",
            "build": "h267e890_0_cpython",
            "depends": ["libffi >=3.4", "https://user:token@example.invalid/spec"],
            "channel": "https://user:token@conda.anaconda.org/conda-forge/linux-64",
        },
    )
    _conda_record(
        bindcraft,
        "anyio.json",
        {
            "name": "anyio",
            "version": "4.8.0",
            "build": "pyhd8ed1ab_0",
            "depends": ["typing_extensions >=4"],
            "schannel": "conda-forge",
        },
    )
    _conda_record(
        bootstrap,
        "python.json",
        {
            "name": "python",
            "version": "3.14.7",
            "build": "hcd007b5_106_cp314",
            "depends": ["libffi >=3.4"],
            "schannel": "conda-forge",
        },
    )
    _conda_record(
        bootstrap,
        "anyio.json",
        {
            "name": "anyio",
            "version": "4.3.0",
            "build": "pyhd8ed1ab_1",
            "depends": ["idna >=2"],
            "schannel": "conda-forge",
        },
    )
    records = module.records_by_prefix(
        {
            module.BINDCRAFT_PREFIX: bindcraft,
            module.BOOTSTRAP_PREFIX: bootstrap,
        }
    )
    assert records[(module.BINDCRAFT_PREFIX, "anyio")]["version"] == "4.8.0"
    assert records[(module.BOOTSTRAP_PREFIX, "anyio")]["version"] == "4.3.0"
    selected = module.bindcraft_rows(records)
    assert [(row["name"], row["version"]) for row in selected] == [
        ("anyio", "4.8.0"),
        ("python", "3.10.21"),
    ]
    assert module.inventory(bindcraft, bootstrap) == 0
    printed = capsys.readouterr().out
    assert "conflict\t" not in printed
    assert "token" not in printed
    assert "section\tbindcraft\t/opt/conda/envs/BindCraft" in printed
    assert "section\tbootstrap\t/opt/conda" in printed
    assert "section\tpip\t/opt/conda/envs/BindCraft" in printed
    assert "conda\t/opt/conda/envs/BindCraft\tanyio\t4.8.0\tpyhd8ed1ab_0\tconda-forge\ttyping_extensions >=4" in printed
    assert "conda\t/opt/conda\tanyio\t4.3.0\tpyhd8ed1ab_1\tconda-forge\tidna >=2" in printed
    assert "conda\t/opt/conda/envs/BindCraft\tpython\t3.10.21\th267e890_0_cpython\tconda.anaconda.org/conda-forge\tlibffi >=3.4,redacted" in printed
    assert "conda\t/opt/conda\tpython\t3.14.7\thcd007b5_106_cp314\tconda-forge\tlibffi >=3.4" in printed
    bindcraft_body = printed.split("section\tbindcraft\t/opt/conda/envs/BindCraft", maxsplit=1)[1]
    bindcraft_body = bindcraft_body.split("section\tbootstrap\t/opt/conda", maxsplit=1)[0]
    bootstrap_body = printed.split("section\tbootstrap\t/opt/conda", maxsplit=1)[1]
    bootstrap_body = bootstrap_body.split("section\tinstaller", maxsplit=1)[0]
    assert "3.14.7" not in bindcraft_body
    assert "4.8.0" not in bootstrap_body
    assert "h267e890_0_cpython" not in bootstrap_body
