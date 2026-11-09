"""Image pins fail closed. No Docker, registry, weight, or wheel download."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cellertz.application.pdl1_container_entrypoint import (
    IMAGE_BINDCRAFT_SCRIPT,
    IMAGE_NO_FILTERS,
)
from cellertz.application.pdl1_image_readiness import (
    ALPHAFOLD_PARAMS_NAME,
    BINDCRAFT_COMMIT,
    BINDCRAFT_CONDA_PACKAGES,
    BINDCRAFT_JAX,
    BINDCRAFT_JAXLIB_CUDA,
    BINDCRAFT_PYTHON,
    CUDA_COMPATIBILITY_URL,
    IMAGE_ENTRYPOINT,
    PRESENT_MODULES,
    PYTHON_310_DYNAMIC_IMPORT,
    REQUIRED_EXECUTABLES,
    REQUIRED_LIBRARIES,
    SCHEMA,
    UNRESOLVED_BLOCKERS,
    EnvironmentSnapshot,
    ImageReadinessError,
    IntendedRuntimeLayout,
    assert_bindcraft_files,
    assert_dalphaball_libraries,
    assert_intended_runtime_layout,
    build_plan,
    environment_failures,
    pin_failures,
    prepend_library_path,
    require_pins,
    unresolved_ldd_libraries,
    wheel_filename_accepted,
)

_DIGEST = "sha256:" + ("ab" * 32)
_FILE_HASH = "cd" * 32
_COMMIT = "a" * 40
_WHEEL_NAME = "pyrosetta-0.0.0-py3-none-any.whl"
_ROOT = Path(__file__).resolve().parents[1]


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": SCHEMA,
        "base_image_repository": "registry.example/cuda",
        "base_image_digest": _DIGEST,
        "host_nvidia_driver": "580.65.06",
        "cuda_version": "12.4",
        "colabdesign_commit": _COMMIT,
        "pyrosetta_wheel_filename": _WHEEL_NAME,
        "pyrosetta_wheel_sha256": _FILE_HASH,
        "alphafold_params_sha256": _FILE_HASH,
    }
    document.update(overrides)
    return document


def _layout(tmp_path: Path, wheel_name: str = _WHEEL_NAME) -> tuple[Path, Path]:
    vendor = tmp_path / "deploy" / "pdl1-bindcraft" / "vendor"
    vendor.mkdir(parents=True)
    source = _ROOT / "deploy" / "pdl1-bindcraft" / "Dockerfile"
    destination = tmp_path / "deploy" / "pdl1-bindcraft" / "Dockerfile"
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    wheel = vendor / wheel_name
    params = vendor / ALPHAFOLD_PARAMS_NAME
    wheel.write_bytes(b"wheel-bytes")
    params.write_bytes(b"params-bytes")
    return wheel, params


def _endpoint(root: Path) -> dict[str, str]:
    return {"docker_host": f"unix://{root / 'docker.sock'}", "docker_root": str(root)}


def _complete_environment() -> EnvironmentSnapshot:
    imports: dict[str, str | None] = {name: "" for name in PRESENT_MODULES}
    imports.update({"numpy": "1.26.4", "flax": "0.9.0", "jax": "0.4.30", "jaxlib": "0.4.30"})
    return EnvironmentSnapshot(
        python=(3, 10),
        imports=imports,
        executables=frozenset(REQUIRED_EXECUTABLES),
        libraries=frozenset(REQUIRED_LIBRARIES),
    )


def test_repository_paths_match_the_adapter() -> None:
    assert IMAGE_BINDCRAFT_SCRIPT == "/opt/BindCraft/bindcraft.py"
    assert IMAGE_NO_FILTERS == "/opt/BindCraft/settings_filters/no_filters.json"
    assert BINDCRAFT_COMMIT == "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
    assert CUDA_COMPATIBILITY_URL.startswith("https://docs.nvidia.com/deploy/cuda-compatibility/")
    assert BINDCRAFT_PYTHON == "3.10"
    assert "numpy<2.0.0" in BINDCRAFT_CONDA_PACKAGES
    assert "flax<0.10.0" in BINDCRAFT_CONDA_PACKAGES
    assert BINDCRAFT_JAX == "jax>=0.4,<=0.6.0"
    assert BINDCRAFT_JAXLIB_CUDA == "jaxlib>=0.4,<=0.6.0=*cuda*"


def test_missing_pins_fail_before_a_build_argument_exists() -> None:
    failures = pin_failures({"schema": SCHEMA})
    assert "base_image_digest_required" in failures
    assert "colabdesign_commit_required" in failures
    assert "pyrosetta_wheel_sha256_required" in failures
    assert "alphafold_params_sha256_required" in failures
    assert "host_nvidia_driver_required" in failures
    assert "cuda_version_required" in failures
    with pytest.raises(ImageReadinessError) as error:
        require_pins({"schema": SCHEMA})
    assert error.value.code == "image_pins_incomplete"
    assert "docker" not in error.value.code


def test_unknown_or_guessed_values_are_rejected() -> None:
    assert pin_failures({"schema": SCHEMA, "token": "secret"}) == ("image_pins_rejected",)
    assert "colabdesign_commit_required" in pin_failures(_document(colabdesign_commit="HEAD"))
    assert "base_image_digest_required" in pin_failures(_document(base_image_digest="latest"))
    assert "cuda_version_required" in pin_failures(_document(cuda_version=""))


def test_wheel_format_is_not_abi_compatibility() -> None:
    assert wheel_filename_accepted(_WHEEL_NAME) is True
    assert wheel_filename_accepted("pyrosetta-0.0.0-1-py3-none-any.whl") is True
    assert wheel_filename_accepted("pyrosetta-0.whl") is False
    assert "pyrosetta_wheel_required" in pin_failures(
        _document(pyrosetta_wheel_filename="pyrosetta-0.whl")
    )
    assert "pyrosetta_abi_unverified" in UNRESOLVED_BLOCKERS


def test_matching_files_return_a_plan_that_does_not_run(tmp_path: Path) -> None:
    wheel, params = _layout(tmp_path)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    document = _document(
        pyrosetta_wheel_sha256=digest,
        alphafold_params_sha256=hashlib.sha256(params.read_bytes()).hexdigest(),
    )
    plan = build_plan(document, wheel=wheel, params=params, root=tmp_path, **_endpoint(tmp_path))
    context = Path(plan.argv[-1])
    dockerfile = Path(plan.argv[plan.argv.index("--file") + 1])
    assert plan.bindcraft_commit == BINDCRAFT_COMMIT
    assert plan.entrypoint == IMAGE_ENTRYPOINT
    assert plan.host_acceptance_complete is False
    assert plan.scientific_run_execution == "disabled"
    assert plan.wheel_abi_verified is False
    assert plan.unresolved_blockers == UNRESOLVED_BLOCKERS
    assert context == tmp_path.resolve()
    assert dockerfile == (tmp_path / "deploy" / "pdl1-bindcraft" / "Dockerfile").resolve()
    assert wheel.resolve().parent == (context / "deploy" / "pdl1-bindcraft" / "vendor").resolve()
    rendered = " ".join(plan.argv)
    endpoint = f"unix://{tmp_path / 'docker.sock'}"
    assert plan.argv[0:4] == ("docker", "--host", endpoint, "build")
    assert f"BASE_IMAGE=registry.example/cuda@{_DIGEST}" in rendered
    assert f"COLABDESIGN_COMMIT={_COMMIT}" in rendered
    assert f"PYROSETTA_WHEEL_FILENAME={_WHEEL_NAME}" in rendered
    assert f"CUDA_VERSION={document['cuda_version']}" in rendered
    assert BINDCRAFT_COMMIT not in rendered
    assert "storage.googleapis.com" not in rendered
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY deploy/pdl1-bindcraft/vendor/ /tmp/vendor/" in text
    assert 'pip install --no-deps "/tmp/vendor/${PYROSETTA_WHEEL_FILENAME}"' in text


def test_a_hash_mismatch_does_not_return_build_arguments(tmp_path: Path) -> None:
    wheel, params = _layout(tmp_path)
    document = _document(pyrosetta_wheel_sha256="ab" * 32)
    with pytest.raises(ImageReadinessError) as error:
        build_plan(document, wheel=wheel, params=params, root=tmp_path, **_endpoint(tmp_path))
    assert error.value.code == "pyrosetta_hash_mismatch"
    assert error.value.missing == ()


def test_files_outside_the_vendor_layout_are_rejected(tmp_path: Path) -> None:
    wheel, params = _layout(tmp_path)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    document = _document(
        pyrosetta_wheel_sha256=digest,
        alphafold_params_sha256=hashlib.sha256(params.read_bytes()).hexdigest(),
    )
    outside = tmp_path / "outside" / _WHEEL_NAME
    outside.parent.mkdir()
    outside.write_bytes(wheel.read_bytes())
    with pytest.raises(ImageReadinessError) as error:
        build_plan(document, wheel=outside, params=params, root=tmp_path, **_endpoint(tmp_path))
    assert error.value.code == "vendor_layout_rejected"

    vendor = tmp_path / "deploy" / "pdl1-bindcraft" / "vendor"
    escaped = vendor / ".." / ".." / ".." / "outside" / _WHEEL_NAME
    assert escaped.resolve() == outside.resolve()
    with pytest.raises(ImageReadinessError) as escaped_error:
        build_plan(document, wheel=escaped, params=params, root=tmp_path, **_endpoint(tmp_path))
    assert escaped_error.value.code == "vendor_layout_rejected"

    link_name = "pyrosetta-0.0.0-py3-none-linux_x86_64.whl"
    link = wheel.with_name(link_name)
    link.symlink_to(outside)
    assert link.is_symlink()
    assert link.resolve() == outside.resolve()
    linked = _document(
        pyrosetta_wheel_filename=link_name,
        pyrosetta_wheel_sha256=digest,
        alphafold_params_sha256=document["alphafold_params_sha256"],
    )
    with pytest.raises(ImageReadinessError) as link_error:
        build_plan(linked, wheel=link, params=params, root=tmp_path, **_endpoint(tmp_path))
    assert link_error.value.code == "vendor_layout_rejected"


def test_cpu_probe_reports_an_incomplete_base_without_running_bindcraft(tmp_path: Path) -> None:
    complete = _complete_environment()
    assert environment_failures(complete) == ()
    missing = dict(complete.imports)
    missing["numpy"] = None
    assert "numpy_missing" in environment_failures(
        EnvironmentSnapshot(complete.python, missing, complete.executables, complete.libraries)
    )
    rejected = dict(complete.imports)
    rejected["numpy"] = "2.0.0"
    rejected["jax"] = "0.6.1"
    failures = environment_failures(
        EnvironmentSnapshot(complete.python, rejected, complete.executables, complete.libraries)
    )
    assert "numpy_version_rejected" in failures
    assert "jax_version_rejected" in failures
    assert "python_version_rejected" in environment_failures(
        EnvironmentSnapshot((3, 12), complete.imports, complete.executables, complete.libraries)
    )
    assert "git_missing" in environment_failures(
        EnvironmentSnapshot(
            complete.python,
            complete.imports,
            complete.executables - {"git"},
            complete.libraries,
        )
    )
    checkout = tmp_path / "BindCraft"
    (checkout / "settings_filters").mkdir(parents=True)
    (checkout / "functions").mkdir()
    (checkout / "bindcraft.py").write_text("this is not python\n", encoding="utf-8")
    (checkout / "settings_filters" / "no_filters.json").write_text("{}\n", encoding="utf-8")
    binary = checkout / "functions" / "dssp"
    binary.write_bytes(b"not-executed")
    binary.chmod(0o755)
    ball = checkout / "functions" / "DAlphaBall.gcc"
    ball.write_bytes(b"not-executed")
    ball.chmod(0o755)
    assert_bindcraft_files(checkout)
    binary.chmod(0o644)
    with pytest.raises(ImageReadinessError) as error:
        assert_bindcraft_files(checkout)
    assert error.value.code == "base_environment_incomplete"
    assert "dssp" in error.value.missing


def test_library_path_preserves_existing_entries() -> None:
    assert prepend_library_path(None) == "/opt/conda/envs/BindCraft/lib:/opt/conda/lib"
    assert prepend_library_path("/usr/local/lib:/opt/extra") == (
        "/opt/conda/envs/BindCraft/lib:/opt/conda/lib:/usr/local/lib:/opt/extra"
    )
    existing = "/opt/conda/lib:/usr/lib"
    assert prepend_library_path(existing) == f"/opt/conda/envs/BindCraft/lib:{existing}"
    assert prepend_library_path(prepend_library_path("/usr/lib")) == prepend_library_path("/usr/lib")


def test_ldd_success_with_not_found_is_a_missing_library(tmp_path: Path) -> None:
    binary = tmp_path / "DAlphaBall.gcc"
    binary.write_bytes(b"not-executed")
    seen: dict[str, object] = {}

    def runner(argv, *, env, capture_output, text, check):
        del capture_output, text, check
        seen["argv"] = argv
        seen["library_path"] = env["LD_LIBRARY_PATH"]
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="\tlibgfortran.so.5 => not found\n\tlibc.so.6 => /lib/libc.so.6 (0x1)\n",
            stderr="",
        )

    with pytest.raises(ImageReadinessError) as error:
        assert_dalphaball_libraries(
            binary,
            base_environment={"LD_LIBRARY_PATH": "/usr/lib", "PATH": "/usr/bin"},
            runner=runner,
        )
    assert error.value.code == "base_environment_incomplete"
    assert error.value.missing == ("libgfortran.so.5",)
    assert seen["library_path"] == "/opt/conda/envs/BindCraft/lib:/opt/conda/lib:/usr/lib"
    resolved = "\tlibgfortran.so.5 => /opt/conda/envs/BindCraft/lib/libgfortran.so.5 (0x1)\n"

    def resolved_runner(argv, *, env, capture_output, text, check):
        del env, capture_output, text, check
        return subprocess.CompletedProcess(argv, 0, stdout=resolved, stderr="")

    assert_dalphaball_libraries(binary, runner=resolved_runner)
    assert unresolved_ldd_libraries(resolved) == ()


def test_intended_layout_requires_the_bindcraft_python_and_fortran_library(tmp_path: Path) -> None:
    root = tmp_path / "image"
    python = root / "bin" / "python"
    env_lib = root / "env" / "lib"
    conda_lib = root / "conda" / "lib"
    layout = IntendedRuntimeLayout(python=python, library_directories=(env_lib, conda_lib))
    with pytest.raises(ImageReadinessError) as missing:
        assert_intended_runtime_layout(layout)
    assert "python_layout_missing" in missing.value.missing
    assert "library_directory_missing" in missing.value.missing

    python.parent.mkdir(parents=True)
    target = root / "bin" / "python3.10"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o755)
    python.symlink_to(target)
    env_lib.mkdir(parents=True)
    conda_lib.mkdir(parents=True)
    with pytest.raises(ImageReadinessError) as no_fortran:
        assert_intended_runtime_layout(layout)
    assert no_fortran.value.missing == ("libgfortran_so_5_missing",)
    (env_lib / "libgfortran.so.5").symlink_to("libgfortran.so.5.0.0")
    (env_lib / "libgfortran.so.5.0.0").write_bytes(b"library")
    assert_intended_runtime_layout(layout)


def test_dockerfile_checks_the_base_without_executing_bindcraft() -> None:
    text = (_ROOT / "deploy" / "pdl1-bindcraft" / "Dockerfile").read_text(encoding="utf-8")
    assert "assert_base_environment" in text
    assert "assert_intended_runtime_layout" in text
    assert "assert_dalphaball_libraries" in text
    assert "/opt/conda/envs/BindCraft/bin/python" in text
    assert "nvidia/cuda:" not in text
    assert "assert_post_install" in text
    assert "assert_bindcraft_files" in text
    assert "JAX_PLATFORMS=cpu" in text
    assert "pyrosetta.whl" not in text
    assert "import bindcraft" not in text
    for line in text.splitlines():
        if "bindcraft.py" in line:
            assert "python" not in line
            assert "import" not in line
    document = (_ROOT / "docs" / "validation" / "pdl1_image_readiness.md").read_text(
        encoding="utf-8"
    )
    assert "numpy<2.0.0" in document
    assert "pyrosetta_abi_unverified" in document
    assert "bindcraft_required_packages_cuda_inconsistency" in document


def test_intermediate_symlinks_outside_the_context_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    document, _wheel, _params = _pinned(outside)
    outside_deploy = outside / "deploy"
    outside_package = outside_deploy / "pdl1-bindcraft"
    outside_vendor = outside_package / "vendor"

    deploy_root = tmp_path / "deploy-link"
    deploy_root.mkdir()
    (deploy_root / "deploy").symlink_to(outside_deploy)
    _reject_linked_copy(document, deploy_root, outside_vendor)
    assert not (deploy_root / "deploy").resolve().is_relative_to(deploy_root.resolve())

    package_root = tmp_path / "package-link"
    (package_root / "deploy").mkdir(parents=True)
    (package_root / "deploy" / "pdl1-bindcraft").symlink_to(outside_package)
    _reject_linked_copy(document, package_root, outside_vendor)
    assert (
        not (package_root / "deploy" / "pdl1-bindcraft")
        .resolve()
        .is_relative_to(package_root.resolve())
    )

    vendor_root = tmp_path / "vendor-link"
    _layout(vendor_root)
    vendor = vendor_root / "deploy" / "pdl1-bindcraft" / "vendor"
    shutil.rmtree(vendor)
    vendor.symlink_to(outside_vendor)
    _reject_linked_copy(document, vendor_root, outside_vendor)
    assert vendor.is_symlink()
    assert not vendor.resolve().is_relative_to(vendor_root.resolve())


def test_startup_path_does_not_import_the_web_application() -> None:
    wrapper = (_ROOT / "deploy/pdl1-bindcraft/pdl1-entrypoint").read_text(encoding="utf-8")
    dockerfile = (_ROOT / "deploy/pdl1-bindcraft/Dockerfile").read_text(encoding="utf-8")
    declared = set(re.findall(r"python -m ([A-Za-z0-9_.]+)", wrapper))
    declared.update(re.findall(r"from ([A-Za-z0-9_.]+) import", dockerfile))
    cellertz_modules = {name for name in declared if name.startswith("cellertz")}
    assert cellertz_modules == {
        "cellertz.application.pdl1_container_entrypoint",
        "cellertz.application.pdl1_image_readiness",
    }
    closure = _import_closure(cellertz_modules)
    assert closure == {
        "cellertz",
        "cellertz.application",
        "cellertz.application.pdl1_container_entrypoint",
        "cellertz.application.pdl1_image_readiness",
    }
    for name in closure:
        tree = ast.parse(_module_source(name))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                assert "UTC" not in {alias.name for alias in node.names}
            if isinstance(node, ast.Attribute):
                assert node.attr != "UTC"
    project = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in project
    assert BINDCRAFT_PYTHON == "3.10"


def test_python_310_dynamic_import_stays_pending_without_that_interpreter() -> None:
    document = (_ROOT / "docs/validation/pdl1_image_readiness.md").read_text(encoding="utf-8")
    binary = shutil.which("python3.10")
    if binary is None and sys.version_info[:2] != (3, 10):
        assert PYTHON_310_DYNAMIC_IMPORT == "pending"
        collapsed = " ".join(document.split())
        assert "Python 3.10 dynamic import check is pending" in collapsed
        assert "A test run on the developer interpreter is not that check." in collapsed
        return
    executable = binary if binary is not None else sys.executable
    probe = (
        "import sys\n"
        "import cellertz.application.pdl1_container_entrypoint\n"
        "import cellertz.application.pdl1_image_readiness\n"
        "loaded = sorted(name for name in sys.modules if name in {\n"
        '    "cellertz.application.conversation",\n'
        '    "cellertz.application.contracts",\n'
        '    "cellertz.application.fixtures",\n'
        "})\n"
        "if loaded:\n"
        '    raise SystemExit("web application loaded: " + ",".join(loaded))\n'
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_ROOT / "src")
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [executable, "-S", "-c", probe],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_public_application_exports_remain_importable() -> None:
    from cellertz import application

    assert set(application.__all__) == set(application._EXPORTS)
    for name in application.__all__:
        assert getattr(application, name) is not None


def _pinned(root: Path) -> tuple[dict[str, object], Path, Path]:
    wheel, params = _layout(root)
    document = _document(
        pyrosetta_wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        alphafold_params_sha256=hashlib.sha256(params.read_bytes()).hexdigest(),
    )
    return document, wheel, params


def _reject_linked_copy(document: dict[str, object], root: Path, vendor: Path) -> None:
    wheel = root / "deploy" / "pdl1-bindcraft" / "vendor" / _WHEEL_NAME
    params = root / "deploy" / "pdl1-bindcraft" / "vendor" / ALPHAFOLD_PARAMS_NAME
    assert wheel.resolve() == (vendor / _WHEEL_NAME).resolve()
    assert not wheel.is_symlink()
    with pytest.raises(ImageReadinessError) as error:
        build_plan(document, wheel=wheel, params=params, root=root, **_endpoint(root))
    assert error.value.code == "vendor_layout_rejected"


def _module_source(module: str) -> str:
    relative = Path(*module.split("."))
    package = _ROOT / "src" / relative / "__init__.py"
    module_file = _ROOT / "src" / relative.with_suffix(".py")
    if module_file.is_file():
        return module_file.read_text(encoding="utf-8")
    return package.read_text(encoding="utf-8")


def _import_closure(modules: set[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(modules)
    for name in list(modules):
        stack.extend(_parents(name))
    while stack:
        name = stack.pop()
        if name in seen or not name.startswith("cellertz"):
            continue
        seen.add(name)
        tree = ast.parse(_module_source(name))
        for imported in _startup_imports(tree):
            if imported.startswith("cellertz"):
                stack.append(imported)
                stack.extend(_parents(imported))
    return seen


def _parents(module: str) -> list[str]:
    parts = module.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def _startup_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            "assert_base_environment",
            "assert_post_install",
        }:
            found.update(_import_module_calls(node))
    return found


def _import_module_calls(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not child.args:
            continue
        func = child.func
        if not isinstance(func, ast.Attribute) or func.attr != "import_module":
            continue
        argument = child.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            found.add(argument.value)
    return found
