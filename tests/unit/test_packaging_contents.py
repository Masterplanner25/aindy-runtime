"""What the distribution must and must not carry.

Found during an archive audit: `llms.txt` and `llms-full.txt` lived only at the repo root, so
they shipped in neither the wheel nor the sdist. They exist so a model reading the *installed*
package can orient itself — at the repo root they served a reader who had already found the
repo, which is the audience that needed them least.

The same audit asked whether the Rust scorer ships. It does not, deliberately: the backend
produces a pure-Python `py3-none-any` wheel, and a compiled `.pyd`/`.so` inside one would
install a broken binary for every user on a different OS/arch/CPython. The **source** ships in
the sdist so it can be built locally, and `native_bridge.py` degrades to the Python scorer.

★ And one thing measured rather than assumed: the 2.4.0 **sdist** carried ~200 cargo
fingerprint files from `target/`, because `recursive-include AINDY *.json` is path-based and
matched them. Some embed the building machine's absolute rustup paths. It never reached PyPI —
the published 2.3.0 wheel was checked and contains none, because CI builds where `target/` is
unpopulated — but it is exactly the hazard that ships the day a release is cut from a developer
machine.

These are source-level assertions on the packaging *configuration*. Building a distribution to
inspect it takes ~40s and needs `setuptools>=83`, which is not a reasonable thing to require of
every unit run; `test_runtime_packaging.py` already builds and inspects real artifacts and is
where a content assertion against a built wheel belongs.
"""
from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.runtime_only

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFEST = (_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
_PYPROJECT = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# llms.txt — must live under the package, or it cannot reach an installed user
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["llms.txt", "llms-full.txt"])
def test_llms_files_live_under_the_package(name):
    """Package-data cannot reach a file outside `AINDY/`, whatever the glob says."""
    assert (_ROOT / "AINDY" / name).is_file(), (
        f"AINDY/{name} is missing. At the repo root it ships in neither wheel nor sdist, "
        f"because `[tool.setuptools.package-data]` only matches inside the package."
    )


@pytest.mark.parametrize("name", ["llms.txt", "llms-full.txt"])
def test_llms_files_are_declared_in_both_places(name):
    """The wheel takes package-data; the sdist takes MANIFEST. Declaring one is half a fix."""
    assert f'"{name}"' in _PYPROJECT, f"{name} not in [tool.setuptools.package-data]"
    assert "recursive-include AINDY llms*.txt" in _MANIFEST, f"{name} not in MANIFEST.in"


# --------------------------------------------------------------------------------------
# The Rust crate — source ships, build output never does
# --------------------------------------------------------------------------------------


def test_cargo_build_output_is_pruned_from_the_sdist():
    """★ The measured one.

    `recursive-include AINDY *.json` is path-based, so without this prune it matches every
    cargo fingerprint file under `target/` — ~200 of them in the 2.4.0 sdist, some embedding
    the building machine's absolute paths.
    """
    assert "prune AINDY/memory/native/memory_bridge_rs/target" in _MANIFEST, (
        "the cargo target/ prune is gone; a release cut from a machine that has built the "
        "crate will ship its build output, including local filesystem paths"
    )


def test_rust_source_ships_so_the_accelerator_can_be_built():
    """Option A of the audit: do not ship a binary, do ship what builds one."""
    for needed in (
        "recursive-include AINDY/memory/native/memory_bridge_rs/src *.rs",
        "include AINDY/memory/native/memory_bridge_rs/Cargo.toml",
        "include AINDY/memory/native/memory_bridge_rs/build.rs",
    ):
        assert needed in _MANIFEST, f"MANIFEST.in is missing: {needed}"


def test_no_compiled_artifact_is_declared_as_package_data():
    """★ Shipping one would be worse than shipping none.

    The backend produces a `py3-none-any` wheel. A `.pyd`/`.so`/`.dylib` inside one installs a
    broken binary for every user not on the exact OS/arch/CPython it was built with, and
    `native_bridge.py`'s clean fallback to Python is a better outcome than that.
    """
    package_data = _PYPROJECT[_PYPROJECT.index("[tool.setuptools.package-data]"):]
    package_data = package_data[: package_data.index("\n[", 1)]

    for ext in (".pyd", ".so", ".dylib"):
        assert ext not in package_data, (
            f"package-data declares {ext}. A pure-Python wheel must not carry a compiled "
            f"extension — it would install a broken binary on every other platform."
        )


def test_the_readme_states_the_native_path_is_not_installed():
    """A user who reads 'optional native scoring path' should not conclude they have one."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")

    assert "build-from-source accelerator" in readme
    assert "installed users run the" in readme.lower() or "Python scoring path" in readme


# --------------------------------------------------------------------------------------
# CONTRIBUTORS.md — a file that promises to travel has to travel
# --------------------------------------------------------------------------------------


def test_contributors_file_exists_and_is_declared_for_both_artifacts():
    """★ Its own text is the requirement: *"anyone who installs the package gets the code;
    this file travels with it so the credit does too."*

    A repo-root file cannot reach a wheel through package-data — that only matches inside
    `AINDY/`. `MANIFEST.in` covers the sdist; `license-files` is the only mechanism that puts a
    root file into a wheel, as `dist-info/licenses/`. Declaring one and not the other would
    leave the promise true for `pip download --no-binary` and false for `pip install`.

    The file is asserted to exist because the config now names it: `license-files` pointing at
    a missing path is a build-time problem in a fresh clone, not a quiet no-op.
    """
    assert (_ROOT / "CONTRIBUTORS.md").is_file(), (
        "CONTRIBUTORS.md is referenced by license-files and MANIFEST.in but does not exist"
    )
    assert "include CONTRIBUTORS.md" in _MANIFEST, "sdist would not carry CONTRIBUTORS.md"
    assert '"CONTRIBUTORS.md"' in _PYPROJECT, (
        "CONTRIBUTORS.md is not in license-files, so the wheel would not carry it"
    )
