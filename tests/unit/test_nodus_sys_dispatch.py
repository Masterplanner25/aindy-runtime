"""FR-5(b) — the Nodus sys() seam loads app syscalls before dispatch.

App domains register their syscalls into the kernel ``SYSCALL_REGISTRY`` from each app's
``bootstrap()`` (run by ``load_plugins()``, reached via ``_ensure_tools_loaded``). The
worker's ``sys()`` seam previously had no plugin-load entry point, so a ``sys()``-only
workflow dispatched against an unpopulated registry → ``"Unknown syscall"``.
``dispatch_worker_syscall`` must run ``_ensure_tools_loaded()`` BEFORE ``dispatch_syscall``.
"""
from __future__ import annotations

import pytest

from AINDY.runtime.nodus_worker import dispatch_worker_syscall

pytestmark = pytest.mark.runtime_only


class _FakeDB:
    closed = False

    def close(self):
        self.closed = True


def test_ensures_plugins_loaded_before_dispatch(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(
        "AINDY.agents.tool_registry._ensure_tools_loaded",
        lambda: order.append("load"),
    )

    def _fake_dispatch(name, payload, *, db=None, user_id=None):
        order.append("dispatch")
        return {"status": "success", "data": {"name": name, "uid": user_id, "payload": payload}}

    monkeypatch.setattr("AINDY.kernel.syscall_dispatcher.dispatch_syscall", _fake_dispatch)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: _FakeDB())

    res = dispatch_worker_syscall("sys.v1.analytics.get_kpi_snapshot", {"x": 1}, user_id="u1")

    # The whole point: the app plugin stack is loaded before dispatch resolves the name.
    assert order == ["load", "dispatch"]
    assert res["status"] == "success"
    assert res["data"]["uid"] == "u1"
    assert res["data"]["payload"] == {"x": 1, "user_id": "u1"}  # user_id threaded in


def test_explicit_user_id_in_payload_is_preserved(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("AINDY.agents.tool_registry._ensure_tools_loaded", lambda: None)
    monkeypatch.setattr(
        "AINDY.kernel.syscall_dispatcher.dispatch_syscall",
        lambda name, payload, *, db=None, user_id=None: seen.update(payload) or {"status": "success"},
    )
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: _FakeDB())

    dispatch_worker_syscall("sys.v1.x.y", {"user_id": "explicit"}, user_id="ctx")
    assert seen["user_id"] == "explicit"  # setdefault must not clobber an explicit id


def test_non_dict_payload_becomes_empty(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("AINDY.agents.tool_registry._ensure_tools_loaded", lambda: None)
    monkeypatch.setattr(
        "AINDY.kernel.syscall_dispatcher.dispatch_syscall",
        lambda name, payload, *, db=None, user_id=None: seen.update(payload) or {"status": "success"},
    )
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: _FakeDB())

    dispatch_worker_syscall("sys.v1.x.y", "not-a-dict", user_id="u1")
    assert seen == {"user_id": "u1"}


def test_error_returns_envelope_and_closes_db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr("AINDY.agents.tool_registry._ensure_tools_loaded", lambda: None)

    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("AINDY.kernel.syscall_dispatcher.dispatch_syscall", _boom)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", lambda: fake)

    res = dispatch_worker_syscall("sys.v1.x.y", {}, user_id="u1")
    assert res == {"status": "error", "error": "kaboom", "data": None, "syscall": "sys.v1.x.y"}
    assert fake.closed is True  # db closed even on handler error
