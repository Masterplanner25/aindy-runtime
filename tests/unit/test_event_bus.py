"""Behavioural suite for the distributed event bus.

Closes the event-bus half of DOCS-COVERAGE-CLAIM-1: `OS_ISOLATION_LAYER.md` cited
`tests/unit/test_event_bus.py` — this path — which had never existed.

The bus exists so a flow that entered WAIT on instance A can be resumed by an event
received on instance B. Everything here runs without Redis: the publisher is driven
through a fake client and the subscriber through `_handle_message` directly, which is
the same entry point `_subscriber_loop` uses per message.

The headline finding is `TestPublisherFailureLatch` — three consecutive publish
failures disable the publisher **permanently** for the process, with no recovery when
Redis returns.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from AINDY.kernel import event_bus as bus_module
from AINDY.kernel.event_bus import (
    _MAX_BUFFER_SIZE,
    EventBus,
    get_event_bus,
    resolve_event_bus_redis_url,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def bus():
    """A fresh, enabled EventBus with no real Redis behind it."""
    instance = EventBus()
    instance._enabled = True
    instance._pub_client = None
    instance._consecutive_failures = 0
    return instance


@pytest.fixture
def fake_redis(bus):
    client = MagicMock()
    bus._get_pub_client = lambda: client
    return client


def _engine(rehydrated=True):
    engine = MagicMock()
    engine.is_rehydrated.return_value = rehydrated
    return engine


def _patch_engine(engine):
    return patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine)


# ── configuration ─────────────────────────────────────────────────────────────


class TestConfiguration:
    def test_redis_url_falls_back_when_unset(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert resolve_event_bus_redis_url() == "redis://localhost:6379/0"

    def test_empty_redis_url_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "")
        assert resolve_event_bus_redis_url() == "redis://localhost:6379/0"

    def test_explicit_redis_url_wins(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://elsewhere:6380/2")
        assert resolve_event_bus_redis_url() == "redis://elsewhere:6380/2"

    def test_module_level_config_is_read_once_at_import(self, monkeypatch):
        """`REDIS_URL`, `CHANNEL` and `ENABLED` are module-level, so changing the
        environment after import has no effect. Same shape as the import-time env
        reads CLAUDE.md flags — a behavioural test cannot see it, only this one can.
        """
        before = bus_module.CHANNEL
        monkeypatch.setenv("AINDY_EVENT_BUS_CHANNEL", "totally:different")
        assert bus_module.CHANNEL == before

    def test_a_new_bus_snapshots_enabled_at_construction(self, monkeypatch):
        monkeypatch.setenv("AINDY_EVENT_BUS_ENABLED", "false")
        assert EventBus()._enabled is bus_module.ENABLED


# ── publisher ─────────────────────────────────────────────────────────────────


class TestPublish:
    def test_disabled_bus_publishes_nothing(self, bus):
        bus._enabled = False
        called = MagicMock()
        bus._get_pub_client = called
        assert bus.publish("evt") is False
        called.assert_not_called()

    def test_successful_publish_returns_true(self, bus, fake_redis):
        assert bus.publish("operation.completed") is True
        fake_redis.publish.assert_called_once()

    def test_payload_carries_event_correlation_and_source(self, bus, fake_redis):
        bus.publish("operation.completed", correlation_id="chain-abc")
        channel, raw = fake_redis.publish.call_args[0]
        assert channel == bus_module.CHANNEL
        assert json.loads(raw) == {
            "event_type": "operation.completed",
            "correlation_id": "chain-abc",
            "source_instance_id": bus._instance_id,
        }

    def test_correlation_id_is_optional(self, bus, fake_redis):
        bus.publish("evt")
        assert json.loads(fake_redis.publish.call_args[0][1])["correlation_id"] is None

    def test_failure_returns_false_and_never_raises(self, bus):
        client = MagicMock()
        client.publish.side_effect = ConnectionError("redis down")
        bus._get_pub_client = lambda: client
        assert bus.publish("evt") is False

    def test_failure_resets_the_client_so_the_next_call_reconnects(self, bus):
        client = MagicMock()
        client.publish.side_effect = ConnectionError("redis down")
        bus._pub_client = client
        bus._get_pub_client = lambda: client
        bus.publish("evt")
        assert bus._pub_client is None

    def test_success_clears_the_failure_counter(self, bus, fake_redis):
        bus._consecutive_failures = 2
        bus.publish("evt")
        assert bus._consecutive_failures == 0


class TestPublisherFailureLatch:
    """Three consecutive failures set `_enabled = False` — and nothing ever sets it
    back. The module docstring's "reconnects with exponential back-off" describes the
    *subscriber* loop; the publisher has no recovery path at all.

    In a multi-instance deployment a transient Redis blip therefore ends cross-instance
    WAIT/RESUME propagation for the life of the process: flows waiting on other
    instances are never resumed, which is precisely what this module exists to prevent.
    """

    @staticmethod
    def _breaking(bus):
        client = MagicMock()
        client.publish.side_effect = ConnectionError("redis down")
        bus._get_pub_client = lambda: client
        return client

    def test_bus_survives_two_failures(self, bus):
        self._breaking(bus)
        bus.publish("evt")
        bus.publish("evt")
        assert bus._enabled is True
        assert bus._consecutive_failures == 2

    def test_third_consecutive_failure_disables_the_bus(self, bus):
        self._breaking(bus)
        for _ in range(3):
            bus.publish("evt")
        assert bus._enabled is False

    def test_recovery_of_redis_does_not_re_enable_the_publisher(self, bus):
        self._breaking(bus)
        for _ in range(3):
            bus.publish("evt")

        healthy = MagicMock()
        bus._get_pub_client = lambda: healthy

        assert bus.publish("evt") is False
        assert healthy.publish.called is False, (
            "the latch short-circuits before touching Redis — a recovered server is "
            "never retried"
        )

    def test_interleaved_success_prevents_the_latch(self, bus):
        """The counter is *consecutive*, so an intermittent failure never latches."""
        broken = MagicMock()
        broken.publish.side_effect = ConnectionError("down")
        healthy = MagicMock()

        for attempt in range(6):
            bus._get_pub_client = (lambda: broken) if attempt % 2 == 0 else (lambda: healthy)
            bus.publish("evt")

        assert bus._enabled is True


# ── subscriber message handling ───────────────────────────────────────────────


class TestHandleMessage:
    def test_malformed_json_is_ignored(self, bus):
        engine = _engine()
        with _patch_engine(engine):
            bus._handle_message("{not json")
        engine.notify_event.assert_not_called()

    def test_own_instance_messages_are_skipped(self, bus):
        """The originating instance already ran its local notify_event; re-dispatching
        would loop."""
        engine = _engine()
        payload = json.dumps(
            {"event_type": "evt", "correlation_id": None,
             "source_instance_id": bus._instance_id}
        )
        with _patch_engine(engine):
            bus._handle_message(payload)
        engine.notify_event.assert_not_called()

    def test_remote_messages_are_dispatched_locally(self, bus):
        engine = _engine()
        payload = json.dumps(
            {"event_type": "operation.completed", "correlation_id": "chain-1",
             "source_instance_id": "some-other-instance"}
        )
        with _patch_engine(engine):
            bus._handle_message(payload)
        engine.notify_event.assert_called_once_with(
            "operation.completed", correlation_id="chain-1", broadcast=False
        )

    def test_dispatch_always_suppresses_rebroadcast(self, bus):
        """`broadcast=False` is what stops an event ping-ponging between instances."""
        engine = _engine()
        with _patch_engine(engine):
            bus._handle_message(json.dumps(
                {"event_type": "evt", "source_instance_id": "other"}
            ))
        assert engine.notify_event.call_args.kwargs["broadcast"] is False

    @pytest.mark.parametrize(
        "payload",
        [
            {"source_instance_id": "other"},
            {"event_type": "", "source_instance_id": "other"},
            {"event_type": None, "source_instance_id": "other"},
            {"event_type": 42, "source_instance_id": "other"},
        ],
    )
    def test_missing_or_non_string_event_type_is_ignored(self, bus, payload):
        engine = _engine()
        with _patch_engine(engine):
            bus._handle_message(json.dumps(payload))
        engine.notify_event.assert_not_called()

    def test_empty_correlation_id_is_normalised_to_none(self, bus):
        engine = _engine()
        with _patch_engine(engine):
            bus._handle_message(json.dumps(
                {"event_type": "evt", "correlation_id": "", "source_instance_id": "other"}
            ))
        assert engine.notify_event.call_args.kwargs["correlation_id"] is None

    def test_a_failing_local_notify_is_non_fatal(self, bus):
        engine = _engine()
        engine.notify_event.side_effect = RuntimeError("scheduler exploded")
        with _patch_engine(engine):
            bus._handle_message(json.dumps(
                {"event_type": "evt", "source_instance_id": "other"}
            ))  # must not raise


# ── pre-rehydration buffering ─────────────────────────────────────────────────


class TestPreRehydrationBuffer:
    def _remote(self, event_type="evt", correlation_id=None):
        return json.dumps({
            "event_type": event_type,
            "correlation_id": correlation_id,
            "source_instance_id": "other-instance",
        })

    def test_events_are_buffered_until_rehydration_completes(self, bus):
        engine = _engine(rehydrated=False)
        with _patch_engine(engine):
            bus._handle_message(self._remote("evt-1"))
        engine.notify_event.assert_not_called()
        assert bus._pre_rehydration_buffer == [("evt-1", None)]

    def test_buffering_preserves_the_correlation_id(self, bus):
        engine = _engine(rehydrated=False)
        with _patch_engine(engine):
            bus._handle_message(self._remote("evt-1", "chain-9"))
        assert bus._pre_rehydration_buffer == [("evt-1", "chain-9")]

    def test_buffer_is_capped_and_drops_the_overflow(self, bus):
        engine = _engine(rehydrated=False)
        bus._pre_rehydration_buffer = [("old", None)] * _MAX_BUFFER_SIZE
        with _patch_engine(engine):
            bus._handle_message(self._remote("overflow"))
        assert len(bus._pre_rehydration_buffer) == _MAX_BUFFER_SIZE
        assert ("overflow", None) not in bus._pre_rehydration_buffer

    def test_own_instance_messages_are_never_buffered(self, bus):
        engine = _engine(rehydrated=False)
        payload = json.dumps({"event_type": "evt", "source_instance_id": bus._instance_id})
        with _patch_engine(engine):
            bus._handle_message(payload)
        assert bus._pre_rehydration_buffer == []


class TestDrainBufferedEvents:
    def test_empty_buffer_drains_zero(self, bus):
        assert bus.drain_buffered_events() == 0

    def test_buffered_events_are_dispatched_in_order(self, bus):
        bus._pre_rehydration_buffer = [("evt-1", "c1"), ("evt-2", None)]
        engine = _engine()
        with _patch_engine(engine):
            assert bus.drain_buffered_events() == 2
        assert [call.args[0] for call in engine.notify_event.call_args_list] == [
            "evt-1", "evt-2"
        ]

    def test_drained_events_suppress_rebroadcast(self, bus):
        bus._pre_rehydration_buffer = [("evt-1", None)]
        engine = _engine()
        with _patch_engine(engine):
            bus.drain_buffered_events()
        assert engine.notify_event.call_args.kwargs["broadcast"] is False

    def test_draining_empties_the_buffer(self, bus):
        bus._pre_rehydration_buffer = [("evt-1", None)]
        with _patch_engine(_engine()):
            bus.drain_buffered_events()
        assert bus._pre_rehydration_buffer == []

    def test_second_drain_is_a_no_op(self, bus):
        bus._pre_rehydration_buffer = [("evt-1", None)]
        with _patch_engine(_engine()):
            assert bus.drain_buffered_events() == 1
            assert bus.drain_buffered_events() == 0

    def test_one_failing_event_does_not_abort_the_drain(self, bus):
        bus._pre_rehydration_buffer = [("bad", None), ("good", None)]
        engine = _engine()
        engine.notify_event.side_effect = [RuntimeError("boom"), None]
        with _patch_engine(engine):
            assert bus.drain_buffered_events() == 1
        assert engine.notify_event.call_count == 2


# ── instance identity + singleton ─────────────────────────────────────────────


class TestInstanceIdentity:
    def test_instance_id_is_non_empty(self, bus):
        assert isinstance(bus._instance_id, str) and bus._instance_id

    def test_instance_id_is_stable_for_one_bus(self, bus):
        assert bus._instance_id == bus._instance_id

    def test_two_buses_in_one_process_share_an_identity(self):
        """Both derive from the same host/pid, so the own-instance filter holds
        across bus objects within a process."""
        assert EventBus()._instance_id == EventBus()._instance_id


class TestSingleton:
    def test_get_event_bus_returns_the_same_object(self):
        assert get_event_bus() is get_event_bus()

    def test_direct_construction_is_independent_of_the_singleton(self):
        assert EventBus() is not get_event_bus()


class TestSubscriberLifecycle:
    def test_no_subscriber_thread_before_start(self, bus):
        assert bus._is_subscriber_running() is False

    def test_stop_is_safe_without_a_running_subscriber(self, bus):
        bus.stop_subscriber()
        bus.stop(timeout=0.01)

    def test_status_reports_a_dict_with_the_documented_keys(self, bus):
        status = bus.get_status()
        assert isinstance(status, dict)
        assert "enabled" in status
