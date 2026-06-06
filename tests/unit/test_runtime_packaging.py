from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
import importlib.util
import tomllib

import pytest

from AINDY._version import __version__ as runtime_package_version
from AINDY.platform_layer import registry
from AINDY.runtime_only import main as runtime_only_entrypoint_main


pytestmark = pytest.mark.runtime_only

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_package_metadata_declares_console_entrypoints():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "aindy-runtime"
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["project"]["description"] == (
        "Self-hostable AI agent execution runtime — syscall contract, DAG flows, vector memory, plugin registry"
    )
    assert pyproject["project"]["scripts"] == {
        "aindy-runtime": "AINDY.runtime_only:main",
    }
    assert pyproject["project"]["urls"] == {
        "Homepage": "https://github.com/Masterplanner25/aindy-runtime",
        "Documentation": "https://github.com/Masterplanner25/aindy-runtime/tree/main/docs/runtime",
        "Source": "https://github.com/Masterplanner25/aindy-runtime",
        "Issues": "https://github.com/Masterplanner25/aindy-runtime/issues",
    }
    assert pyproject["tool"]["setuptools"]["package-data"]["AINDY"] == [
        "*.json",
        "**/*.json",
        "platform/dist/**",
    ]
    assert pyproject["project"]["optional-dependencies"]["release"] == [
        "build==1.3.0",
        "twine==6.2.0",
    ]
    assert callable(runtime_only_entrypoint_main)


def test_default_app_manifest_prefers_working_directory_for_installed_runtime(monkeypatch, tmp_path):
    apps_repo = tmp_path / "apps-repo"
    nested_workdir = apps_repo / "services" / "api"
    nested_workdir.mkdir(parents=True)
    app_manifest = apps_repo / "aindy_plugins.json"
    app_manifest.write_text('{"profiles": {"default-apps": {"plugins": ["apps.bootstrap"]}}}', encoding="utf-8")

    monkeypatch.chdir(nested_workdir)
    monkeypatch.setattr(
        registry,
        "_source_checkout_app_manifest_path",
        lambda: tmp_path / "missing-source-manifest.json",
    )

    assert registry._default_app_manifest_path() == app_manifest


def test_runtime_build_artifacts_include_runtime_owned_assets(tmp_path):
    if importlib.util.find_spec("wheel") is None:
        pytest.skip("wheel is required in the local interpreter to verify built artifacts without isolation")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    wheel_path = next(tmp_path.glob("aindy_runtime-*.whl"))
    sdist_path = next(tmp_path.glob("aindy_runtime-*.tar.gz"))

    dist_info_dir = f"aindy_runtime-{runtime_package_version}.dist-info"
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_names = set(wheel.namelist())
        entry_points = wheel.read(f"{dist_info_dir}/entry_points.txt").decode("utf-8")
        metadata = wheel.read(f"{dist_info_dir}/METADATA").decode("utf-8")

    assert "AINDY/runtime_plugins.json" in wheel_names
    assert "AINDY/version.json" in wheel_names
    assert "AINDY/system_manifest.json" in wheel_names
    assert "AINDY/nodus/stdlib/.nodus/deps.json" in wheel_names
    assert "aindy-runtime = AINDY.runtime_only:main" in entry_points
    assert "Home-page: https://github.com/Masterplanner25/aindy-runtime" in metadata
    assert "Project-URL: Documentation, https://github.com/Masterplanner25/aindy-runtime/tree/main/docs/runtime" in metadata

    with tarfile.open(sdist_path, "r:gz") as sdist:
        sdist_names = set(sdist.getnames())

    sdist_root = f"aindy_runtime-{runtime_package_version}"
    assert f"{sdist_root}/docs/runtime/DEPLOYMENT_PROFILES.md" in sdist_names
    assert f"{sdist_root}/AINDY/runtime_plugins.json" in sdist_names
    assert f"{sdist_root}/AINDY/nodus/stdlib/.nodus/deps.json" in sdist_names


def test_installed_cli_help():
    """main() invoked with --help exits 0 and prints usage without touching the database layer.

    This is the automated equivalent of RELEASE_CHECKLIST.md step 5:
    ``aindy-runtime --help`` must exit 0 and display the program name.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv=['aindy-runtime', '--help']; "
            "from AINDY.runtime_only import main; main()",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"aindy-runtime --help exited {result.returncode}; stderr: {result.stderr!r}"
    )
    assert "aindy-runtime" in result.stdout, (
        f"expected 'aindy-runtime' in help output; got: {result.stdout!r}"
    )


def test_installed_cli_help_without_database_url():
    """--help must exit 0 even when DATABASE_URL is absent (CLI-1 guard validation).

    Verifies that the lazy-import guard in runtime_only.py prevents database engine
    creation on --help even when no DATABASE_URL is configured in the environment.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SECRET_KEY")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.argv=['aindy-runtime', '--help']; "
            "from AINDY.runtime_only import main; main()",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"aindy-runtime --help failed without DATABASE_URL; stderr: {result.stderr!r}"
    )
    assert "aindy-runtime" in result.stdout
