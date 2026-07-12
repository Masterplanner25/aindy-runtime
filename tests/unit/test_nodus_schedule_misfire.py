"""ECOGAP-5a — durable-timer correctness + downtime-misfire catch-up.

Two things are covered:

  1. `_parse_cron` imports the top-level ``apscheduler`` name (the same one the runtime
     scheduler uses) rather than the explicit vendored module — so in production it builds a
     real CronTrigger the real scheduler accepts, instead of a foreign trigger instance that
     was rejected (leaving restored Nodus jobs unregistered).
  2. Per-job `misfire_policy`: a `run_once` job that missed a fire while the process was down
     gets ONE coalesced catch-up run scheduled at boot; `skip` jobs (default) do not.

Note: under pytest, `import apscheduler` resolves to the vendored stub (pythonpath shadow), so
the cron *fire-time math* is exercised with a controllable fake trigger — the real cron math is
APScheduler's own tested behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from AINDY.runtime import nodus_schedule_service as svc

pytestmark = pytest.mark.runtime_only


class _FakeTrigger:
    """Controllable stand-in: get_next_fire_time returns a fixed datetime (or None)."""

    def __init__(self, next_fire):
        self._next_fire = next_fire

    def get_next_fire_time(self, previous_fire_time, now):
        return self._next_fire


# --- _parse_cron ---


def test_parse_cron_accepts_valid_expression():
    assert svc._parse_cron("0 * * * *") is not None


def test_parse_cron_rejects_invalid_expression():
    with pytest.raises(ValueError):
        svc._parse_cron("not a cron")


# --- missed-window detection (_has_missed_fire logic) ---


def test_missed_fire_true_when_next_fire_after_reference_is_in_the_past():
    now = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)
    ref = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    # A fire was due at 13:00 (after ref, <= now) → missed.
    trigger = _FakeTrigger(datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc))
    assert svc._has_missed_fire(trigger, ref, now) is True


def test_missed_fire_false_when_next_fire_is_in_the_future():
    now = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)
    ref = datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)
    trigger = _FakeTrigger(datetime(2026, 7, 12, 16, 0, tzinfo=timezone.utc))  # future
    assert svc._has_missed_fire(trigger, ref, now) is False


def test_missed_fire_false_when_no_next_fire():
    now = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)
    ref = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    assert svc._has_missed_fire(_FakeTrigger(None), ref, now) is False


def test_missed_fire_false_when_reference_none():
    now = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)
    assert svc._has_missed_fire(_FakeTrigger(now), None, now) is False


def test_missed_fire_false_when_trigger_cannot_compute():
    now = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)
    ref = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    assert svc._has_missed_fire(object(), ref, now) is False  # no get_next_fire_time


# --- create validation ---


def test_create_rejects_invalid_misfire_policy():
    # Rejected before any DB work (the Mock db is never touched).
    with pytest.raises(ValueError, match="misfire_policy"):
        svc.create_nodus_scheduled_job(
            db=MagicMock(),
            script="print(1)",
            cron_expression="0 * * * *",
            user_id="",
            misfire_policy="bogus",
        )


# --- catch-up scheduling wiring ---


class _FakeJob:
    def __init__(self, *, misfire_policy, last_run_at, created_at, job_id="job-1"):
        self.id = job_id
        self.job_name = "nightly-report"
        self.misfire_policy = misfire_policy
        self.last_run_at = last_run_at
        self.created_at = created_at


class _FakeScheduler:
    def __init__(self):
        self.added: list[dict] = []

    def add_job(self, func, **kwargs):
        self.added.append(kwargs)


def _patch_scheduler(monkeypatch):
    sched = _FakeScheduler()
    monkeypatch.setattr("AINDY.platform_layer.scheduler_service.get_scheduler", lambda: sched)
    return sched


def _missed_trigger():
    # A fire in the recent past → _has_missed_fire True for any recent reference.
    return _FakeTrigger(datetime.now(timezone.utc) - timedelta(minutes=1))


def _future_trigger():
    return _FakeTrigger(datetime.now(timezone.utc) + timedelta(hours=1))


def test_run_once_missed_window_schedules_one_catchup(monkeypatch):
    sched = _patch_scheduler(monkeypatch)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    job = _FakeJob(misfire_policy="run_once", last_run_at=past, created_at=past)

    assert svc._maybe_schedule_misfire_catchup(job, _missed_trigger()) is True
    assert len(sched.added) == 1
    assert sched.added[0]["id"].endswith("_catchup")
    assert sched.added[0]["args"] == ["job-1"]


def test_skip_policy_never_schedules_catchup(monkeypatch):
    sched = _patch_scheduler(monkeypatch)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    job = _FakeJob(misfire_policy="skip", last_run_at=past, created_at=past)

    assert svc._maybe_schedule_misfire_catchup(job, _missed_trigger()) is False
    assert sched.added == []


def test_run_once_no_missed_window_no_catchup(monkeypatch):
    sched = _patch_scheduler(monkeypatch)
    just_ran = datetime.now(timezone.utc)
    job = _FakeJob(misfire_policy="run_once", last_run_at=just_ran, created_at=just_ran)

    assert svc._maybe_schedule_misfire_catchup(job, _future_trigger()) is False
    assert sched.added == []
