"""FR-29 / `WAIT-DETECT-SHAPE-1` — a request that READS a waiting run must not itself park.

Found by the app team running Tutorial 2 against their 2.14.0 container (app FR-29): eight
`GET /platform/flows/runs/{id}` of a parked run left eight `execution_units` rows in `waiting`
— the READERS' units, one per request — each with a `[Scheduler] waiting backup write failed …
ForeignKeyViolation … waiting_flow_runs_run_id_fkey` WARNING, because the id the scheduler tried
to persist as a `waiting_flow_runs.run_id` was the request's execution-unit id, not a flow run.

The mechanism (`core/execution_pipeline/waits.py::_detect_wait`): the pipeline classified ANY
handler result dict whose ``status`` upper-cases to ``WAITING`` as *the request itself* waiting.
A read of a waiting run returns the run's row; the row says ``status: "waiting"``; the reader is
parked. It is armed by the app's ``register_flow_result("flow_run_get", result_key=…)`` — on a
platform-only server the row is nested under ``flow_run_get_result`` with no top-level
``status``, which is why the runtime's own live run never saw it (the first test below is that
control).

★ The "legitimate" half was never legitimate either. `POST /platform/nodus/run` on a script
that suspends returns ``{"status": "WAITING", …}`` with ``waiting_for`` NESTED under ``data`` /
``result`` (`_format_execution_response`), so the detector read neither ``wait_for`` nor
``waiting_for`` and parked the request's unit on the literal event ``"unknown"`` — which
nothing ever emits. The dict path parked units; it never once resumed one.

The contract these tests pin: **a request's execution unit describes the request.** When the
request returns, its unit completes — the thing that is waiting is the run it read or started,
whose own `flow_runs` row and execution unit (ACTIVE-COUNT-WAIT-LEAK-1) already carry the wait.
Only an explicit `ExecutionWaitSignal` parks a request unit, and the scheduler's DB backup
refuses to persist a wait for an id that is not a flow run.

Every route test here CALLS the route (`ROUTE-GUARD-1`); the unit it inspects is the one the
response envelope names.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"


# ── fixtures ───────────────────────────────────────────────────────────────────


def _admin_session_for(uid: str):
    from AINDY.auth.api_key_auth import derive_session_scopes

    def _session():
        return {
            "sub": uid,
            "user_id": uid,
            "auth_type": "jwt",
            "is_admin": True,
            "session_scopes": derive_session_scopes(is_admin=True),
        }

    return _session


@pytest.fixture
def user_id():
    return str(uuid.uuid4())


@pytest.fixture
def client(runtime_only_app, user_id):
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session_for(user_id)
    with TestClient(runtime_only_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def scheduler_spy():
    """The scheduler engine as a spy: a parked request would call `register_wait`, and the
    assertion is on that call — not on a stub that silently absorbs it."""
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


@pytest.fixture
def parked_run(db_session, user_id):
    """A flow run already parked on EVENT, the shape `GET …/runs/{id}` reads."""
    from AINDY.db.models.flow_run import FlowRun

    run = FlowRun(
        id=str(uuid.uuid4()),
        flow_name="tutorial_wait_resume",
        workflow_type="nodus",
        user_id=uuid.UUID(user_id),
        status="waiting",
        waiting_for=EVENT,
        current_node="nodus.execute",
        state={"nodus_wait_event_type": EVENT},
        trace_id=str(uuid.uuid4()),
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def app_profile_result_key():
    """What `aindy-apps-monolith` registers (`apps/rippletrace/bootstrap.py`): a result key that
    makes `run_flow("flow_run_get")["data"]` the BARE run row rather than the flow state."""
    from AINDY.platform_layer import registry

    registry._flow_result_keys["flow_run_get"] = "flow_run_get_result"
    try:
        yield
    finally:
        registry._flow_result_keys.pop("flow_run_get", None)


def _eu(db, eu_id):
    from AINDY.db.models.execution_unit import ExecutionUnit

    db.expire_all()
    row = db.query(ExecutionUnit).filter(ExecutionUnit.id == uuid.UUID(str(eu_id))).one_or_none()
    assert row is not None, f"the envelope named eu_id={eu_id} but no execution_units row exists"
    return row


# ── GET of a waiting run ───────────────────────────────────────────────────────


def test_platform_only_read_of_a_waiting_run_completes_the_reader(
    client, db_session, parked_run, scheduler_spy
):
    """Control: with no result key the row is nested and the detector never fired — the
    runtime's own live run saw exactly this. Pins the shape the other test depends on."""
    response = client.get(f"/platform/flows/runs/{parked_run.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["flow_run_get_result"]["status"] == "waiting"

    assert body["status"] == "success"
    reader = _eu(db_session, body["eu_id"])
    assert reader.status == "completed", reader.status
    scheduler_spy.register_wait.assert_not_called()


def test_app_profile_read_of_a_waiting_run_completes_the_reader(
    client, db_session, parked_run, scheduler_spy, app_profile_result_key
):
    """★ The defect. With the app's result key the handler returns the bare row, whose own
    ``status: "waiting"`` used to be read as the READER waiting: its unit parked, a wait
    registered under the reader's id, a `waiting_flow_runs` FK violation per read."""
    response = client.get(f"/platform/flows/runs/{parked_run.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["status"] == "waiting", "the app-profile shape: the row is `data` itself"
    assert body["data"]["waiting_for"] == EVENT

    assert body["status"] == "success", (
        "the envelope must describe the REQUEST (done), not the run it read (waiting)"
    )
    assert "eu_wait_for" not in body["metadata"]
    reader = _eu(db_session, body["eu_id"])
    assert reader.status == "completed", (
        f"the reader's execution unit was parked ({reader.status}) — a read is not a wait"
    )
    assert reader.wait_condition in (None, {}), reader.wait_condition
    scheduler_spy.register_wait.assert_not_called()


def test_four_app_profile_reads_leave_no_waiting_units_behind(
    client, db_session, parked_run, scheduler_spy, app_profile_result_key
):
    """The live signature was one leaked unit PER READ (eight reads, eight rows)."""
    from AINDY.db.models.execution_unit import ExecutionUnit

    before = db_session.query(ExecutionUnit).filter(ExecutionUnit.status == "waiting").count()
    for _ in range(4):
        assert client.get(f"/platform/flows/runs/{parked_run.id}").status_code == 200
    db_session.expire_all()
    after = db_session.query(ExecutionUnit).filter(ExecutionUnit.status == "waiting").count()
    assert after == before, f"{after - before} reader unit(s) parked by four reads"
    scheduler_spy.register_wait.assert_not_called()


# ── POST /platform/nodus/run on a script that suspends ────────────────────────

# Tutorial 2's shape, minus its sys() calls.
SUSPENDING_SCRIPT = """
let received = get_state("nodus_received_events")
if (received == nil) {
    set_state("phase", 1i)
    set_state("nodus_wait_requested", true)
    set_state("nodus_wait_event_type", "review.approved")
} else {
    set_state("phase", 2i)
}
"""


@pytest.fixture
def in_process_worker(monkeypatch):
    """Serve the adapter's warm-pool request from `nodus_worker.run_one` in this process."""
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker
    from AINDY.runtime import nodus_worker_pool as pool_mod

    class _InProcessPool:
        def execute(self, payload, *, timeout_s):
            return nodus_worker.run_one(payload)

    monkeypatch.setenv("AINDY_NODUS_WARM_POOL", "1")
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _InProcessPool())


def test_starting_a_script_that_suspends_completes_the_request_unit(
    client, db_session, user_id, scheduler_spy, in_process_worker
):
    """★ The other half, and it needs nothing of the app's: the execution record's top-level
    ``status: "WAITING"`` tripped the same branch on any server, and because ``waiting_for``
    is nested the request's unit was parked on the event ``"unknown"`` — for good."""
    from AINDY.db.models.flow_run import FlowRun

    response = client.post(
        "/platform/nodus/run",
        json={"script": SUSPENDING_SCRIPT, "input": {"sprint": "sprint-12"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    record = body["data"]
    assert record["status"] == "WAITING", record
    assert record["nodus_status"] == "waiting", record

    # The RUN is parked, with its own row — that is where the wait lives.
    run = db_session.query(FlowRun).filter(FlowRun.id == record["run_id"]).one()
    assert run.status == "waiting" and run.waiting_for == EVENT
    assert scheduler_spy.register_wait.call_count == 1, (
        "exactly one wait: the run's. A second registration is the request parking itself"
    )
    assert scheduler_spy.register_wait.call_args.kwargs["run_id"] == str(run.id)
    assert scheduler_spy.register_wait.call_args.kwargs["wait_for_event"] == EVENT

    # The REQUEST is done.
    assert body["status"] == "success", body["status"]
    assert "eu_wait_for" not in body["metadata"]
    request_unit = _eu(db_session, body["eu_id"])
    assert request_unit.status == "completed", (
        f"the request's unit is {request_unit.status!r} — parked on "
        f"{(request_unit.wait_condition or {}).get('event_name')!r}; nothing would ever wake it"
    )


# ── the explicit signal still parks — the path that is MEANT to ───────────────


@pytest.mark.parametrize("form", ["raised", "returned"])
def test_an_explicit_wait_signal_still_parks_the_request_unit(db_session, scheduler_spy, form):
    """Liveness control for the detector: the typed signal is the only thing that parks a
    request unit, and it must keep doing so — a change that made every request complete
    would pass the tests above and break the extension contract `ExecutionWaitSignal` states.

    ★ Both forms, because they take DIFFERENT paths: a RAISED signal is caught by the
    pipeline's `except ExecutionWaitSignal` and never reaches `_detect_wait`; a RETURNED
    instance is what `_detect_wait`'s surviving branch exists for. A first draft covered only
    the raise, and a mutation that deleted the detector's branch outright went green."""
    import asyncio

    from AINDY.core.execution_gate import ExecutionWaitSignal
    from AINDY.core.execution_pipeline import ExecutionContext, ExecutionPipeline

    uid = str(uuid.uuid4())
    ctx = ExecutionContext(
        request_id=str(uuid.uuid4()),
        route_name="platform.custom",
        user_id=uid,
        metadata={"db": db_session},
    )
    signal = ExecutionWaitSignal("payment.confirmed", resume_key="inv_1", payload={"invoice": "inv_1"})

    def handler(_ctx):
        if form == "raised":
            raise signal
        return signal

    result = asyncio.run(ExecutionPipeline().run(ctx, handler))
    assert result.eu_status == "waiting"
    assert result.metadata["eu_wait_for"] == "payment.confirmed"
    unit = _eu(db_session, result.metadata["eu_id"])
    assert unit.status == "waiting"
    scheduler_spy.register_wait.assert_called_once()
    assert scheduler_spy.register_wait.call_args.kwargs["wait_for_event"] == "payment.confirmed"


def test_a_wait_shaped_dict_alone_is_not_a_wait(db_session, scheduler_spy):
    """The detector, directly: the exact shape that parked the readers and the nodus request
    — a top-level ``status: WAITING`` with or without a wait name — is a RESULT, not a signal."""
    import asyncio

    from AINDY.core.execution_pipeline import ExecutionContext, ExecutionPipeline

    for payload in (
        {"status": "waiting", "waiting_for": EVENT, "id": "some-run"},  # the bare row
        {"status": "WAITING", "data": {"waiting_for": EVENT}},  # _format_execution_response
        {"status": "WAITING", "wait_for": EVENT},  # the shape the old detector wanted
    ):
        ctx = ExecutionContext(
            request_id=str(uuid.uuid4()),
            route_name="platform.custom",
            user_id=str(uuid.uuid4()),
            metadata={"db": db_session},
        )
        result = asyncio.run(ExecutionPipeline().run(ctx, lambda _c, p=payload: p))
        assert result.success and result.eu_status is None, (payload, result.eu_status)
        assert result.data == payload, "the result itself is untouched"
        assert _eu(db_session, result.metadata["eu_id"]).status == "completed", payload
    scheduler_spy.register_wait.assert_not_called()


# ── the scheduler's DB backup refuses an id that is not a flow run ────────────


def _engine_with_waiting_entry(run_id: str, *, eu_type: str = "flow"):
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    engine = SchedulerEngine.__new__(SchedulerEngine)
    import threading

    engine._lock = threading.RLock()
    engine._waiting = {
        str(run_id): {
            "wait_for": EVENT,
            "correlation_id": "corr",
            "eu_id": str(run_id),
            "priority": "normal",
            "eu_type": eu_type,
            "wait_condition": {"type": "event", "event_name": EVENT},
        }
    }
    return engine


def test_backup_write_is_skipped_for_an_id_that_is_not_a_flow_run(db_session, testing_session_factory, caplog):
    """Ask 3 of FR-29. On Postgres this was a ForeignKeyViolation WARNING per read; on SQLite the
    row simply lands (FKs are not enforced) — so the assertion is on the row, which
    discriminates on both engines. Positive control first: a real flow run DOES get its row."""
    import logging

    from AINDY.db.models.flow_run import FlowRun
    from AINDY.db.models.waiting_flow_run import WaitingFlowRun

    real = FlowRun(
        id=str(uuid.uuid4()), flow_name="f", user_id=uuid.uuid4(), status="waiting",
        waiting_for=EVENT, trace_id="t",
    )
    db_session.add(real)
    db_session.commit()

    with patch("AINDY.kernel.scheduler.persistence._get_session_factory", return_value=testing_session_factory):
        _engine_with_waiting_entry(real.id)._persist_wait_backup(real.id)
        assert db_session.query(WaitingFlowRun).filter(WaitingFlowRun.run_id == real.id).count() == 1, (
            "liveness: the backup write must still happen for a flow run, or the test proves nothing"
        )

        not_a_run = str(uuid.uuid4())  # a request's execution-unit id
        with caplog.at_level(logging.WARNING, logger="AINDY.kernel.scheduler.common"):
            _engine_with_waiting_entry(not_a_run)._persist_wait_backup(not_a_run)
        db_session.expire_all()
        assert db_session.query(WaitingFlowRun).filter(WaitingFlowRun.run_id == not_a_run).count() == 0, (
            "a waiting_flow_runs row was written for an id that is not a flow run"
        )
        assert not [r for r in caplog.records if "backup write failed" in r.getMessage()], (
            "an id that is not a flow run is a skip, not a failure that reads like data loss"
        )
