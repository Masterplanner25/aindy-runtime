"""The vendored `apscheduler` shim must cover what the runtime actually calls.

`pytest.ini` sets `pythonpath = . AINDY`, so `import apscheduler` resolves to
`AINDY/apscheduler` — a hand-written shim — for **every test in this repo**. Anything the
runtime calls that the shim does not implement is untested by construction, and where the call
sits inside a `try/except` it fails *silently*: the test passes, production takes a different
branch.

That shape has now appeared three times:

1. `executors.pool` missing → the dedicated-executor branch shipped unexercised (FR-15 (b)).
2. `events` + `add_listener` missing → the starvation listener shipped unexercised (SYSMAX-5).
3. `remove_job` missing → `_remove_from_scheduler` swallowed an `AttributeError` under a
   comment claiming it was for an already-deleted job. Removal could have been a permanent
   no-op with every test green.

`test_shim_covers_every_scheduler_method_the_runtime_calls` is the guard that stops a fourth:
it derives the required surface from the source rather than from a hand-maintained list.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIM = ROOT / "AINDY" / "apscheduler"

#: Methods called on a SchedulerEngine or other non-APScheduler object that happen to be
#: reached through a variable named `scheduler`. Excluded by name so the scan below can stay
#: source-derived rather than becoming a curated allowlist of what we expect to find.
_NOT_APSCHEDULER = {
    "notify_event",
    "peek_matching_run_ids",
    "register_wait",
    "waiting_for",
    "schedule",
    "tick_waits",
    "get",  # dict access on a mapping named *_scheduler
    "cleanup_stale_waits",
    "get_metrics_snapshot",
    "stats",
    "reset",
    "enqueue",
    "dequeue_next",
    "queue_depth",
}


def _shim_scheduler_methods() -> set[str]:
    source = (SHIM / "schedulers" / "background.py").read_text(encoding="utf-8")
    return set(re.findall(r"^    def ([a-z_][a-z0-9_]*)\(", source, re.M))


def _runtime_scheduler_calls() -> set[str]:
    """Methods the runtime invokes on something named `scheduler`/`_scheduler`."""
    found: set[str] = set()
    for path in (ROOT / "AINDY").rglob("*.py"):
        if SHIM in path.parents or path == SHIM:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found.update(re.findall(r"\b_?scheduler\.([a-z_][a-z0-9_]*)\(", text))
    return {name for name in found if name not in _NOT_APSCHEDULER}


# --------------------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------------------


def test_shim_covers_every_scheduler_method_the_runtime_calls():
    """★ Derived from source, not from a maintained list.

    A hand-written list of "methods we expect" would drift exactly like the shim did. This
    scans for real call sites, so adding `scheduler.pause_job(...)` to the runtime fails here
    until the shim can express it.
    """
    called = _runtime_scheduler_calls()
    implemented = _shim_scheduler_methods()

    assert called, "found no scheduler calls — the scan is broken, not the code"
    missing = called - implemented
    assert not missing, (
        f"the runtime calls {sorted(missing)} on the scheduler, but the vendored shim does not "
        f"implement it. Under pytest `import apscheduler` resolves to the shim, so those call "
        f"sites are untested — and where they sit inside a try/except they fail silently."
    )


@pytest.mark.parametrize(
    "module,attribute",
    [
        ("apscheduler.executors.pool", "ThreadPoolExecutor"),
        ("apscheduler.schedulers.background", "BackgroundScheduler"),
        ("apscheduler.triggers.cron", "CronTrigger"),
        ("apscheduler.triggers.interval", "IntervalTrigger"),
        ("apscheduler.triggers.date", "DateTrigger"),
        ("apscheduler.events", "EVENT_JOB_MAX_INSTANCES"),
        ("apscheduler.events", "EVENT_JOB_MISSED"),
        ("apscheduler.jobstores.base", "JobLookupError"),
    ],
)
def test_every_imported_apscheduler_symbol_resolves(module, attribute):
    """Each of these is imported somewhere in `AINDY/`; a missing one is a silent fallback."""
    import importlib

    mod = importlib.import_module(module)
    assert hasattr(mod, attribute), f"{module}.{attribute} is missing from the shim"


# --------------------------------------------------------------------------------------
# Semantics, not just presence
# --------------------------------------------------------------------------------------


def test_remove_job_raises_the_same_type_the_runtime_catches():
    """Presence is not enough — the shim must fail the way production fails.

    `_remove_from_scheduler` treats `JobLookupError` as benign and anything else as a real
    fault. A shim that no-op'd on a missing job would exercise the wrong branch and hide the
    distinction this audit restored.
    """
    from apscheduler.jobstores.base import JobLookupError
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: None, id="present", name="present")

    scheduler.remove_job("present")
    assert scheduler.get_job("present") is None

    with pytest.raises(JobLookupError):
        scheduler.remove_job("never-existed")


def test_get_job_matches_the_real_contract():
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: None, id="a", name="A")

    assert scheduler.get_job("a") is not None
    assert scheduler.get_job("missing") is None, "get_job must return None, not raise"


def test_remove_from_scheduler_reports_a_real_fault(monkeypatch, caplog):
    """The behaviour the audit restored: only a missing job is silent.

    Previously a bare `except Exception: pass` swallowed everything under a comment saying
    "Job may already be gone", so a renamed API was indistinguishable from a deleted job and
    removal could be a permanent no-op.
    """
    import logging

    from AINDY.runtime import nodus_schedule_service as nss

    class _BrokenScheduler:
        def remove_job(self, job_id, jobstore=None):
            raise AttributeError("scheduler API changed")

    monkeypatch.setattr(
        "AINDY.platform_layer.scheduler_service.get_scheduler", lambda: _BrokenScheduler()
    )

    with caplog.at_level(logging.WARNING):
        nss._remove_from_scheduler("some-job")  # must not raise

    assert any("failed to remove job" in r.getMessage() for r in caplog.records), (
        "a genuine scheduler fault was swallowed silently — the exact defect this audit found"
    )


def test_nodus_resolves_to_the_installed_package_not_the_runtime_shim():
    """★ The other package `pythonpath = . AINDY` could shadow — and this one matters more.

    `AINDY/nodus/` shares its name with the installed `nodus-lang` package, and
    `AINDY/nodus/runtime/embedding.py` shares the *exact module path* of the real
    `nodus.runtime.embedding`. GUEST-CONFINE-1's confinement tests import `NodusRuntime` from
    that path and assert 31 builtins are refused — so if resolution ever flipped, they would
    be asserting against the wrong object while still passing.

    Today the collision is self-limiting: `AINDY/nodus/runtime/embedding.py` is a re-export
    (`from nodus.runtime.embedding import NodusRuntime`), so shadowing would self-import and
    fail loudly rather than quietly. This pins the resolution anyway, because that property
    depends on the file staying a re-export — a future edit adding a real definition there
    would turn a loud failure into a silent one.
    """
    import inspect

    import nodus
    from nodus.runtime.embedding import NodusRuntime

    origin = str(getattr(nodus, "__file__", ""))
    assert "site-packages" in origin, (
        f"`import nodus` resolved to {origin!r} rather than the installed package. "
        f"GUEST-CONFINE-1's tests would then assert confinement against the wrong VM."
    )
    assert "site-packages" in str(inspect.getsourcefile(NodusRuntime))

    # The confinement arguments the guest guard depends on must be on THIS object.
    params = inspect.signature(NodusRuntime.__init__).parameters
    for flag in ("allow_subprocess", "allow_network", "allow_env"):
        assert flag in params, f"{flag} missing — guest confinement would be silently inert"


def test_remove_from_scheduler_stays_quiet_for_an_absent_job(monkeypatch, caplog):
    """Control for the test above: the benign case must NOT warn, or the warning is noise."""
    import logging

    from apscheduler.jobstores.base import JobLookupError

    from AINDY.runtime import nodus_schedule_service as nss

    class _EmptyScheduler:
        def remove_job(self, job_id, jobstore=None):
            raise JobLookupError(job_id)

    monkeypatch.setattr(
        "AINDY.platform_layer.scheduler_service.get_scheduler", lambda: _EmptyScheduler()
    )

    with caplog.at_level(logging.WARNING):
        nss._remove_from_scheduler("gone-already")

    assert not [r for r in caplog.records if "failed to remove job" in r.getMessage()]
