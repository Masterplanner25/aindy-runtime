"""End-to-end coverage of the event bus pub/sub wire (EVENTBUS-COVERAGE-1).

The bus exists so a flow that entered WAIT on instance A can be resumed by an event
received on instance B. Until now **no test exercised that path**: 0 integration tests
called `EventBus.publish()`, all `notify_event(...)` calls in `tests/integration/`
passed `broadcast=False`, and nothing anywhere called `start_subscriber()`. The unit
suite drives `publish` through a `MagicMock` and `_handle_message` directly, which
covers each half but never the wire between them.

That gap is why `EVENTBUS-PUBLISH-LATCH-1` — a publisher that permanently disabled
cross-instance propagation after three transient failures — survived in `main` until
it was found by reading the code.

What this exercises, for real, over a real Redis:

    EventBus.publish()  →  redis pub/sub  →  _subscriber_loop (a live thread)
                        →  _handle_message  →  SchedulerEngine.notify_event

**Marker choice is deliberate.** `redis`, *not* `integration`: this needs Redis, not
Postgres, and `pytest.mark.integration` triggers the conftest guard that skips when
`DATABASE_URL` is not a live PostgreSQL URL (see the marker hazard in CLAUDE.md). The
Integration CI job runs `pytest -c pytest.integration.ini` with `testpaths =
tests/integration` and no `-m` filter, so this runs there regardless of marker.

**Skips only when `REDIS_URL` is unset** (i.e. local dev without Redis). If `REDIS_URL`
*is* set — as it always is in the Integration job — a connection failure is a **test
failure**, not a skip. Otherwise this suite could silently stop covering the wire and
still look green, which is the failure mode it was written to end.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from AINDY.kernel.event_bus import EventBus

pytestmark = pytest.mark.redis

# How long to wait for a message to traverse the wire before calling it a failure.
# Generous: the assertion is on an observable effect, polled — not a fixed sleep, which
# is the classic flaky-test recipe (see FLAKY-1, already one required-check coin flip
# too many).
_WIRE_DEADLINE_SECS = 10.0
_POLL_INTERVAL_SECS = 0.05


def _redis_url() -> str | None:
    return os.getenv("REDIS_URL") or None


@pytest.fixture(scope="module")
def redis_url() -> str:
    url = _redis_url()
    if not url:
        pytest.skip("REDIS_URL not set — local dev without Redis")
    # Deliberately NOT wrapped in a skip: if REDIS_URL is configured, the wire must
    # work. A skip here would hide the coverage loss this suite exists to prevent.
    import redis as _redis

    client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=5)
    client.ping()
    return url


class _RecordingEngine:
    """Stands in for SchedulerEngine, capturing what the subscriber dispatches."""

    def __init__(self, rehydrated: bool = True) -> None:
        self._rehydrated = rehydrated
        self.calls: list[tuple[str, str | None, bool]] = []
        self._lock = threading.Lock()

    def is_rehydrated(self) -> bool:
        return self._rehydrated

    def mark_rehydrated(self) -> None:
        self._rehydrated = True

    def notify_event(self, event_type, *, correlation_id=None, broadcast=True):
        with self._lock:
            self.calls.append((event_type, correlation_id, broadcast))
        return 1

    def received(self) -> list[tuple[str, str | None, bool]]:
        with self._lock:
            return list(self.calls)


@pytest.fixture
def engine(monkeypatch):
    """Patch the scheduler engine the subscriber thread resolves per message."""
    recorder = _RecordingEngine()
    monkeypatch.setattr(
        "AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: recorder
    )
    return recorder


def _make_bus(instance_id: str) -> EventBus:
    """A bus with an explicit instance id.

    **Load-bearing.** `_get_instance_id()` derives from hostname, so two buses in one
    process share an identity — pinned by
    `test_event_bus.py::test_two_buses_in_one_process_share_an_identity`. Without
    distinct ids the subscriber's own-instance filter silently drops every message and
    the test passes for entirely the wrong reason.
    """
    bus = EventBus()
    bus._enabled = True
    bus._instance_id = instance_id
    return bus


@pytest.fixture
def subscriber(engine, redis_url):
    """A started subscriber bus, torn down without leaking the daemon thread."""
    bus = _make_bus(f"subscriber-{uuid.uuid4().hex[:8]}")
    bus.start_subscriber()
    yield bus
    bus.stop_subscriber()
    # `pubsub.listen()` blocks, so the loop only notices _stop_event after a message.
    # Nudge it so the thread exits promptly instead of lingering as a daemon.
    try:
        _make_bus("teardown").publish("wire.teardown.nudge")
    except Exception:
        pass


def _publish_until_observed(
    publisher: EventBus,
    event_type: str,
    predicate,
    *,
    correlation_id: str | None = None,
    deadline_secs: float = _WIRE_DEADLINE_SECS,
) -> bool:
    """Publish repeatedly until *predicate* holds or the deadline expires.

    Republishing is what makes this deterministic rather than racy. Redis pub/sub has
    no persistence: a message published before the subscriber's `SUBSCRIBE` lands is
    simply gone, and there is no exposed readiness signal to wait on. Sleeping "long
    enough" first is exactly the fixed-sleep pattern that produces flaky tests, so
    instead the publish is retried inside the polling loop.
    """
    deadline = time.monotonic() + deadline_secs
    while time.monotonic() < deadline:
        publisher.publish(event_type, correlation_id=correlation_id)
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_SECS)
    return predicate()


class TestCrossInstancePropagation:
    def test_an_event_published_on_a_reaches_the_subscriber_on_b(self, subscriber, engine):
        """The whole reason the module exists."""
        publisher = _make_bus("publisher-a")

        delivered = _publish_until_observed(
            publisher,
            "order.completed",
            lambda: any(c[0] == "order.completed" for c in engine.received()),
        )

        assert delivered, (
            "no event crossed the wire within "
            f"{_WIRE_DEADLINE_SECS}s — publish→redis→subscriber→notify_event is broken"
        )

    def test_correlation_id_survives_the_wire(self, subscriber, engine):
        publisher = _make_bus("publisher-a")
        chain = f"chain-{uuid.uuid4().hex[:8]}"

        delivered = _publish_until_observed(
            publisher,
            "payment.received",
            lambda: any(c[1] == chain for c in engine.received()),
            correlation_id=chain,
        )

        assert delivered, "correlation_id did not survive serialization across Redis"

    def test_dispatch_suppresses_rebroadcast(self, subscriber, engine):
        """`broadcast=False` on the receiving side is what stops an event
        ping-ponging between instances forever."""
        publisher = _make_bus("publisher-a")

        _publish_until_observed(
            publisher,
            "loop.check",
            lambda: any(c[0] == "loop.check" for c in engine.received()),
        )

        matching = [c for c in engine.received() if c[0] == "loop.check"]
        assert matching, "event never arrived"
        assert all(c[2] is False for c in matching)

    def test_publish_reports_success_against_a_live_redis(self, redis_url):
        """Guards the publisher's own contract on the real client, not a mock."""
        assert _make_bus("publisher-solo").publish("wire.publish.check") is True


class TestOwnInstanceFilter:
    def test_a_bus_does_not_dispatch_its_own_message(self, engine, redis_url):
        """The originating instance already ran its local `notify_event`; dispatching
        its own broadcast would double-handle every event.

        **This test asserts an absence, so it needs a liveness control** or a broken
        wire would satisfy it trivially — which is exactly the vacuous-coverage failure
        this suite exists to end. So: the same subscriber must receive a *different*
        instance's event (proving the channel is live in this very setup) while never
        receiving its own.
        """
        bus = _make_bus("self-talker")
        bus.start_subscriber()
        try:
            other = _make_bus("someone-else")

            # Liveness control: publish from a DIFFERENT instance and require delivery.
            # If the wire is broken this fails, so the absence assertion below can only
            # pass when the channel is genuinely working.
            alive = _publish_until_observed(
                other,
                "wire.alive.check",
                lambda: any(c[0] == "wire.alive.check" for c in engine.received()),
            )
            assert alive, (
                "liveness control failed — the channel is not delivering, so the "
                "own-instance assertion below would be vacuous"
            )

            # Now the actual assertion: the bus's own broadcast must not come back.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                bus.publish("self.filter.check")
                time.sleep(_POLL_INTERVAL_SECS)

            own = [c for c in engine.received() if c[0] == "self.filter.check"]
            assert not own, "a bus dispatched its own broadcast back into notify_event"
        finally:
            bus.stop_subscriber()


class TestPreRehydrationBuffering:
    def test_events_arriving_before_rehydration_are_buffered_then_drained(
        self, monkeypatch, redis_url
    ):
        """Buffering is what stops an event being lost when it arrives during startup,
        before `_waiting` is populated. Exercised over the wire, since that is the only
        way the ordering can actually occur in production.
        """
        recorder = _RecordingEngine(rehydrated=False)
        monkeypatch.setattr(
            "AINDY.kernel.scheduler_engine.get_scheduler_engine", lambda: recorder
        )

        subscriber_bus = _make_bus(f"buffering-{uuid.uuid4().hex[:8]}")
        subscriber_bus.start_subscriber()
        try:
            publisher = _make_bus("publisher-a")
            buffered = _publish_until_observed(
                publisher,
                "early.event",
                lambda: bool(subscriber_bus._pre_rehydration_buffer),
            )
            assert buffered, "event was not buffered while rehydration was incomplete"
            assert recorder.received() == [], "dispatched before rehydration completed"

            recorder.mark_rehydrated()
            drained = subscriber_bus.drain_buffered_events()

            assert drained >= 1
            assert any(c[0] == "early.event" for c in recorder.received())
        finally:
            subscriber_bus.stop_subscriber()


class TestPublisherHealthOverTheWire:
    def test_a_healthy_publisher_is_not_suspended(self, redis_url):
        """EVENTBUS-PUBLISH-LATCH-1 regression guard against a real server: successful
        publishes must leave the circuit closed and the bus reporting healthy."""
        bus = _make_bus("health-check")
        for _ in range(5):
            assert bus.publish("wire.health.check") is True

        status = bus.get_status()
        assert status["publish_suspended"] is False
        assert status["publish_circuit_state"] == "closed"
        assert status["enabled"] is True
