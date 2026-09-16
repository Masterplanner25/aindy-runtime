"""
tests/integration/test_multi_instance_resume.py
─────────────────────────────────────────────────
Integration test: flow started on Instance A is resumed by Instance B.

Uses fakeredis with shared state to simulate two independent SchedulerEngine
instances connected to the same Redis backend.

Requires: fakeredis (pip install fakeredis)

Ported from: masterplan-infiniteweave-monday-node-2025-0411/tests/integration/
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

try:
    import fakeredis

    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False


pytestmark = [
    pytest.mark.multi_instance,
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed"),
]


@pytest.fixture
def shared_redis():
    """Single fakeredis server shared by both SchedulerEngine instances."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


def _make_engine():
    from AINDY.kernel.scheduler_engine import SchedulerEngine

    engine = SchedulerEngine()
    engine.mark_rehydration_complete()
    return engine


class TestMultiInstanceResume:
    def test_instance_b_resumes_flow_started_on_instance_a(
        self,
        shared_redis,
        db_session,
        db_session_factory,
        caplog,
    ):
        """
        Scenario:
        1. Instance A: register_wait("run-123", "order.completed", ...)
        2. Instance A dies (simulated by not using it further)
        3. Instance B receives notify_event("order.completed")
        4. Assert: Instance B enqueues resume for run-123
        5. Assert: the claimed callback RUNS its real code path. (Until 2026-09-16 this step
           patched `resume_execution_unit` to a spy and asserted the spy was called — which
           could not see that the real callback rolled back on close, nor that it moved only
           the unit and never the run. The spec here is `eu_type="task"`, which has no rebuild,
           so the real path is the unit-only fallback; it must say so at WARNING.)
        """
        import logging

        from AINDY.db.models.waiting_flow_run import WaitingFlowRun
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
        from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

        instance_a = _make_engine()
        eu_id = "eu-abc"
        run_id = "run-123"
        tenant_id = "tenant-test"

        spec = ResumeSpec(
            handler=RESUME_HANDLER_EU,
            eu_id=eu_id,
            tenant_id=tenant_id,
            run_id=run_id,
            eu_type="task",
        )
        registry = RedisWaitRegistry(shared_redis)
        registry.register(run_id, spec)

        db_session.add(
            FlowRun(
                id=run_id,
                flow_name="test.flow",
                workflow_type="test_flow",
                state={},
                current_node="wait_node",
                status="waiting",
                waiting_for="order.completed",
                trace_id=None,
            )
        )
        db_session.add(
            WaitingFlowRun(
                run_id=run_id,
                event_type="order.completed",
                correlation_id=None,
                eu_id=eu_id,
                priority="normal",
                instance_id="instance-a",
            )
        )
        db_session.commit()

        instance_b = _make_engine()

        with patch("AINDY.kernel.event_bus.get_redis_client", return_value=shared_redis), patch(
            "AINDY.db.SessionLocal",
            db_session_factory,
        ), patch("AINDY.db.database.SessionLocal", db_session_factory):
            count = instance_b.notify_event("order.completed", broadcast=False)

            assert count == 1
            item = instance_b.dequeue_next()
            assert item is not None
            assert item.execution_unit_id == eu_id
            assert item.run_id == run_id

            with caplog.at_level(logging.WARNING, logger="AINDY.kernel.resume_spec"):
                item.run_callback()

        assert any("cannot be rebuilt" in r.getMessage() for r in caplog.records), (
            "the claimed callback did not run its real path (a `task` spec has no rebuild, so "
            "the unit-only fallback must run and warn)"
        )
        assert registry.get_spec(run_id) is None

        # Instance A is intentionally unused after registration; this keeps the
        # test faithful to the "origin instance died" scenario.
        assert instance_a.waiting_for(run_id) is None

    def test_a_run_scoped_wake_resumes_only_that_run_cross_instance(
        self,
        shared_redis,
        db_session,
        db_session_factory,
    ):
        """RESUME-FANOUT-UNSCOPED-1 on the CROSS-INSTANCE path.

        Two runs parked on instance A, same event, no correlation (the widest match). Instance B
        receives a wake scoped to run-A. Only run-A may be claimed; run-B's spec must survive in
        the shared registry. Without the `run_id` filter in `_cross_instance_resume` this is the
        live fan-out, one process boundary over.
        """
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.db.models.waiting_flow_run import WaitingFlowRun
        from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
        from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

        registry = RedisWaitRegistry(shared_redis)
        for run_id, eu_id in (("run-scoped-A", "eu-sA"), ("run-scoped-B", "eu-sB")):
            registry.register(
                run_id,
                ResumeSpec(
                    handler=RESUME_HANDLER_EU, eu_id=eu_id, tenant_id="tenant-scoped",
                    run_id=run_id, eu_type="flow",
                ),
            )
            db_session.add(
                FlowRun(
                    id=run_id, flow_name="test.flow", workflow_type="test_flow", state={},
                    current_node="wait_node", status="waiting",
                    waiting_for="review.approved", trace_id=None,
                )
            )
            db_session.add(
                WaitingFlowRun(
                    run_id=run_id, event_type="review.approved", correlation_id=None,
                    eu_id=eu_id, priority="normal", instance_id="instance-a",
                )
            )
        db_session.commit()

        instance_b = _make_engine()
        with patch("AINDY.kernel.event_bus.get_redis_client", return_value=shared_redis), patch(
            "AINDY.db.SessionLocal", db_session_factory
        ):
            count = instance_b.notify_event(
                "review.approved", run_id="run-scoped-A", broadcast=False
            )

        assert count == 1
        item = instance_b.dequeue_next()
        assert item is not None and item.run_id == "run-scoped-A"
        assert instance_b.dequeue_next() is None, "run-B was woken by run-A's resume"
        assert registry.get_spec("run-scoped-A") is None
        assert registry.get_spec("run-scoped-B") is not None, "run-B's wait was claimed"

    def test_a_none_correlation_wait_resumes_cross_instance_on_a_correlated_emit(
        self,
        shared_redis,
        db_session,
        db_session_factory,
    ):
        """WAIT-PAYLOAD-PATH-1 (b) — the row of the rule table the two paths disagreed on.

        A wait registered with ``correlation_id=None`` (the widest match) on instance A; the
        emit carries one — as EVERY emit does, since `_notify_scheduler_of_event` falls back to
        the trace id. Locally that wait always resumed. Cross-instance it NEVER did: the old
        comparison (`if correlation_id and wait_corr != correlation_id`) vetoed it, so a
        None-correlation wait whose instance had died waited for the watchdog. Every other test
        in this file registers None AND publishes None, which is why it went unseen.
        """
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.db.models.waiting_flow_run import WaitingFlowRun
        from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
        from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

        registry = RedisWaitRegistry(shared_redis)
        registry.register(
            "run-nonecorr",
            ResumeSpec(
                handler=RESUME_HANDLER_EU, eu_id="eu-nc", tenant_id="tenant-nc",
                run_id="run-nonecorr", eu_type="flow",
            ),
        )
        db_session.add(
            FlowRun(
                id="run-nonecorr", flow_name="test.flow", workflow_type="test_flow", state={},
                current_node="wait_node", status="waiting", waiting_for="invoice.paid",
                trace_id=None,
            )
        )
        db_session.add(
            WaitingFlowRun(
                run_id="run-nonecorr", event_type="invoice.paid", correlation_id=None,
                eu_id="eu-nc", priority="normal", instance_id="instance-a",
            )
        )
        db_session.commit()

        instance_b = _make_engine()
        with patch("AINDY.kernel.event_bus.get_redis_client", return_value=shared_redis), patch(
            "AINDY.db.SessionLocal", db_session_factory
        ):
            count = instance_b.notify_event(
                "invoice.paid", correlation_id="trace-of-the-emitter", broadcast=False
            )

        assert count == 1, "a None-correlation wait must resume cross-instance on a correlated emit"
        item = instance_b.dequeue_next()
        assert item is not None and item.run_id == "run-nonecorr"
        assert registry.get_spec("run-nonecorr") is None

    def test_only_one_instance_claims_concurrent_resume(
        self,
        shared_redis,
        db_session,
        db_session_factory,
    ):
        """
        When two instances call notify_event for the same run_id,
        exactly one enqueues the resume and the other finds the key already deleted.
        """
        from AINDY.db.models.waiting_flow_run import WaitingFlowRun
        from AINDY.db.models.flow_run import FlowRun
        from AINDY.kernel.redis_wait_registry import RedisWaitRegistry
        from AINDY.kernel.resume_spec import RESUME_HANDLER_EU, ResumeSpec

        run_id = "run-race"
        eu_id = "eu-race"
        tenant_id = "tenant-race"

        RedisWaitRegistry(shared_redis).register(
            run_id,
            ResumeSpec(
                handler=RESUME_HANDLER_EU,
                eu_id=eu_id,
                tenant_id=tenant_id,
                run_id=run_id,
                eu_type="flow",
            ),
        )
        db_session.add(
            FlowRun(
                id=run_id,
                flow_name="test.flow",
                workflow_type="test_flow",
                state={},
                current_node="wait_node",
                status="waiting",
                waiting_for="payment.received",
                trace_id=None,
            )
        )
        db_session.add(
            WaitingFlowRun(
                run_id=run_id,
                event_type="payment.received",
                correlation_id=None,
                eu_id=eu_id,
                priority="normal",
                instance_id="instance-a",
            )
        )
        db_session.commit()

        instance_b1 = _make_engine()
        instance_b2 = _make_engine()

        with patch("AINDY.kernel.event_bus.get_redis_client", return_value=shared_redis), patch(
            "AINDY.db.SessionLocal",
            db_session_factory,
        ), patch(
            "AINDY.core.execution_unit_service.ExecutionUnitService.resume_execution_unit",
        ):
            first = instance_b1.notify_event("payment.received", broadcast=False)
            second = instance_b2.notify_event("payment.received", broadcast=False)

        total_items = (
            instance_b1.queue_depth()["normal"] +
            instance_b2.queue_depth()["normal"]
        )
        assert first + second == 1
        assert total_items == 1

    def test_no_redis_falls_back_to_local_only(self):
        """When Redis is unavailable, cross-instance path is silently skipped."""
        engine = _make_engine()

        with patch("AINDY.kernel.event_bus.get_redis_client", return_value=None):
            count = engine.notify_event("any.event", broadcast=False)

        assert count == 0
        assert engine.queue_depth() == {"high": 0, "normal": 0, "low": 0}
