"""FR-18 — a liveness probe must not persist a full health snapshot.

What these assert, and why each one is here rather than a source check:

* the fingerprint ignores the fields that move on their own (a timestamp, a pool
  gauge, a domain's ``last_checked``) and notices the ones that mean something —
  without that the change-detection is defeated and the rate control does nothing;
* a run of identical probes writes **one** row, and a posture change writes another
  immediately;
* what lands is the digest, not the 26-key snapshot — asserted by looking at the
  payload handed to ``emit_system_event``, because the payload *is* the defect;
* the escape hatches (``full`` payload mode, the off switch) do what they say;
* the counter an operator would actually read moves, per outcome. It is the
  instrument that distinguishes "suppressed" from "never ran" — CLAUDE.md's
  variant-10 rule.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from AINDY.core import health_liveness_signal as sig
from AINDY.kernel.clock import frozen_at, utcnow

pytestmark = pytest.mark.runtime_only


def _health_payload(**overrides):
    """A payload shaped like the real one: constant posture, moving instrumentation."""
    payload = {
        "status": "healthy",
        "timestamp": "2026-08-22T12:00:00+00:00",
        "version": "2.5.0",
        "degraded_domains": [],
        "degraded_apps": [],
        "platform": {"execution_engine": "ok", "database": "ok"},
        "trusted_python_execution": {"inventory": ["a"] * 50, "mode": "sandboxed"},
        "plugin_sandbox_attestation": {"verified": True, "image": "python:3.11-alpine"},
        "domains": {"masterplan": {"healthy": True, "last_checked": "2026-08-22T12:00:00+00:00"}},
        "db_pool": {"checkedout": 3, "pool_size": 10, "pressure_ratio": 0.2},
        "async_jobs": {"execution_mode": "thread", "queue_max": 100},
        "wait_resume": {"propagation_mode": "cross-instance", "event_bus_enabled": True},
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    for env in (
        "AINDY_HEALTH_LIVENESS_EVENTS",
        "AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD",
        "AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(env, raising=False)
    sig.reset_state()
    yield
    sig.reset_state()


# ── fingerprint ──────────────────────────────────────────────────────────────


class TestFingerprint:
    def test_identical_payloads_fingerprint_identically(self):
        assert sig.posture_fingerprint(_health_payload()) == sig.posture_fingerprint(_health_payload())

    @pytest.mark.parametrize(
        "mutation",
        [
            {"timestamp": "2026-08-22T12:00:15+00:00"},
            {"db_pool": {"checkedout": 9, "pool_size": 10, "pressure_ratio": 0.9}},
            {"domains": {"masterplan": {"healthy": True, "last_checked": "2026-08-22T13:00:00+00:00"}}},
        ],
    )
    def test_volatile_fields_do_not_move_it(self, mutation):
        """The rate control lives or dies on this: any of these ticks every 15s."""
        assert sig.posture_fingerprint(_health_payload()) == sig.posture_fingerprint(
            _health_payload(**mutation)
        )

    @pytest.mark.parametrize(
        "mutation",
        [
            {"status": "degraded"},
            {"degraded_domains": ["masterplan"]},
            {"warnings": ["db_pool_near_exhaustion"]},
            {"plugin_sandbox_attestation": {"verified": False, "image": "python:3.11-alpine"}},
            {"domains": {"masterplan": {"healthy": False, "last_checked": "2026-08-22T12:00:00+00:00"}}},
        ],
    )
    def test_posture_changes_move_it(self, mutation):
        assert sig.posture_fingerprint(_health_payload()) != sig.posture_fingerprint(
            _health_payload(**mutation)
        )


# ── decision ─────────────────────────────────────────────────────────────────


class TestDecision:
    def test_first_probe_after_boot_persists(self):
        persist, reason, _, _ = sig._decide("fp-1", now=utcnow(), interval=3600)
        assert (persist, reason) == (True, "boot")

    def test_unchanged_probes_are_suppressed_and_counted(self):
        now = utcnow()
        sig._decide("fp-1", now=now, interval=3600)
        for expected_probes in (1, 2, 3):
            persist, reason, probes, _ = sig._decide("fp-1", now=now, interval=3600)
            assert (persist, reason) == (False, "suppressed")
            assert probes == expected_probes

    def test_a_change_persists_immediately(self):
        now = utcnow()
        sig._decide("fp-1", now=now, interval=3600)
        sig._decide("fp-1", now=now, interval=3600)
        persist, reason, probes, _ = sig._decide("fp-2", now=now, interval=3600)
        assert (persist, reason) == (True, "changed")
        assert probes == 2  # the row says how many probes it stands for

    def test_heartbeat_fires_once_the_interval_has_passed(self):
        start = utcnow()
        sig._decide("fp-1", now=start, interval=3600)
        assert sig._decide("fp-1", now=start + timedelta(seconds=3599), interval=3600)[0] is False
        persist, reason, _, _ = sig._decide("fp-1", now=start + timedelta(seconds=3600), interval=3600)
        assert (persist, reason) == (True, "interval")

    def test_interval_zero_means_change_only(self):
        start = utcnow()
        sig._decide("fp-1", now=start, interval=0)
        persist, _, _, _ = sig._decide("fp-1", now=start + timedelta(days=7), interval=0)
        assert persist is False


class TestConfig:
    def test_interval_defaults_and_rejects_garbage(self, monkeypatch):
        assert sig.heartbeat_interval_seconds() == sig.DEFAULT_INTERVAL_SECONDS
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS", "not-a-number")
        assert sig.heartbeat_interval_seconds() == sig.DEFAULT_INTERVAL_SECONDS
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS", "-5")
        assert sig.heartbeat_interval_seconds() == 0
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS", "120")
        assert sig.heartbeat_interval_seconds() == 120

    def test_env_is_read_per_call_not_at_import(self, monkeypatch):
        """CLAUDE.md standing rule — an import-time read is invisible to a test like this."""
        assert sig.liveness_events_enabled() is True
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENTS", "0")
        assert sig.liveness_events_enabled() is False
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENTS", "true")
        assert sig.liveness_events_enabled() is True


# ── what actually gets written ───────────────────────────────────────────────


class _Recorder:
    """Stands in for emit_system_event and keeps every payload it was handed."""

    def __init__(self):
        self.payloads = []

    def __call__(self, *, db, event_type, payload, source=None, required=False, **kwargs):
        assert event_type == sig.EVENT_TYPE
        self.payloads.append(payload)
        return "event-id"


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr("AINDY.core.system_event_service.emit_system_event", rec)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: MagicMock())
    return rec


class TestRecordLivenessProbe:
    def test_a_run_of_identical_probes_writes_one_row(self, recorder):
        outcomes = [sig.record_liveness_probe(_health_payload()) for _ in range(20)]
        assert outcomes[0] == "persisted:boot"
        assert set(outcomes[1:]) == {"suppressed"}
        assert len(recorder.payloads) == 1
        assert recorder.payloads[0]["reason"] == "boot"

    def test_a_degradation_is_recorded_at_once(self, recorder):
        sig.record_liveness_probe(_health_payload())
        sig.record_liveness_probe(_health_payload())
        assert sig.record_liveness_probe(_health_payload(status="degraded")) == "persisted:changed"
        assert recorder.payloads[-1]["status"] == "degraded"
        assert recorder.payloads[-1]["probes_since_last_event"] == 2

    def test_the_persisted_payload_is_a_digest_not_the_snapshot(self, recorder):
        sig.record_liveness_probe(_health_payload())
        written = recorder.payloads[0]
        # The blobs that made a row ~28 kB must not be in it …
        for heavy in ("trusted_python_execution", "plugin_sandbox_attestation", "domains", "platform"):
            assert heavy not in written
        # … and what replaces them must still answer "was it healthy, and did it move?"
        assert written["status"] == "healthy"
        assert written["posture_fingerprint"].startswith("sha256:")
        assert written["snapshot_endpoint"] == "/health/detail"
        assert written["snapshot_bytes"] > 0

    def test_a_change_row_names_the_keys_that_moved(self, recorder):
        """Otherwise a warming cache and a leaking volatile field look identical."""
        sig.record_liveness_probe(_health_payload())
        sig.record_liveness_probe(_health_payload(status="degraded", degraded_domains=["masterplan"]))
        assert recorder.payloads[0]["changed_keys"] == []  # boot names nothing
        assert recorder.payloads[1]["changed_keys"] == ["degraded_domains", "status"]

    def test_full_mode_restores_the_whole_snapshot(self, recorder, monkeypatch):
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD", "full")
        assert sig.record_liveness_probe(_health_payload()) == "persisted:boot"
        assert "trusted_python_execution" in recorder.payloads[0]

    def test_off_switch_writes_nothing(self, recorder, monkeypatch):
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENTS", "0")
        assert sig.record_liveness_probe(_health_payload()) == "disabled"
        assert recorder.payloads == []

    def test_a_failed_write_never_reaches_the_caller(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("database is on fire")

        monkeypatch.setattr("AINDY.core.system_event_service.emit_system_event", _boom)
        monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: MagicMock())
        assert sig.record_liveness_probe(_health_payload()) == "failed"

    def test_a_failed_write_is_retried_rather_than_swallowed(self, monkeypatch):
        """A failure must not leave the transition recorded-as-emitted and unwritten."""
        calls = {"n": 0}

        def _flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database is on fire")
            return "event-id"

        monkeypatch.setattr("AINDY.core.system_event_service.emit_system_event", _flaky)
        monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: MagicMock())

        assert sig.record_liveness_probe(_health_payload()) == "failed"
        assert sig.record_liveness_probe(_health_payload()) == "persisted:boot"
        assert calls["n"] == 2

    def test_heartbeat_row_lands_after_the_interval(self, recorder, monkeypatch):
        monkeypatch.setenv("AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS", "60")
        start = utcnow()
        with frozen_at(start):
            sig.record_liveness_probe(_health_payload())
            assert sig.record_liveness_probe(_health_payload()) == "suppressed"
        with frozen_at(start + timedelta(seconds=61)):
            assert sig.record_liveness_probe(_health_payload()) == "persisted:interval"
        assert [row["reason"] for row in recorder.payloads] == ["boot", "interval"]


class TestOperatorSignal:
    """The counter is the thing on the dashboard; assert on it, not on a log line."""

    def _read(self, outcome: str) -> float:
        from AINDY.platform_layer.metrics import REGISTRY

        value = REGISTRY.get_sample_value(
            "aindy_health_liveness_events_total", {"outcome": outcome}
        )
        return float(value or 0.0)

    def test_persisted_and_suppressed_are_both_visible(self, recorder):
        prometheus = pytest.importorskip("prometheus_client")
        if getattr(prometheus, "_is_stub", False):
            pytest.skip("prometheus_client stub cannot expose registry samples")

        before_boot = self._read("persisted_boot")
        before_suppressed = self._read("suppressed")

        for _ in range(5):
            sig.record_liveness_probe(_health_payload())

        assert self._read("persisted_boot") == before_boot + 1
        assert self._read("suppressed") == before_suppressed + 4


class TestThroughTheRoute:
    """A route test must call the route (CLAUDE.md, ROUTE-GUARD-1).

    Asserting on ``health_liveness_signal`` alone proves the decision is right and not
    that ``/health`` reaches it — which is the half that was broken.

    The payload is pinned deliberately. Whether the *live* health payload is stable
    across probes is a property of the posture providers (several populate lazily, so a
    cold process legitimately reports a change or two before settling), not of this
    wiring — gating CI on it makes a flaky test out of a real behaviour. The digest's
    ``changed_keys`` is what reports that at runtime.
    """

    def test_repeated_probes_write_one_digest_row(self, recorder, monkeypatch):
        import importlib
        import sys

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from AINDY.platform_layer.rate_limiter import limiter

        # The `AINDY.routes` namespace shadow: `from AINDY.routes import health_router`
        # returns the APIRouter object, not the module. Reach the module through
        # sys.modules so the payload provider can be patched.
        hr = sys.modules.get("AINDY.routes.health_router") or importlib.import_module(
            "AINDY.routes.health_router"
        )
        monkeypatch.setattr(hr, "_testing_health_payload", lambda: _health_payload())

        app = FastAPI()
        app.state.limiter = limiter
        app.include_router(hr.router)

        with TestClient(app) as client:
            for _ in range(4):
                assert client.get("/health").status_code == 200

        assert len(recorder.payloads) == 1, (
            "identical probes wrote a row each; keys reported as moving: "
            + str([row.get("changed_keys") for row in recorder.payloads[1:]])
        )
        written = recorder.payloads[0]
        assert "trusted_python_execution" not in written
        assert written["posture_fingerprint"].startswith("sha256:")
