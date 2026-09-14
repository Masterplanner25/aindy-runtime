"""`ACTIVE-COUNT-WAIT-LEAK-1` — a parked run holds no tenant concurrency slot.

Observed live (2026-09-13): after four waiting runs accumulated, a read-only
`GET /platform/flows/runs/{id}` returned **429 "Too many concurrent executions for this
tenant"**. `PersistentFlowRunner` called `mark_started` at start, `mark_completed` on
success/failure, and **nothing on WAIT** — so a run parked as a durable row kept one of the
tenant's `MAX_CONCURRENT_PER_TENANT` (5) slots for as long as it waited. "Parked as a row, costs
nothing while waiting" cost one fifth of the admission budget per wait.

A second defect sat beside it: `can_execute` was re-asked at EVERY node with the run's own slot
already in the count (`QUOTA-ACCRUAL-ORPHAN-1`'s shape), so at exactly the cap the last-admitted
run parked itself on `resource_available` while still holding the slot it was refused for.

The model now: **a run holds a slot exactly while it is executing.** Acquired at its first node
after a start OR a resume, after `can_execute` judges it against a count that does not include
it; released by every WAIT and by completion/failure. A refusal parks the run holding nothing.

★ `can_execute` short-circuits under `settings.is_testing` — the standing rule about test-mode
guards ABOVE the decision. Every test here patches `is_testing` False on the settings class (a
pydantic property), or it would be exercising a `return True, None`.

★ The assertions read the counter an operator reads (`get_tenant_active`), not a log line.

Mutation-checked: drop `_release_slot_on_wait` from the WAIT branch → the parked-count tests
fail; acquire unconditionally at start again → the at-cap test fails; drop the `_holds_slot`
guard in completion → the never-acquired test fails.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

pytestmark = pytest.mark.runtime_only

EVENT = "review.approved"


@pytest.fixture
def enforcing():
    from AINDY.config import Settings

    with patch.object(Settings, "is_testing", new_callable=PropertyMock, return_value=False):
        yield


@pytest.fixture
def rm(monkeypatch):
    """A fresh ResourceManager with a small cap, installed as the singleton."""
    import AINDY.kernel.resource_manager as rm_mod

    fresh = rm_mod.ResourceManager()
    fresh.MAX_CONCURRENT_PER_TENANT = 2
    monkeypatch.setattr(rm_mod, "_RESOURCE_MANAGER", fresh)
    return fresh


@pytest.fixture
def scheduler_spy():
    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


@pytest.fixture
def parking_flow():
    """A one-node flow whose node parks on its first run and completes on its second,
    recording the tenant's active count as seen FROM INSIDE the node."""
    from AINDY.runtime.flow_engine import registry as reg

    node = f"slot_probe_{uuid.uuid4().hex[:6]}"
    name = f"slot_probe_flow_{uuid.uuid4().hex[:6]}"
    seen_active: list[int] = []

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        from AINDY.kernel.resource_manager import get_resource_manager

        seen_active.append(get_resource_manager().get_tenant_active(state["tenant"]))
        if "parked" not in state:
            return {"status": "WAIT", "wait_for": EVENT, "output_patch": {"parked": True}}
        return {"status": "SUCCESS", "output_patch": {"done": True}}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow(name, flow)
    try:
        yield name, flow, seen_active
    finally:
        reg.FLOW_REGISTRY.pop(name, None)
        reg.NODE_REGISTRY.pop(node, None)


def _start(db, flow, name, user_id):
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    runner = PersistentFlowRunner(flow=flow, db=db, user_id=str(user_id), workflow_type=None)
    return runner, runner.start({"tenant": str(user_id)}, flow_name=name)


def _resume(db, flow, run_id, user_id):
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    return PersistentFlowRunner(flow=flow, db=db, user_id=str(user_id), workflow_type=None).resume(run_id)


def _run(db, run_id):
    from AINDY.db.models.flow_run import FlowRun

    db.expire_all()
    return db.query(FlowRun).filter(FlowRun.id == run_id).one()


# ── the leak ─────────────────────────────────────────────────────────────────


def test_a_parked_run_holds_no_slot(db_session, rm, enforcing, scheduler_spy, parking_flow):
    """★ THE live observation, inverted: park runs up to and past the cap; the tenant's active
    count is 0 and admission is open."""
    name, flow, seen = parking_flow
    user = uuid.uuid4()
    tenant = str(user)

    runs = []
    for _ in range(rm.MAX_CONCURRENT_PER_TENANT + 2):
        _, response = _start(db_session, flow, name, user)
        assert response["status"] == "WAITING", response
        runs.append(response["run_id"])

    assert rm.get_tenant_active(tenant) == 0, (
        f"{rm.get_tenant_active(tenant)} slot(s) held by runs that are all parked — the tenant "
        "is paying admission budget for rows"
    )
    assert rm.can_execute(tenant) == (True, None)
    assert all(_run(db_session, r).status == "waiting" for r in runs)
    # Liveness: each run DID hold a slot while its node executed — seen from inside the node.
    assert seen == [1] * len(runs), seen


def test_a_resumed_run_reacquires_and_releases_on_completion(
    db_session, rm, enforcing, scheduler_spy, parking_flow
):
    """The other half of the cycle: resume → slot held during the node → 0 after completion."""
    name, flow, seen = parking_flow
    user = uuid.uuid4()
    tenant = str(user)
    _, first = _start(db_session, flow, name, user)
    run_id = first["run_id"]
    assert rm.get_tenant_active(tenant) == 0

    second = _resume(db_session, flow, run_id, user)

    assert second["status"] in {"COMPLETED", "SUCCESS"}, second
    assert _run(db_session, run_id).status == "success"
    assert seen == [1, 1], "the resumed node must run holding exactly one slot"
    assert rm.get_tenant_active(tenant) == 0, "completion did not release the re-acquired slot"


def test_a_tenant_at_the_cap_parks_on_resources_holding_nothing(
    db_session, rm, enforcing, scheduler_spy, parking_flow
):
    """★★ The QUOTA-ACCRUAL-ORPHAN-1 shape, at the flow runner. With the cap full of OTHER
    work, a new run must park on `resource_available` without a slot — and not run its node.
    Before: it took slot cap+1 first, then asked, then parked holding it."""
    name, flow, seen = parking_flow
    user = uuid.uuid4()
    tenant = str(user)
    for i in range(rm.MAX_CONCURRENT_PER_TENANT):
        rm.mark_started(tenant, f"other-{i}")  # the cap, owned by executions that are not ours

    _, response = _start(db_session, flow, name, user)

    assert response["status"] == "WAITING", response
    assert response["data"]["waiting_for"] == "resource_available"
    assert seen == [], "a refused run executed its node anyway"
    assert rm.get_tenant_active(tenant) == rm.MAX_CONCURRENT_PER_TENANT, (
        "a refused run must not hold a slot while it waits for one"
    )
    run = _run(db_session, response["run_id"])
    assert run.status == "waiting" and run.waiting_for == "resource_available"

    # Capacity frees → the run resumes, acquires, and this time parks on its EVENT — still
    # holding nothing afterwards.
    rm.mark_completed(tenant, "other-0")
    resumed = _resume(db_session, flow, str(run.id), user)
    assert resumed["status"] == "WAITING" and resumed["data"]["waiting_for"] == EVENT
    assert seen == [rm.MAX_CONCURRENT_PER_TENANT], "the node ran with the cap exactly full — correct"
    assert rm.get_tenant_active(tenant) == rm.MAX_CONCURRENT_PER_TENANT - 1


def test_a_runner_that_never_acquired_does_not_release_someone_elses_slot(
    db_session, rm, enforcing, scheduler_spy
):
    """`mark_completed` on a runner that holds nothing would decrement a slot another run is
    using — an under-count is over-admission. A node that FAILS still acquired (it ran), so the
    case is a runner that fails BEFORE its first node: a run whose flow graph is incomplete."""
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    user = uuid.uuid4()
    tenant = str(user)
    rm.mark_started(tenant, "other-0")  # someone else's live execution

    node = f"orphan_{uuid.uuid4().hex[:6]}"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        return {"status": "SUCCESS"}

    # `start` names a node that is not registered → fails before any acquisition.
    flow = {"start": "not_a_registered_node", "end": [node], "edges": {}}
    name = f"orphan_flow_{uuid.uuid4().hex[:6]}"
    reg.register_flow(name, flow)
    try:
        response = PersistentFlowRunner(
            flow=flow, db=db_session, user_id=tenant, workflow_type=None
        ).start({}, flow_name=name)
        assert response["status"] == "FAILED", response
        assert rm.get_tenant_active(tenant) == 1, (
            "the failing runner released a slot it never held"
        )
    finally:
        reg.FLOW_REGISTRY.pop(name, None)
        reg.NODE_REGISTRY.pop(node, None)


# ── the ResourceManager seam ─────────────────────────────────────────────────


def test_mark_waiting_releases_the_slot_and_keeps_the_snapshot(rm):
    """Unlike `mark_completed`, a wait keeps the usage snapshot: the run will be back, and its
    wall-time / syscall / token accrual continues. The capacity event is the same."""
    rm.mark_started("t", "eu-1")
    rm.mark_started("t", "eu-2")
    assert rm.get_tenant_active("t") == 2 == rm.MAX_CONCURRENT_PER_TENANT

    with patch("AINDY.kernel.event_bus.publish_event") as publish:
        rm.mark_waiting("t", "eu-1")

    assert rm.get_tenant_active("t") == 1
    assert "eu-1" not in rm._pending_purge, "a parked run's snapshot must survive the wait"
    publish.assert_called_once_with("resource_available")

    rm.mark_completed("t", "eu-2")
    assert "eu-2" in rm._pending_purge, "liveness: completion still queues the purge"


def test_the_flow_eu_is_parked_with_its_run(db_session, rm, enforcing, scheduler_spy, parking_flow):
    """The durable record agrees with the row: `flow|flow_run` units were observed stuck
    `executing` for parked runs. On WAIT the run's own EU goes `waiting` with the condition;
    the resume callback's `resume_execution_unit` can then do waiting → resumed → executing."""
    from AINDY.core.execution_unit_service import ExecutionUnitService

    name, flow, _ = parking_flow
    user = uuid.uuid4()
    _, response = _start(db_session, flow, name, user)
    assert response["status"] == "WAITING"

    db_session.expire_all()
    eu = ExecutionUnitService(db_session).get_by_source("flow_run", response["run_id"])
    assert eu is not None
    assert eu.status == "waiting", f"the run parked but its EU is {eu.status!r}"
    assert (eu.wait_condition or {}).get("event_name") == EVENT
