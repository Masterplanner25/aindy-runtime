"""The two dependency sources must agree, because CI installs from the one that is not shipped.

`Runtime Contracts` and `Integration Tests` both do:

```yaml
python -m pip install -r AINDY/requirements.txt
python -m pip install -e .[test] --no-deps --no-build-isolation
```

**`--no-deps` means `pyproject.toml`'s pins are never applied in CI.** The tests run against
`AINDY/requirements.txt`; the wheel a user installs declares `pyproject.toml`. So a divergence
between them is not a tidiness problem — it is CI proving something about a dependency set that
nobody ships.

★ **This was live, and for four months.** `pyproject.toml` moved to `nodus-lang==4.2.0` in #451
(FR-16, the app team's requested nodus upgrade). `AINDY/requirements.txt` still said `4.1.0` — it
had said so since the initial repo extraction and the bump PR missed it. Every green run since,
including the ones that signed off FR-16, exercised **the version being upgraded away from**,
while the published wheel required the new one. Found by
`test_nodus_upgrade_contract.py::test_the_installed_version_matches_the_declared_pin` on its
first CI run.

Exactly one package had drifted, so this is a missed edit rather than systemic rot — which is
precisely the kind of thing a guard is for, because the next bump can miss it the same way.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REQUIREMENTS = _ROOT / "AINDY" / "requirements.txt"
_PYPROJECT = _ROOT / "pyproject.toml"

_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*((?:[=<>!~][^;]*)?)")


def _normalise(name: str) -> str:
    """PEP 503 name normalisation — `nodus_lang` and `nodus-lang` are the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject_pins() -> dict[str, str]:
    text = _PYPROJECT.read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
    assert block, "could not find the [project] dependencies array in pyproject.toml"

    pins: dict[str, str] = {}
    for raw in re.findall(r'"([^"]+)"', block.group(1)):
        # Drop environment markers and extras: `foo[bar]>=1 ; python_version<'3.12'`
        spec = raw.split(";")[0].strip()
        spec = re.sub(r"\[[^\]]*\]", "", spec)
        match = _REQUIREMENT_RE.match(spec)
        if match:
            pins[_normalise(match.group(1))] = match.group(2).replace(" ", "")
    return pins


def _requirements_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        spec = re.sub(r"\[[^\]]*\]", "", line.split(";")[0].strip())
        match = _REQUIREMENT_RE.match(spec)
        if match:
            pins[_normalise(match.group(1))] = match.group(2).replace(" ", "")
    return pins


# --------------------------------------------------------------------------------------
# ★ The guard
# --------------------------------------------------------------------------------------


def test_shared_dependencies_pin_the_same_versions():
    """★ Where both files name a package, they must agree.

    Deliberately scoped to the intersection: `requirements.txt` legitimately carries transitive
    and ops-only packages `pyproject.toml` does not declare, and requiring set equality would
    fail for reasons that are not defects. What is never legitimate is the two files asking for
    *different versions of the same thing*, because CI believes one and users get the other.
    """
    declared = _pyproject_pins()
    installed = _requirements_pins()

    conflicts = [
        f"{name}: pyproject={declared[name] or '(unpinned)'} "
        f"requirements={installed[name] or '(unpinned)'}"
        for name in sorted(set(declared) & set(installed))
        if declared[name] != installed[name]
    ]

    assert not conflicts, (
        "these packages are pinned differently in pyproject.toml and AINDY/requirements.txt. "
        "CI installs requirements.txt and then `pip install -e . --no-deps`, so it tests the "
        f"requirements version while users get the pyproject one: {conflicts}"
    )


def test_the_comparison_is_actually_comparing_something():
    """Liveness. An empty intersection would satisfy the test above trivially.

    Both parsers have to survive real formatting — extras, environment markers, comments — and a
    silent parse failure looks exactly like agreement.
    """
    shared = set(_pyproject_pins()) & set(_requirements_pins())

    assert len(shared) >= 10, (
        f"only {len(shared)} packages are declared in both files; one of the parsers has "
        f"probably stopped matching the file format"
    )


def test_nodus_lang_is_pinned_exactly_in_both():
    """The specific pin this guard exists for.

    `nodus-lang` is pinned with `==` deliberately (`NODUS-UPGRADE-1`): an app must not be able to
    adopt a nodus release on its own. That only holds if **both** sources say so — a loose spec
    in `requirements.txt` would let CI drift onto a different nodus without anything failing.
    """
    declared = _pyproject_pins()
    installed = _requirements_pins()

    assert declared.get("nodus-lang", "").startswith("=="), (
        "nodus-lang is no longer pinned exactly in pyproject.toml"
    )
    assert installed.get("nodus-lang", "").startswith("=="), (
        "nodus-lang is no longer pinned exactly in AINDY/requirements.txt — CI would be free to "
        "resolve a different nodus than the one the wheel requires"
    )

# --------------------------------------------------------------------------------------
# ★ Is the pin actually installable alongside everything else we install?
# --------------------------------------------------------------------------------------


def test_no_installed_package_forbids_our_declared_pins():
    """★ Turns "installed != pinned" into "X caps it", which is the actionable half.

    A pin can be written, committed and merged while being **impossible to install**, because
    another package in the same environment caps it. pip then quietly resolves *down*, and the
    only symptom is that the installed version does not match the declared one — which says
    nothing about who is responsible.

    This is not hypothetical. Bumping `nodus-lang` to 5.0.0 passed locally and failed CI with
    `installed nodus-lang 4.2.0 != pinned 5.0.0`. The cause was `nodus-mcp 0.1.2`, which requires
    `nodus-lang<5.0.0,>=4.0.0`; CI installs it *after* `requirements.txt`, so pip downgraded
    nodus-lang to satisfy it. `pip install nodus-lang==5.0.0 nodus-mcp` is a flat
    `ResolutionImpossible`.

    Local was green only because the environment had been forced into a state pip would never
    produce — `pip check` flagged it, nothing else did. This asserts the declared pin against
    every installed distribution's stated requirements, so the conflict surfaces at the developer's
    desk and names the package responsible.

    `MCP-SDK-2X-1` is the same family: an ecosystem package capping a dependency and blocking an
    upgrade until it ships a compatible release.
    """
    from importlib.metadata import distributions

    from packaging.requirements import Requirement
    from packaging.version import Version

    declared = _pyproject_pins()
    exact = {
        name: spec[2:]
        for name, spec in declared.items()
        if spec.startswith("==") and "," not in spec
    }
    assert exact, "no exact pins found to check — the parser has probably stopped matching"

    conflicts: list[str] = []
    for dist in distributions():
        dist_name = _normalise(dist.metadata["Name"] or "")
        # Our own distribution is excluded: in an editable dev install its recorded metadata is
        # whatever it was at `pip install -e .` time and goes stale on every pin change, which
        # would make this fail for a reason that is not a conflict. `pyproject.toml` is the
        # authority on our own declaration, and the tests above already compare it.
        if dist_name == "aindy-runtime":
            continue
        for raw in dist.requires or []:
            try:
                requirement = Requirement(raw)
            except Exception:
                continue
            target = _normalise(requirement.name)
            if target not in exact or not requirement.specifier:
                continue
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue  # an extra/platform-conditional requirement that does not apply here
            if not requirement.specifier.contains(Version(exact[target]), prereleases=True):
                conflicts.append(
                    f"{dist_name} requires {target}{requirement.specifier} "
                    f"but we pin =={exact[target]}"
                )

    assert not conflicts, (
        "our declared pins cannot be installed alongside packages already in this environment, "
        "so pip will silently resolve to a different version than the one shipped: "
        f"{sorted(set(conflicts))}"
    )
