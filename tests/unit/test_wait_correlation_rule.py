"""`WAIT-PAYLOAD-PATH-1` (b) — ONE correlation rule for waking a wait, on both paths.

Two copies of the rule existed and disagreed. The local scan (`waits.py`) vetoed only when BOTH
the wait and the emit carried a correlation and they differed; the cross-instance fallback
(`cross_instance.py`) vetoed whenever the emit carried one the wait's did not equal — and an emit
ALWAYS carries one (`_notify_scheduler_of_event` falls back to the trace id). So a wait registered
with ``correlation_id=None`` woke locally on any emit and never cross-instance: two instances,
two answers to the same emit. `test_multi_instance_resume.py` never saw it — it registers None
and publishes None.

★ Reading the two copies side by side surfaced a THIRD thing, live on the per-run route: a resume
payload that happens to carry a ``correlation_id`` key (a common field name in a client's own
domain) was read as the wake's correlation, differed from the run's trace, and vetoed the wake —
the payload landed on the row, the route said ``resumed: true``, and the run stayed parked.
Probed before the fix: ``payload on row: True | woken: False``. With `run_id` naming the run,
correlation cannot be allowed a veto: the run id is the only key unique to the run.

What is pinned:

* the predicate's truth table (`correlation_admits`);
* **parity** — the same (wait, emit, run-scoped) table driven through BOTH the local scan and the
  cross-instance fallback gives the same answer for every row. This is the test the entry asked
  for: "publish with a correlation_id against a None wait on both paths";
* the route trap, through `route_event` on a real `SchedulerEngine`;
* the cross-instance fallback is driven for real — only the Redis registry and the backup-row
  load are faked at their boundaries, and `_enqueue_resume` is the real one (the wake is read
  back off the queue, not off a mock).

Mutation-checked: restore the old cross-instance comparison → the parity test fails on the
``None`` wait row; drop `run_scoped` from the predicate → the route-trap test and the run-scoped
parity row fail; apply the old strict rule locally → the None-wait local row fails.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

EVENT = "order.completed"

# (wait correlation, emit correlation, run-scoped) → must the wake be admitted?
TABLE = [
    (None, None, False, True),
    (None, "emit-1", False, True),      # ★ the disagreement: local said yes, cross said no
    ("wait-1", None, False, True),
    ("wait-1", "wait-1", False, True),
    ("wait-1", "emit-1", False, False),  # both carry one and they differ — the only veto
    ("wait-1", "emit-1", True, True),    # ★ run-scoped: the run id is decisive
    (None, "emit-1", True, True),
]
IDS = ["none/none", "none/emit", "wait/none", "equal", "differ", "differ-run-scoped", "none/emit-run-scoped"]


@pytest.mark.parametrize("wait_corr,emit_corr,run_scoped,expected", TABLE, ids=IDS)
def test_the_predicate(wait_corr, emit_corr, run_scoped, expected):
    from AINDY.kernel.scheduler.common import correlation_admits

    assert correlation_admits(wait_corr, emit_corr, run_scoped=run_scoped) is expected


# ── the two paths, driven with the same table ─────────────────────────────────


def _engine():
    from AINDY.kernel.scheduler.engine import SchedulerEngine

    eng = SchedulerEngine()
    eng.mark_rehydration_complete()
    return eng


def _drain(engine) -> set[str]:
    woken = set()
    while (item := engine.dequeue_next()) is not None:
        woken.add(str(item.run_id))
    return woken


def _local(wait_corr, emit_corr, run_scoped) -> bool:
    """The local scan: the wait lives in THIS engine's `_waiting`."""
    eng = _engine()
    run_id = f"run-{uuid.uuid4()}"
    eng.register_wait(
        run_id=run_id, wait_for_event=EVENT, tenant_id="t", eu_id="",
        resume_callback=lambda: None, correlation_id=wait_corr,
    )
    with patch("AINDY.kernel.scheduler.waits._cross_instance_resume", return_value=0):
        eng.notify_event(
            EVENT, correlation_id=emit_corr, run_id=run_id if run_scoped else None, broadcast=False
        )
    return _drain(eng) == {run_id}


def _cross(wait_corr, emit_corr, run_scoped) -> bool:
    """The cross-instance fallback: the wait is in Redis + the backup table, in NO engine's
    `_waiting` (its instance is gone). Faked at exactly those two boundaries."""
    from AINDY.kernel.resume_spec import ResumeSpec
    from AINDY.kernel.scheduler.cross_instance import _cross_instance_resume

    eng = _engine()
    run_id = f"run-{uuid.uuid4()}"
    spec = ResumeSpec(handler="eu", eu_id="eu-1", tenant_id="t", run_id=run_id, eu_type="flow")

    class _Registry:
        _redis = object()

        def __init__(self, _client=None):
            pass

        def get_all_specs(self):
            return {run_id: spec}

        def unregister_if_exists(self, rid):
            return rid == run_id

    row = SimpleNamespace(event_type=EVENT, correlation_id=wait_corr, priority="normal", eu_id="eu-1")
    with patch("AINDY.kernel.redis_wait_registry.RedisWaitRegistry", _Registry), patch(
        "AINDY.kernel.event_bus.get_redis_client", return_value=object()
    ), patch("AINDY.kernel.scheduler_engine._load_wait_entry_from_db", return_value=row), patch(
        "AINDY.kernel.resume_spec.build_callback_from_spec", return_value=lambda: None
    ):
        resumed = _cross_instance_resume(
            eng, EVENT, emit_corr, set(), run_id=run_id if run_scoped else None
        )
    return resumed == 1 and _drain(eng) == {run_id}


@pytest.mark.parametrize("wait_corr,emit_corr,run_scoped,expected", TABLE, ids=IDS)
def test_local_scan_follows_the_rule(wait_corr, emit_corr, run_scoped, expected):
    assert _local(wait_corr, emit_corr, run_scoped) is expected


@pytest.mark.parametrize("wait_corr,emit_corr,run_scoped,expected", TABLE, ids=IDS)
def test_cross_instance_fallback_follows_the_rule(wait_corr, emit_corr, run_scoped, expected):
    assert _cross(wait_corr, emit_corr, run_scoped) is expected


@pytest.mark.parametrize("wait_corr,emit_corr,run_scoped,_", TABLE, ids=IDS)
def test_both_paths_give_the_same_answer(wait_corr, emit_corr, run_scoped, _):
    """★ Parity is the claim, independent of what the rule IS: two instances must not answer one
    emit differently. Kept separate from the two above so that a future change to the rule that
    updates one copy and not the other is caught even if the table is updated to match it."""
    assert _local(wait_corr, emit_corr, run_scoped) is _cross(wait_corr, emit_corr, run_scoped)


# ── the route trap, on the real per-run path ─────────────────────────────────


def test_a_payload_that_carries_a_correlation_id_key_still_wakes_the_named_run(db_session):
    """The live shape: `POST …/runs/{id}/resume` with a payload whose keys are the client's own.
    Before the fix the payload was injected and the wake vetoed by that key — `resumed: true`
    on the wire, run parked forever."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import route_event

    eng = _engine()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=eng):
        trace = str(uuid.uuid4())
        run = FlowRun(
            id=str(uuid.uuid4()), flow_name="corr_trap", status="waiting", current_node="n",
            state={"trace_id": trace}, waiting_for="review.approved", trace_id=trace,
        )
        db_session.add(run)
        db_session.commit()
        eng.register_wait(
            run_id=run.id, wait_for_event="review.approved", tenant_id="t", eu_id="",
            resume_callback=lambda: None, correlation_id=trace, trace_id=trace,
        )

        results = route_event(
            "review.approved",
            {"reviewer": "shawn", "approved": True, "correlation_id": "client-ref-42"},
            db_session,
            run_id=run.id,
        )

    assert results == [{"run_id": run.id, "payload_injected": True, "woken": True}]
    assert _drain(eng) == {run.id}, "the named run was injected into and then not woken"
    assert eng.waiting_for(run.id) is None
