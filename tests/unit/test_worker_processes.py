"""ECOGAP-6 Phase 2 — coverage for the standalone worker process entrypoints.

`metric_writer_worker.main`, `memory_ingest_worker.main`, and the production
`worker/__main__.main` orchestration were untested. These drive each ``main()`` with
all collaborators faked (no DB / Redis / real threads), exercising the start →
health-check-registration → loop → shutdown lifecycle and the schema-gate branch.
"""
from __future__ import annotations

import signal

import pytest

pytestmark = pytest.mark.runtime_only


class _FakeHealthServer:
    def __init__(self, *a, **k):
        self.checks: dict = {}
        self.started = False
        self.stopped = False

    def register_check(self, name, fn):
        self.checks[name] = fn

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _FakeWorker:
    """Stands in for the metric writer / ingest queue."""

    def __init__(self):
        self.started = False
        self.stopped = False

    def snapshot(self):
        return {"worker_running": True}

    def start(self):
        self.started = True

    def stop(self, timeout=10, **k):
        self.stopped = True


def _one_shot_sleep(captured):
    """A time.sleep replacement that fires the captured SIGTERM handler once so the
    worker's `while is_running` loop exits after a single iteration."""
    def _sleep(_seconds):
        handler = captured.pop(signal.SIGTERM, None)
        if handler is not None:
            handler(signal.SIGTERM, None)
    return _sleep


def _run_worker_main(monkeypatch, module, worker_getter_path, worker, check_name):
    captured: dict = {}
    health = _FakeHealthServer()
    monkeypatch.setattr("AINDY.platform_layer.log_config.configure_logging", lambda **k: None)
    monkeypatch.setattr("AINDY.worker.health_server.WorkerHealthServer", lambda *a, **k: health)
    monkeypatch.setattr(worker_getter_path, lambda *a, **k: worker)
    monkeypatch.setattr(module.signal, "signal", lambda sig, handler: captured.__setitem__(sig, handler))
    monkeypatch.setattr(module.time, "sleep", _one_shot_sleep(captured))
    module.main()
    return health, captured


def test_metric_writer_worker_lifecycle(monkeypatch):
    from AINDY.worker import metric_writer_worker as mw

    worker = _FakeWorker()
    health, _ = _run_worker_main(
        monkeypatch, mw, "AINDY.core.request_metric_writer.get_writer", worker, "writer_alive"
    )
    assert worker.started and worker.stopped  # started, then stopped in finally
    assert health.started and health.stopped
    assert "writer_alive" in health.checks
    assert health.checks["writer_alive"]() is True  # reflects snapshot worker_running


def test_memory_ingest_worker_lifecycle(monkeypatch):
    from AINDY.worker import memory_ingest_worker as mi

    queue = _FakeWorker()
    health, _ = _run_worker_main(
        monkeypatch,
        mi,
        "AINDY.memory.memory_ingest_service.configure_memory_ingest_queue",
        queue,
        "queue_alive",
    )
    assert queue.started and queue.stopped
    assert health.started and health.stopped
    assert "queue_alive" in health.checks
    assert health.checks["queue_alive"]() is True


# --- worker/__main__ orchestration ---


def _patch_main_deps(monkeypatch, *, schema_ready=True, elected=True):
    import AINDY.worker.__main__ as mm

    calls: dict = {"run_worker_loop": 0, "scheduler_stop": 0, "elector_started": 0}
    monkeypatch.setattr(mm, "load_plugins", lambda: None)
    monkeypatch.setattr(
        mm, "validate_worker_deployment_profile",
        lambda: {"background_leadership_mode": "lease", "name": "prod", "source": "test"},
    )
    monkeypatch.setattr(mm, "publish_worker_runtime_state", lambda **k: None)
    monkeypatch.setattr(mm, "validate_queue_backend", lambda: object())
    monkeypatch.setattr(mm, "_wait_for_background_schema", lambda: schema_ready)

    class _Lifecycle:
        def start_background_tasks(self, **k):
            return True

        def stop_background_tasks(self, **k):
            return None

    monkeypatch.setattr(mm, "lifecycle_services", _Lifecycle())

    class _Sched:
        def start(self):
            pass

        def stop(self):
            calls["scheduler_stop"] += 1

    monkeypatch.setattr(mm, "scheduler_service", _Sched())
    monkeypatch.setattr(mm, "run_worker_loop", lambda **k: calls.__setitem__("run_worker_loop", calls["run_worker_loop"] + 1))

    class _Elector:
        owner_id = "owner-1"

        def elect_once(self):
            return elected

        def start(self):
            calls["elector_started"] += 1

    monkeypatch.setattr("AINDY.platform_layer.leadership.get_background_elector", lambda **k: _Elector())
    monkeypatch.setattr("AINDY.platform_layer.leadership.background_owner_id", lambda: "owner-1")
    monkeypatch.setattr("AINDY.platform_layer.leadership.stop_background_elector", lambda: None)
    return mm, calls


def test_worker_main_happy_path_runs_loop_and_cleans_up(monkeypatch):
    mm, calls = _patch_main_deps(monkeypatch, schema_ready=True, elected=True)
    mm.main()
    assert calls["run_worker_loop"] == 1
    assert calls["elector_started"] == 1
    assert calls["scheduler_stop"] >= 1  # finally always stops the scheduler


def test_worker_main_blocks_when_schema_not_ready(monkeypatch):
    mm, calls = _patch_main_deps(monkeypatch, schema_ready=False)
    with pytest.raises(RuntimeError, match="schema is not ready"):
        mm.main()
    assert calls["run_worker_loop"] == 0  # never reached the loop
