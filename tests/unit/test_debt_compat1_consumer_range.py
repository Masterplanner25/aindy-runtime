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


# ── where the metadata came from, and whether another copy shadows it ───────────────────────


def _write_dist(root, dirname, requires):
    info = root / dirname
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.1", "Name: shadowprobe-app", "Version: 1.0.0"]
    lines += [f"Requires-Dist: {r}" for r in requires]
    (info / ("PKG-INFO" if dirname.endswith(".egg-info") else "METADATA")).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return info


def test_real_metadata_names_its_path_and_a_shadowing_copy(tmp_path, monkeypatch, caplog):
    """The app's case, rebuilt with REAL importlib.metadata: a stale egg-info earlier on sys.path
    declares an old range, and the installed dist-info declares the current one. Python reads the
    first. The record must say which copy it read and that another exists."""
    stale_root, installed_root = tmp_path / "repo_root", tmp_path / "site_packages"
    stale = _write_dist(stale_root, "shadowprobe_app.egg-info", ["aindy-runtime>=2.9.0"])
    installed = _write_dist(installed_root, "shadowprobe_app-1.0.0.dist-info", ["aindy-runtime<3.0,>=2.25.0"])
    monkeypatch.syspath_prepend(str(installed_root))
    monkeypatch.syspath_prepend(str(stale_root))  # first on sys.path, like a repo-root cwd
    caplog.set_level(logging.WARNING, logger=LOGGER)

    [record] = check_consumer_requirements(
        ["shadowprobe.mod"], runtime_version="2.25.0",
        packages_distributions=lambda: {"shadowprobe": ["shadowprobe-app"]},
    )
    assert record["requirement"] == ">=2.9.0", "control: the stale copy is the one Python reads"
    assert record["metadata_path"] == str(stale)
    assert record["shadowed_metadata"] == [str(installed)]
    [warning] = [r for r in _warnings(caplog) if "other cop" in r.getMessage()]
    assert str(stale) in warning.getMessage() and str(installed) in warning.getMessage()


def test_a_single_copy_reports_its_path_and_no_shadow(tmp_path, monkeypatch, caplog):
    installed = _write_dist(tmp_path / "sp", "shadowprobe_app-1.0.0.dist-info", ["aindy-runtime<3.0,>=2.25.0"])
    monkeypatch.syspath_prepend(str(tmp_path / "sp"))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    [record] = check_consumer_requirements(
        ["shadowprobe.mod"], runtime_version="2.25.0",
        packages_distributions=lambda: {"shadowprobe": ["shadowprobe-app"]},
    )
    assert record["metadata_path"] == str(installed)
    assert record["shadowed_metadata"] == []
    assert record["status"] == "satisfied"
    assert _warnings(caplog) == []


def test_the_unsatisfied_warning_names_the_metadata_path(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    check_consumer_requirements(
        ["myapp.x"], runtime_version="2.6.0",
        packages_distributions=lambda: {"myapp": ["my-app"]},
        distribution=lambda name: SimpleNamespace(version="1.0.0", requires=["aindy-runtime>=2.11"],
                                                  _path="/somewhere/my_app-1.0.0.dist-info"),
        distributions=lambda: [],
    )
    [warning] = _warnings(caplog)
    assert "/somewhere/my_app-1.0.0.dist-info" in warning.getMessage()


# ── FR-52: once per module set, never once per call ─────────────────────────────────────────
# The registry's getters call load_plugins() lazily — 26 times in one warm `GET /memory/nodes` —
# and the check ended every call reading every installed distribution's metadata: ~3 s on each
# of the app's requests (p50 3,780 ms as shipped, 820 ms with the check stubbed out).


@pytest.fixture
def counted(plugin_package, monkeypatch):
    """`packages_distributions` (the expensive half) and `load_plugins`, counted."""
    import importlib.metadata as md

    from AINDY.platform_layer import registry

    calls = {"scan": 0, "load_plugins": 0}
    owners = md.packages_distributions  # the fixture's fake

    def scan():
        calls["scan"] += 1
        return owners()

    real_load = registry.load_plugins

    def load(*args, **kwargs):
        calls["load_plugins"] += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(md, "packages_distributions", scan)
    monkeypatch.setattr(registry, "load_plugins", load)
    return calls


@pytest.fixture
def empty_registry():
    from AINDY.platform_layer import registry
    from tests.unit.test_extension_abi import _REGISTRY_STATE_EMPTY, _copy_registry_value

    snapshot = {name: _copy_registry_value(getattr(registry, name)) for name in _REGISTRY_STATE_EMPTY}
    for name, value in _REGISTRY_STATE_EMPTY.items():
        setattr(registry, name, _copy_registry_value(value))
    try:
        yield registry
    finally:
        for name, value in snapshot.items():
            setattr(registry, name, value)


def test_the_check_runs_once_across_repeated_loads(counted, plugin_package, empty_registry):
    for _ in range(5):
        empty_registry.load_plugins(manifest_path=plugin_package)
    assert counted["load_plugins"] == 5
    assert counted["scan"] == 1, f"the consumer check ran {counted['scan']} times for one module set"
    [record] = rc.consumer_requirement_checks()
    assert record["status"] == "unsatisfied", "running it once must still record its answer"


def test_a_different_module_set_is_checked(counted, plugin_package, empty_registry, tmp_path):
    """Liveness for the latch: it suppresses repeats, not checks."""
    (plugin_package.parent / "apps" / "debtcompat_other.py").write_text("def bootstrap():\n    pass\n", encoding="utf-8")
    wider = tmp_path / "wider.json"
    wider.write_text('{"plugins": ["apps.debtcompat_plugin", "apps.debtcompat_other"]}', encoding="utf-8")
    try:
        empty_registry.load_plugins(manifest_path=plugin_package)
        empty_registry.load_plugins(manifest_path=wider)
        empty_registry.load_plugins(manifest_path=wider)
    finally:
        sys.modules.pop("apps.debtcompat_other", None)
    assert counted["scan"] == 2


def test_a_warm_request_scans_no_metadata(runtime_only_client, db_session, counted, plugin_package, monkeypatch):
    """The FR's own instrument: count the scans across one warm request, through the real route."""
    import uuid

    from AINDY.db.models.user import User
    from AINDY.services.auth_service import create_access_token, hash_password

    user = User(email=f"fr52-{uuid.uuid4().hex[:8]}@aindy.test", username=f"fr52{uuid.uuid4().hex[:8]}",
                hashed_password=hash_password("fr52-password"), is_active=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    headers = {"Authorization": "Bearer " + create_access_token({"sub": str(user.id)}, token_version=0)}
    monkeypatch.setenv("AINDY_PLUGIN_MANIFEST", str(plugin_package))
    try:
        assert runtime_only_client.get("/memory/nodes?limit=1", headers=headers).status_code == 200  # warm
        counted.update(scan=0, load_plugins=0)
        assert runtime_only_client.get("/memory/nodes?limit=1", headers=headers).status_code == 200
    finally:
        sys.modules.pop("apps.debtcompat_plugin", None)
    assert counted["load_plugins"] > 0, "liveness: the request must go through the lazy getters"
    assert counted["scan"] == 0, f"{counted['scan']} metadata scans in one warm request"
