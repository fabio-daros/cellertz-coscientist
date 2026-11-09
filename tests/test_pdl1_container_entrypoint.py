"""Container entrypoint tests. No BindCraft, Docker, GPU, or network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cellertz.application.pdl1_container_entrypoint import (
    IMAGE_NO_FILTERS,
    Pdl1EntrypointError,
    derive_settings,
    operational_environment,
    parse_policy,
    run_entrypoint,
    subprocess_runner,
    worker_argv,
)
from cellertz.application.pdl1_container_entrypoint import (
    main as entrypoint_main,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    CONTAINER_NO_FILTERS,
)

SOURCE = {
    "design_path": "/content/drive/My Drive/BindCraft/PDL1/",
    "binder_name": "PDL1",
    "starting_pdb": "/content/bindcraft/example/PDL1.pdb",
    "chains": "A",
    "target_hotspot_residues": "56",
    "lengths": [65, 150],
    "number_of_final_designs": 100,
}


def _layouts(tmp_path: Path):
    from cellertz.application.pdl1_container_entrypoint import ContainerLayout, ImageLayout

    inputs = tmp_path / "input"
    workspace = tmp_path / "work"
    output = tmp_path / "output"
    image = tmp_path / "image"
    inputs.mkdir()
    image.mkdir()
    script = image / "bindcraft.py"
    filters = image / "no_filters.json"
    script.write_text("print('unused')\n", encoding="utf-8")
    filters.write_text("{}\n", encoding="utf-8")
    settings = inputs / "settings.json"
    raw = json.dumps(SOURCE, indent=2, sort_keys=True) + "\n"
    settings.write_text(raw, encoding="utf-8")
    advanced = inputs / "advanced.json"
    advanced.write_text('{"max_trajectories": 1}\n', encoding="utf-8")
    target = inputs / "target.pdb"
    target.write_text("ATOM\n", encoding="utf-8")
    layout = ContainerLayout(
        target_pdb=target,
        settings_json=settings,
        advanced_json=advanced,
        design_records=output / "design_records.csv",
        workspace=workspace,
    )
    owned = ImageLayout(bindcraft_script=script, filters=filters, python=sys.executable)
    return layout, owned, raw.encode()


def _argv(filters: Path) -> list[str]:
    return [
        "--cellertz-seed",
        "7",
        "--length-min",
        "65",
        "--length-max",
        "65",
        "--n-designs",
        "1",
        "--filters",
        str(filters),
    ]


def _policy(owned):
    return parse_policy(_argv(owned.filters), filters_path=str(owned.filters))


def test_derivation_rewrites_only_supported_fields_and_keeps_the_source(tmp_path: Path) -> None:
    layout, owned, raw = _layouts(tmp_path)
    derived = derive_settings(layout, _policy(owned))
    written = json.loads(derived.settings_path.read_text(encoding="utf-8"))
    assert written["starting_pdb"] == str(layout.target_pdb)
    assert written["design_path"] == str(derived.design_path)
    assert written["lengths"] == [65, 65]
    assert written["number_of_final_designs"] == 1
    assert written["binder_name"] == "PDL1"
    assert written["chains"] == "A"
    assert written["target_hotspot_residues"] == "56"
    assert layout.settings_json.read_bytes() == raw
    assert layout.advanced_json.read_bytes() == b'{"max_trajectories": 1}\n'
    assert layout.target_pdb.read_bytes() == b"ATOM\n"
    assert derived.settings_path.parent == layout.workspace
    assert not str(derived.settings_path).startswith(str(layout.settings_json.parent))


def test_an_explicit_empty_hotspot_replaces_the_staged_example(tmp_path: Path) -> None:
    layout, owned, raw = _layouts(tmp_path)
    derived = derive_settings(layout, replace(_policy(owned), hotspot=""))
    written = json.loads(derived.settings_path.read_text(encoding="utf-8"))
    assert written["target_hotspot_residues"] == ""
    assert layout.settings_json.read_bytes() == raw
    assert b'"56"' in raw


def test_missing_scientific_override_is_rejected(tmp_path: Path) -> None:
    _layout, owned, _raw = _layouts(tmp_path)
    argv = _argv(owned.filters)
    argv.remove("--cellertz-seed")
    argv.remove("7")
    with pytest.raises(Pdl1EntrypointError) as caught:
        parse_policy(argv, filters_path=str(owned.filters))
    assert caught.value.code == "policy_incomplete"


def test_unsupported_argument_is_rejected(tmp_path: Path) -> None:
    _layout, owned, _raw = _layouts(tmp_path)
    with pytest.raises(Pdl1EntrypointError) as caught:
        parse_policy([* _argv(owned.filters), "--hotspot", "56"], filters_path=str(owned.filters))
    assert caught.value.code == "unsupported_argument"


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--cellertz-seed", "-1"),
        ("--cellertz-seed", "true"),
        ("--length-min", "0"),
        ("--length-max", "64"),
        ("--n-designs", "0"),
        ("--n-designs", "65"),
        ("--length-min", "150"),
    ],
)
def test_malformed_scientific_values_are_rejected(tmp_path: Path, flag: str, value: str) -> None:
    _layout, owned, _raw = _layouts(tmp_path)
    argv = _argv(owned.filters)
    if flag == "--length-max" and value == "64":
        argv[argv.index("--length-min") + 1] = "65"
        argv[argv.index("--length-max") + 1] = "64"
    elif flag == "--length-min" and value == "150":
        argv[argv.index("--length-min") + 1] = "150"
        argv[argv.index("--length-max") + 1] = "65"
    else:
        argv[argv.index(flag) + 1] = value
    with pytest.raises(Pdl1EntrypointError) as caught:
        parse_policy(argv, filters_path=str(owned.filters))
    assert caught.value.code == "malformed_value"


def test_filter_path_must_be_the_image_policy(tmp_path: Path) -> None:
    _layout, owned, _raw = _layouts(tmp_path)
    other = tmp_path / "default_filters.json"
    other.write_text("{}\n", encoding="utf-8")
    argv = _argv(owned.filters)
    argv[-1] = str(other)
    with pytest.raises(Pdl1EntrypointError) as caught:
        parse_policy(argv, filters_path=str(owned.filters))
    assert caught.value.code == "filter_policy_rejected"
    assert IMAGE_NO_FILTERS == CONTAINER_NO_FILTERS


def test_child_seed_follows_numpy_and_ignores_the_parent(tmp_path: Path) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    script = owned.bindcraft_script
    script.write_text(
        "import numpy as np\n"
        "print(int(np.random.randint(0, high=999999, size=1, dtype=int)[0]))\n",
        encoding="utf-8",
    )
    np.random.seed(1)
    policy = _policy(owned)
    derived = derive_settings(layout, policy)
    completed = subprocess.run(
        worker_argv(policy, owned, derived),
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    expected = int(np.random.RandomState(7).randint(0, high=999999, size=1, dtype=int)[0])
    decoy = int(np.random.RandomState(1).randint(0, high=999999, size=1, dtype=int)[0])
    assert expected != decoy
    assert completed.returncode == 0
    assert completed.stdout.strip() == str(expected)


def test_runner_is_a_shell_false_process_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["shell"] = kwargs["shell"]
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    code = subprocess_runner(["/usr/bin/python", "worker"], {"PATH": "/usr/bin"})
    assert code == 0
    assert seen["shell"] is False
    assert type(seen["argv"]) is list
    assert seen["env"]["XLA_PYTHON_CLIENT_PREALLOCATE"] == "false"
    assert "CELLERTZ_SEED" not in seen["env"]


def test_nonzero_exit_and_launch_failure_publish_nothing(tmp_path: Path) -> None:
    layout, owned, raw = _layouts(tmp_path)
    policy = _policy(owned)

    def fail(argv, env):
        assert type(argv) is list
        assert env["PYTHONUNBUFFERED"] == "1"
        return 3

    with pytest.raises(Pdl1EntrypointError) as caught:
        run_entrypoint(layout, owned, policy, runner=fail)
    assert caught.value.code == "bindcraft_failed"
    assert not layout.design_records.exists()
    assert layout.settings_json.read_bytes() == raw

    def explode(argv, env):
        raise OSError("no such file")

    fresh = replace(layout, workspace=tmp_path / "work-launch")
    with pytest.raises(Pdl1EntrypointError) as launched:
        run_entrypoint(fresh, owned, policy, runner=explode)
    assert launched.value.code == "launch_failed"
    assert not layout.design_records.exists()


def test_missing_and_header_only_output_are_not_manufactured(tmp_path: Path) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    policy = _policy(owned)

    def missing(argv, env):
        return 0

    with pytest.raises(Pdl1EntrypointError) as absent:
        run_entrypoint(layout, owned, policy, runner=missing)
    assert absent.value.code == "design_records_missing"
    assert not layout.design_records.exists()

    fresh = replace(layout, workspace=tmp_path / "work-header")

    def header_only(argv, env):
        stats = fresh.workspace / "designs" / "final_design_stats.csv"
        stats.write_text("Design,Sequence\n", encoding="utf-8")
        return 0

    with pytest.raises(Pdl1EntrypointError) as empty:
        run_entrypoint(fresh, owned, policy, runner=header_only)
    assert empty.value.code == "design_records_header_only"
    assert "validation" in empty.value.detail
    assert not layout.design_records.exists()


def test_valid_stats_are_published_only_at_the_executor_path(tmp_path: Path) -> None:
    layout, owned, raw = _layouts(tmp_path)
    policy = _policy(owned)
    payload = "Design,Sequence,Average_i_pTM\nbinder1,ACDEFGHIK,0.5\n"

    def succeed(argv, env):
        assert "--cellertz-seed" in argv
        assert argv[argv.index("--cellertz-seed") + 1] == "7"
        assert str(layout.settings_json) not in argv
        derived = json.loads(
            (layout.workspace / "settings.json").read_text(encoding="utf-8")
        )
        assert derived["number_of_final_designs"] == 1
        stats = Path(derived["design_path"]) / "final_design_stats.csv"
        stats.parent.mkdir(parents=True, exist_ok=True)
        stats.write_text(payload, encoding="utf-8")
        return 0

    published = run_entrypoint(layout, owned, policy, runner=succeed)
    assert published == layout.design_records
    assert layout.design_records.read_text(encoding="utf-8") == payload
    assert list(layout.design_records.parent.iterdir()) == [layout.design_records]
    assert layout.settings_json.read_bytes() == raw


def test_invalid_sequence_is_not_published(tmp_path: Path) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    policy = _policy(owned)

    def bad(argv, env):
        stats = layout.workspace / "designs" / "final_design_stats.csv"
        stats.parent.mkdir(parents=True, exist_ok=True)
        stats.write_text("Design,Sequence\nbinder1,not-peptide\n", encoding="utf-8")
        return 0

    with pytest.raises(Pdl1EntrypointError) as caught:
        run_entrypoint(layout, owned, policy, runner=bad)
    assert caught.value.code == "design_records_invalid"
    assert not layout.design_records.exists()


def test_operational_environment_sets_jax_preallocate_without_a_seed() -> None:
    env = operational_environment({"PATH": "/usr/bin", "HOME": "/tmp", "LD_LIBRARY_PATH": "/usr/lib"})
    assert env["XLA_PYTHON_CLIENT_PREALLOCATE"] == "false"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PATH"] == "/usr/bin"
    assert env["LD_LIBRARY_PATH"] == "/opt/conda/envs/BindCraft/lib:/opt/conda/lib:/usr/lib"
    assert "7" not in env.values()


def test_module_cli_rejects_a_partial_policy(capsys: pytest.CaptureFixture[str]) -> None:
    code = entrypoint_main(["--cellertz-seed", "7"])
    assert code == 1
    assert capsys.readouterr().err.strip() == "policy_incomplete"


def test_preexisting_stats_are_not_published(tmp_path: Path) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    stats = layout.workspace / "designs" / "final_design_stats.csv"
    stats.parent.mkdir(parents=True)
    stats.write_text("Design,Sequence\nkept,ACDEFGHIK\n", encoding="utf-8")
    called = False

    def runner(argv, env):
        nonlocal called
        called = True
        return 0

    with pytest.raises(Pdl1EntrypointError) as caught:
        run_entrypoint(layout, owned, _policy(owned), runner=runner)
    assert caught.value.code == "workspace_not_fresh"
    assert called is False
    assert not layout.design_records.exists()
    assert stats.read_text(encoding="utf-8").startswith("Design,Sequence\nkept")


def test_short_reads_reconstruct_the_complete_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    payload = "Design,Sequence\nbinder1,ACDEFGHIK\nbinder2,LMNPQRSTV\n"
    first = "Design,Sequence\nbinder1,ACDEFGHIK\n"
    assert payload.startswith(first) and len(payload) > len(first)
    width = len(first.encode())
    calls = {"n": 0}
    real_read = os.read

    def short_read(descriptor: int, size: int) -> bytes:
        calls["n"] += 1
        return real_read(descriptor, min(size, width))

    def write_both(argv, env):
        stats = layout.workspace / "designs" / "final_design_stats.csv"
        stats.write_text(payload, encoding="utf-8")
        return 0

    monkeypatch.setattr(os, "read", short_read)
    published = run_entrypoint(layout, owned, _policy(owned), runner=write_both)
    assert calls["n"] >= 2
    assert published.read_text(encoding="utf-8") == payload


def test_read_failure_after_a_valid_row_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    payload = "Design,Sequence\nbinder1,ACDEFGHIK\nbinder2,LMNPQRSTV\n"
    width = len(b"Design,Sequence\nbinder1,ACDEFGHIK\n")
    state = {"once": False}
    real_read = os.read

    def fail_after_row(descriptor: int, size: int) -> bytes:
        if not state["once"]:
            state["once"] = True
            return real_read(descriptor, min(size, width))
        raise OSError("read interrupted")

    def write_both(argv, env):
        stats = layout.workspace / "designs" / "final_design_stats.csv"
        stats.write_text(payload, encoding="utf-8")
        return 0

    monkeypatch.setattr(os, "read", fail_after_row)
    with pytest.raises(Pdl1EntrypointError) as caught:
        run_entrypoint(layout, owned, _policy(owned), runner=write_both)
    assert caught.value.code == "design_records_invalid"
    assert "read interrupted" not in caught.value.detail
    assert not layout.design_records.exists()


def test_cumulative_read_over_the_ceiling_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cellertz.application import pdl1_container_entrypoint as entrypoint

    layout, owned, _raw = _layouts(tmp_path)
    first = b"Design,Sequence\nbinder1,ACDEFGHIK\n"
    extra = b"binder2,LMNPQRSTV\n"
    monkeypatch.setattr(entrypoint, "_MAX_RECORD_BYTES", len(first))
    pending = bytearray(first + extra)

    def overflowing(descriptor: int, size: int) -> bytes:
        if not pending:
            return b""
        count = min(size, len(first))
        chunk = bytes(pending[:count])
        del pending[:count]
        return chunk

    def write_within_ceiling(argv, env):
        stats = layout.workspace / "designs" / "final_design_stats.csv"
        stats.write_bytes(first)
        return 0

    monkeypatch.setattr(os, "read", overflowing)
    with pytest.raises(Pdl1EntrypointError) as caught:
        run_entrypoint(layout, owned, _policy(owned), runner=write_within_ceiling)
    assert caught.value.code == "design_records_invalid"
    assert "ceiling" in caught.value.detail
    assert not layout.design_records.exists()


def test_oversized_stats_are_rejected_without_loading_the_file(tmp_path: Path) -> None:
    layout, owned, _raw = _layouts(tmp_path)
    policy = _policy(owned)

    def huge(argv, env):
        stats = layout.workspace / "designs" / "final_design_stats.csv"
        descriptor = os.open(stats, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.truncate(descriptor, 1_073_741_825)
        finally:
            os.close(descriptor)
        return 0

    with pytest.raises(Pdl1EntrypointError) as caught:
        run_entrypoint(layout, owned, policy, runner=huge)
    assert caught.value.code == "design_records_invalid"
    assert "ceiling" in caught.value.detail
    assert not layout.design_records.exists()


def test_main_rejects_non_ascii_and_overlong_numbers(capsys: pytest.CaptureFixture[str]) -> None:
    filters = "/opt/BindCraft/settings_filters/no_filters.json"
    unicode_argv = [
        "--cellertz-seed",
        "٣",
        "--length-min",
        "65",
        "--length-max",
        "65",
        "--n-designs",
        "1",
        "--filters",
        filters,
    ]
    assert entrypoint_main(unicode_argv) == 1
    first = capsys.readouterr()
    assert first.err.strip() == "malformed_value"
    assert "Traceback" not in first.err
    assert "ValueError" not in first.err
    overlong = [
        "--cellertz-seed",
        "7",
        "--length-min",
        "65",
        "--length-max",
        "65",
        "--n-designs",
        "1" * 40,
        "--filters",
        filters,
    ]
    assert entrypoint_main(overlong) == 1
    second = capsys.readouterr()
    assert second.err.strip() == "malformed_value"
    assert "Traceback" not in second.err


def test_main_converts_a_missing_settings_file(capsys: pytest.CaptureFixture[str]) -> None:
    code = entrypoint_main(
        [
            "--cellertz-seed",
            "7",
            "--length-min",
            "65",
            "--length-max",
            "65",
            "--n-designs",
            "1",
            "--filters",
            "/opt/BindCraft/settings_filters/no_filters.json",
        ]
    )
    assert code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "settings_unreadable"
    assert "Traceback" not in captured.err
