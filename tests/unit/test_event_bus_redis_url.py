"""
Tests for resolve_event_bus_redis_url() and get_redis_client() precedence logic.

AINDY_REDIS_URL was removed in the 2.0 cleanup (EVENTBUS-REDIS-URL-CONSOLIDATION-1).
The canonical variable is REDIS_URL. Tests below verify the REDIS_URL-only path.
"""
import pytest

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# resolve_event_bus_redis_url — REDIS_URL precedence
# ---------------------------------------------------------------------------

def test_redis_url_is_used_for_event_bus(monkeypatch):
    """REDIS_URL is the canonical Redis URL for the event bus."""
    monkeypatch.setenv("REDIS_URL", "redis://standard-host:6379/0")

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://standard-host:6379/0"


def test_localhost_default_when_redis_url_unset(monkeypatch):
    """Falls back to localhost default when REDIS_URL is not set."""
    monkeypatch.delenv("REDIS_URL", raising=False)

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://localhost:6379/0"


def test_empty_redis_url_falls_through_to_default(monkeypatch):
    """Empty REDIS_URL is treated as unset; falls through to the localhost default."""
    monkeypatch.setenv("REDIS_URL", "")

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://localhost:6379/0"


# ---------------------------------------------------------------------------
# get_redis_client — explicit-configuration-only behaviour
# ---------------------------------------------------------------------------

def test_get_redis_client_returns_none_when_redis_url_unset(monkeypatch):
    """
    No auxiliary client when Redis is not explicitly configured.

    The auxiliary client must not use the localhost default — connecting
    somewhere an operator did not ask for would be surprising.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr("AINDY.kernel.event_bus.ENABLED", True)

    from AINDY.kernel.event_bus import get_redis_client
    assert get_redis_client() is None


def test_get_redis_client_returns_none_when_bus_disabled(monkeypatch):
    """No auxiliary client when the event bus is disabled regardless of URL."""
    monkeypatch.setenv("REDIS_URL", "redis://some-host:6379/0")
    monkeypatch.setattr("AINDY.kernel.event_bus.ENABLED", False)

    from AINDY.kernel.event_bus import get_redis_client
    assert get_redis_client() is None
