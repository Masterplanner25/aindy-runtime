"""DEBT-COMPAT-1 — a plugin distribution's declared aindy-runtime range is compared with the running
runtime at plugin load, and a mismatch WARNS (DEC-080).

The runtime published its policy on `/api/version` and nothing read it. A consumer sat a major
behind, under the advertised floor; another's dev venv ran five releases older than its own
declared range while every suite passed. Nothing fetches `/api/version`, so the comparison is made
where both facts are visible: `load_plugins`, from the installed metadata of the distribution that
owns each plugin module.

What is pinned:
* each status: satisfied, unsatisfied, undeclared, satisfied with no upper bound, not_installed,
  unknown; and which of them warn;
* an extra-only requirement does not count; several lines intersect;
* the runtime's own modules are never a consumer (a runtime-only boot must not warn);
* it never raises, including when the metadata probe itself fails;
* real `importlib.metadata`, with no fakes: a real installed distribution that declares nothing;
* through the real `load_plugins` and then the real `GET /api/version`: the record is served.
"""
from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest

from AINDY.platform_layer import runtime_compatibility as rc
from AINDY.platform_layer.runtime_compatibility import check_consumer_requirements

pytestmark = pytest.mark.runtime_only

LOGGER = "AINDY.platform_layer.runtime_compatibility"


@pytest.fixture(autouse=True)
def _restore_checks():
    saved = list(rc._consumer_checks)
    try:
        yield
    finally:
        rc._consumer_checks[:] = saved


def _check(requires, *, runtime="2.24.0", owners=None, module="myapp.bootstrap"):
    return check_consumer_requirements(
        [module],
        runtime_version=runtime,
        packages_distributions=lambda: owners if owners is not None else {"myapp": ["my-app"]},
        distribution=lambda name: SimpleNamespace(version="1.0.0", requires=requires),
    )


def _warnings(caplog):
    return [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.WARNING]


@pytest.mark.parametrize("requires, runtime, status, upper, warns", [
    (["aindy-runtime<3.0,>=2.24.0"], "2.24.0", "satisfied", True, False),
    (["aindy-runtime<3.0,>=2.11.0"], "2.6.0", "unsatisfied", True, True),      # the 2026-09-11 dev venv
    (["aindy-runtime>=2.0,<3.0"], "1.4.0", "unsatisfied", True, True),         # the claw case, had it declared
    (["aindy-runtime>=2.0"], "2.24.0", "satisfied", False, True),              # no upper bound
    (["aindy-runtime~=2.24"], "2.30.1", "satisfied", True, False),
    (["aindy-runtime==2.24.0"], "2.24.1", "unsatisfied", True, True),
    (["nodus-lang>=5"], "2.24.0", "undeclared", None, True),                  # declares nothing about us
    ([], "2.24.0", "undeclared", None, True),
    (None, "2.24.0", "undeclared", None, True),
    (['aindy-runtime>=9 ; extra == "dev"'], "2.24.0", "undeclared", None, True),  # an extra is not enforced
    (["AINDY_Runtime<3.0,>=2.24.0"], "2.24.0", "satisfied", True, False),     # names are canonicalised
    (["aindy-runtime<2.24", "aindy-runtime>=2.20"], "2.24.0", "unsatisfied", True, True),  # lines intersect
])
def test_each_status(caplog, requires, runtime, status, upper, warns):
    caplog.set_level(logging.INFO, logger=LOGGER)
    [record] = _check(requires, runtime=runtime)
    assert record["status"] == status
    assert record["upper_bound"] is upper
    assert record["distribution"] == "my-app" and record["modules"] == ["myapp.bootstrap"]
    assert bool(_warnings(caplog)) is warns, [r.getMessage() for r in caplog.records]


def test_the_unsatisfied_warning_says_where_the_runtime_came_from(caplog):
    """Every version instrument is cwd-sensitive; the warning prints the path beside the number."""
    caplog.set_level(logging.WARNING, logger=LOGGER)
    _check(["aindy-runtime<3.0,>=2.11.0"], runtime="2.6.0")
    [warning] = _warnings(caplog)
    message = warning.getMessage()
    assert "my-app 1.0.0" in message and "2.6.0" in message and "<3.0,>=2.11.0" in message
    import AINDY

    assert str(list(AINDY.__path__)[0]) in message
    assert "not a refusal" in message


def test_a_module_no_distribution_owns_is_not_installed_at_info(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    [record] = _check(["aindy-runtime>=2"], owners={})
    assert record["status"] == "not_installed" and record["distribution"] is None
    assert _warnings(caplog) == []


def test_the_runtimes_own_modules_are_never_a_consumer(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    assert _check([], module="AINDY.platform_layer.runtime_agent_defaults",
                  owners={"AINDY": ["aindy-runtime"]}) == []
    assert _check([], module="runtime_extra.mod", owners={"runtime_extra": ["aindy_runtime"]}) == []
    # a source checkout the runtime was never installed from: nothing owns `AINDY`
    assert _check([], module="AINDY.platform_layer.runtime_agent_defaults", owners={}) == []
    assert _warnings(caplog) == []


def test_it_never_raises():
    def boom(*_a, **_k):
        raise RuntimeError("metadata unreadable")

    [record] = check_consumer_requirements(["myapp.x"], packages_distributions=boom)
    assert record["status"] == "not_installed"
    [record] = check_consumer_requirements(["myapp.x"], packages_distributions=lambda: {"myapp": ["my-app"]},
                                           distribution=boom)
    assert record["status"] == "unknown" and "metadata unreadable" in record["error"]


def test_real_metadata_a_real_distribution_that_declares_nothing():
    """No fakes: `pytest` is installed, owns the top-level `pytest`, and does not depend on us."""
    [record] = check_consumer_requirements(["pytest"])
    assert record["distribution"] and record["distribution"].lower() == "pytest"
    assert record["distribution_version"]
    assert record["status"] == "undeclared"


# ── through the real load_plugins, then the real route ──────────────────────────────────────


@pytest.fixture
def plugin_package(tmp_path, monkeypatch):
    """A first-party plugin `apps.debtcompat_plugin` (in-process plugins must live under `apps.`).

    `apps` is installed as a temporary package in `sys.modules`: a dev host may have the real
    app installed (its `apps` would win), CI has none, and the test must mean the same on both.
    """
    import importlib.metadata as md
    import types

    pkg = tmp_path / "apps"
    pkg.mkdir()
    (pkg / "debtcompat_plugin.py").write_text("def bootstrap():\n    pass\n", encoding="utf-8")
    fake_apps = types.ModuleType("apps")
    fake_apps.__path__ = [str(pkg)]
    monkeypatch.setitem(sys.modules, "apps", fake_apps)
    manifest = tmp_path / "aindy_plugins.json"
    manifest.write_text('{"plugins": ["apps.debtcompat_plugin"]}', encoding="utf-8")

    real_distribution = md.distribution

    def fake_distribution(name):
        if name == "debtcompat-app":
            return SimpleNamespace(version="0.9.0", requires=["aindy-runtime>=99.0,<100.0"])
        return real_distribution(name)

    monkeypatch.setattr(md, "packages_distributions", lambda: {"apps": ["debtcompat-app"]})
    monkeypatch.setattr(md, "distribution", fake_distribution)
    try:
        yield manifest
    finally:
        sys.modules.pop("apps.debtcompat_plugin", None)


def test_load_plugins_runs_the_check_and_boot_continues(plugin_package, caplog):
    from AINDY.platform_layer import registry
    from tests.unit.test_extension_abi import _REGISTRY_STATE_EMPTY, _copy_registry_value

    snapshot = {name: _copy_registry_value(getattr(registry, name)) for name in _REGISTRY_STATE_EMPTY}
    caplog.set_level(logging.WARNING, logger=LOGGER)
    try:
        for name, value in _REGISTRY_STATE_EMPTY.items():
            setattr(registry, name, _copy_registry_value(value))
        loaded = registry.load_plugins(manifest_path=plugin_package)
    finally:
        for name, value in snapshot.items():
            setattr(registry, name, value)

    assert loaded == ["apps.debtcompat_plugin"], "a failed version check must never stop a plugin loading"
    [record] = rc.consumer_requirement_checks()
    assert record["distribution"] == "debtcompat-app" and record["status"] == "unsatisfied"
    assert _warnings(caplog), "the mismatch was not logged"


def test_the_version_route_serves_the_checks(runtime_only_client):
    rc._consumer_checks[:] = [{
        "distribution": "debtcompat-app", "distribution_version": "0.9.0", "modules": ["apps.debtcompat_plugin"],
        "requirement": "<100.0,>=99.0", "upper_bound": True, "runtime_version": "2.24.0", "status": "unsatisfied",
    }]
    response = runtime_only_client.get("/api/version")
    assert response.status_code == 200
    consumers = response.json()["compatibility"]["consumers"]
    assert consumers and consumers[0]["status"] == "unsatisfied"
    assert consumers[0]["distribution"] == "debtcompat-app"
