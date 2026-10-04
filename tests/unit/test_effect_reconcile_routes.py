"""EFFECT-OUTCOME-UNKNOWN-1 phase 4 — reconciling an `unknown` effect over HTTP.

The routes are the only way an `unknown` effect leaves that state: it is held on replay (DEC-085)
and never reaped (DEC-087), so without them it would sit forever. Proven end to end: a REAL
`unknown` recorded through the dispatcher, resolved over the REAL route, then dispatched again. After
`success` the handler must not run (the replay returns the data); after `failed` it must run (the
slot is free). Plus the audit event in the same transaction (if it cannot be written, nothing
changes), the refusals (404 / 409 / 422), and who may call it.

Runs on a private file-backed engine: these routes answer 4xx and commit, and the shared
one-connection fixture would read a flush as a commit (catalogue variant 15).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

ADMIN = str(uuid.uuid4())
EU = str(uuid.uuid4())


@pytest.fixture
def test_engine(tmp_path):
    from tests.fixtures.db import _import_model_registry

    from AINDY.db.database import Base

    _import_model_registry()
    engine = create_engine(f"sqlite:///{tmp_path / 'reconcile.db'}",
                           connect_args={"check_same_thread": False, "timeout": 10}, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _fk_off(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_factory(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=test_engine)


@pytest.fixture
def testing_session_factory(db_session_factory):
    return db_session_factory


@pytest.fixture
def as_principal(runtime_only_app, db_session_factory, monkeypatch):
    monkeypatch.setattr("AINDY.db.database.SessionLocal", db_session_factory)
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")

    def _set(principal):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: principal

    _set({"sub": ADMIN, "user_id": ADMIN, "auth_type": "jwt", "is_admin": True,
          "session_scopes": ["platform.admin"]})
    yield _set
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def _restore_syscall_registry():
    from AINDY.kernel import syscall_registry as R

    before = set(R.SYSCALL_REGISTRY.keys())
    try:
        yield
    finally:
        for name in set(R.SYSCALL_REGISTRY.keys()) - before:
            R.SYSCALL_REGISTRY.pop(name, None)


# ── a real `unknown`, recorded by the dispatcher ──────────────────────────────────────────


class _OkRm:
    def check_quota(self, _x):
        return True, None

    def record_usage(self, _x, _u):
        return None


def _effect_that_went_unknown():
    """Register an EXACTLY_ONCE syscall whose first run raises EffectOutcomeUnknown, dispatch it,
    and return (name, runs, action_id). Later runs succeed, so a re-run is visible."""
    from AINDY.core.execution_gate import compute_action_id
    from AINDY.kernel import syscall_registry as R
    from AINDY.kernel.syscall_outcome import EffectOutcomeUnknown

    name = f"sys.v1.test.rec_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        if len(runs) == 1:
            raise EffectOutcomeUnknown("charge sent; no answer before the timeout")
        return {"charged": True}

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=handler, capability="test.rec", execution_guarantee="EXACTLY_ONCE")
    assert _dispatch(name)["status"] == "unknown"
    return name, runs, compute_action_id(action_type=name, input_payload={"amount": 5}, scope=EU)


def _dispatch(name):
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    d = D.SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **k: None
    ctx = R.SyscallContext(execution_unit_id=EU, user_id="u-1", capabilities=["test.rec"], trace_id="t")
    with patch.object(D, "_get_rm", lambda: _OkRm()):
        return d.dispatch(name, {"amount": 5}, ctx)


def _status(factory, action_id):
    from AINDY.db.models.effect_record import EffectRecord

    s = factory()
    try:
        r = s.query(EffectRecord).filter(EffectRecord.action_id == action_id).first()
        return None if r is None else r.status
    finally:
        s.close()


def _data(resp):
    body = resp.json()
    return body.get("data", body)


# ── the routes ─────────────────────────────────────────────────────────────────────────────


def test_the_unknown_effect_is_listed_with_its_detail(runtime_only_client, as_principal, db_session_factory):
    from AINDY.kernel import syscall_registry as R

    _name, _runs, action_id = _effect_that_went_unknown()
    # a settled effect beside it, which the worklist must NOT include
    settled = f"sys.v1.test.rec_settled_{uuid.uuid4().hex[:6]}"
    R.SYSCALL_REGISTRY[settled] = R.SyscallEntry(handler=lambda p, c: {"ok": True}, capability="test.rec",
                                                 execution_guarantee="EXACTLY_ONCE")
    assert _dispatch(settled)["status"] == "success"
    resp = runtime_only_client.get("/platform/effects/unknown")
    assert resp.status_code == 200, resp.text[:300]
    items = _data(resp)["effects"]
    assert [i["action_id"] for i in items] == [action_id]
    assert items[0]["detail"] == "charge sent; no answer before the timeout"


def test_resolved_success_is_replayed_and_never_re_run(runtime_only_client, as_principal, db_session_factory):
    name, runs, action_id = _effect_that_went_unknown()
    resp = runtime_only_client.post(f"/platform/effects/{action_id}/resolve",
                                    json={"status": "success", "note": "the processor shows the charge"})
    assert resp.status_code == 200, resp.text[:300]
    assert _status(db_session_factory, action_id) == "success"
    replay = _dispatch(name)
    assert runs == [1], "a resolved-success effect was re-run"
    assert replay["status"] == "success", replay
    # the held wrapper was replaced by the recorded data, so the replay is an ordinary success
    assert replay["data"] == {}, f"the replay returned the held wrapper, not the data: {replay['data']}"


def test_resolved_failed_frees_the_slot_so_a_retry_runs(runtime_only_client, as_principal, db_session_factory):
    name, runs, action_id = _effect_that_went_unknown()
    held = _dispatch(name)
    assert held["status"] == "unknown" and runs == [1], "control: before resolution the effect is held"
    resp = runtime_only_client.post(f"/platform/effects/{action_id}/resolve",
                                    json={"status": "failed", "note": "the processor has no record of it"})
    assert resp.status_code == 200, resp.text[:300]
    retried = _dispatch(name)
    assert runs == [1, 1] and retried["status"] == "success", (runs, retried)


def test_the_audit_event_records_who_and_why(runtime_only_client, as_principal, db_session_factory):
    from AINDY.db.models.system_event import SystemEvent

    _name, _runs, action_id = _effect_that_went_unknown()
    runtime_only_client.post(f"/platform/effects/{action_id}/resolve",
                             json={"status": "failed", "note": "checked with the bank"})
    s = db_session_factory()
    try:
        events = s.query(SystemEvent).filter(SystemEvent.type == "effect.reconciled").all()
    finally:
        s.close()
    assert len(events) == 1
    payload = events[0].payload
    assert payload["action_id"] == action_id and payload["status"] == "failed"
    assert payload["note"] == "checked with the bank" and payload["resolved_by"] == ADMIN
    assert payload["previous_detail"] == "charge sent; no answer before the timeout"


def test_if_the_audit_event_cannot_be_written_nothing_changes(runtime_only_client, as_principal, db_session_factory):
    _name, _runs, action_id = _effect_that_went_unknown()

    def _boom(**_kw):
        raise RuntimeError("system_events unavailable")

    with patch("AINDY.core.execution_signal_helper.queue_system_event", _boom):
        resp = runtime_only_client.post(f"/platform/effects/{action_id}/resolve",
                                        json={"status": "success", "note": "n"})
    assert resp.status_code >= 500, resp.text[:300]
    assert _status(db_session_factory, action_id) == "unknown", "resolved without its audit record"


def test_only_an_unknown_can_be_resolved(runtime_only_client, as_principal, db_session_factory):
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.rec_ok_{uuid.uuid4().hex[:6]}"
    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=lambda p, c: {"ok": True}, capability="test.rec",
                                              execution_guarantee="EXACTLY_ONCE")
    assert _dispatch(name)["status"] == "success"
    from AINDY.core.execution_gate import compute_action_id

    action_id = compute_action_id(action_type=name, input_payload={"amount": 5}, scope=EU)
    resp = runtime_only_client.post(f"/platform/effects/{action_id}/resolve", json={"status": "failed", "note": "n"})
    assert resp.status_code == 409, resp.text[:300]
    assert _status(db_session_factory, action_id) == "success", "a settled effect was changed"


@pytest.mark.parametrize("body", [{"status": "success"}, {"status": "success", "note": ""},
                                  {"status": "partial", "note": "n"}])
def test_a_malformed_resolution_is_refused(runtime_only_client, as_principal, db_session_factory, body):
    _name, _runs, action_id = _effect_that_went_unknown()
    resp = runtime_only_client.post(f"/platform/effects/{action_id}/resolve", json=body)
    assert resp.status_code == 422, resp.text[:300]
    assert _status(db_session_factory, action_id) == "unknown"


def test_a_missing_effect_is_404(runtime_only_client, as_principal):
    resp = runtime_only_client.post("/platform/effects/no-such-action/resolve", json={"status": "failed", "note": "n"})
    assert resp.status_code == 404, resp.text[:300]


@pytest.mark.parametrize("principal", [
    {"sub": "u-2", "user_id": "u-2", "auth_type": "jwt", "is_admin": False, "session_scopes": []},
    {"sub": "u-3", "user_id": "u-3", "auth_type": "api_key", "api_key_scopes": ["flow.read"]},
], ids=["non-admin session", "key without platform.admin"])
def test_only_an_operator_may_resolve(runtime_only_client, as_principal, db_session_factory, principal):
    _name, _runs, action_id = _effect_that_went_unknown()
    as_principal(principal)
    resp = runtime_only_client.post(f"/platform/effects/{action_id}/resolve", json={"status": "failed", "note": "n"})
    assert resp.status_code == 403, resp.text[:300]
    assert _status(db_session_factory, action_id) == "unknown"
    assert runtime_only_client.get("/platform/effects/unknown").status_code == 403


def test_resolving_updates_the_gauge(runtime_only_client, as_principal, db_session_factory):
    from AINDY.platform_layer.metrics import REGISTRY

    from AINDY.platform_layer.metrics import effect_unknown_unresolved

    _name, _runs, action_id = _effect_that_went_unknown()
    effect_unknown_unresolved.set(7)  # a sentinel: the route must recount, not leave it
    runtime_only_client.post(f"/platform/effects/{action_id}/resolve", json={"status": "failed", "note": "n"})
    assert REGISTRY.get_sample_value("aindy_effect_unknown_unresolved") == 0.0


def test_the_reconciliation_event_is_kept_as_audit():
    from AINDY.core.system_event_retention import RETENTION_AUDIT, retention_class_for
    from AINDY.core.system_event_types import SystemEventTypes

    assert retention_class_for(SystemEventTypes.EFFECT_RECONCILED) == RETENTION_AUDIT
