"""
Tests for resolve_event_bus_redis_url() and get_redis_client() precedence logic.

Covers the fix for the silent-misconfiguration defect where event_bus.py ignored
REDIS_URL and always fell back to redis://localhost:6379/0 when AINDY_REDIS_URL
was unset.  See CHANGELOG 2026-05-27 and EVENTBUS-REDIS-URL-CONSOLIDATION-1.
"""
import pytest

# ---------------------------------------------------------------------------
# resolve_event_bus_redis_url — four-case precedence table
# ---------------------------------------------------------------------------

def test_aindy_redis_url_wins_when_both_set(monkeypatch):
    """AINDY_REDIS_URL takes precedence; operator contract for split-Redis topologies."""
    monkeypatch.setenv("AINDY_REDIS_URL", "redis://aindy-host:6379/0")
    monkeypatch.setenv("REDIS_URL", "redis://standard-host:6379/0")

    from importlib import reload
    import AINDY.kernel.event_bus as eb
    assert eb.resolve_event_bus_redis_url() == "redis://aindy-host:6379/0"


def test_redis_url_used_when_aindy_redis_url_unset(monkeypatch):
    """Operators who only set REDIS_URL get the correct URL, not localhost."""
    monkeypatch.delenv("AINDY_REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://standard-host:6379/0")

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://standard-host:6379/0"


def test_localhost_default_when_both_unset(monkeypatch):
    """Falls back to localhost default when neither variable is set."""
    monkeypatch.delenv("AINDY_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://localhost:6379/0"


def test_empty_aindy_redis_url_falls_through_to_redis_url(monkeypatch):
    """
    Empty AINDY_REDIS_URL is treated as unset and falls through.

    ``export AINDY_REDIS_URL=`` should clear the override and let REDIS_URL
    take over — it must not forward the empty string to the Redis client.
    """
    monkeypatch.setenv("AINDY_REDIS_URL", "")
    monkeypatch.setenv("REDIS_URL", "redis://fallback:6379/0")

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://fallback:6379/0"


def test_empty_redis_url_falls_through_to_default(monkeypatch):
    """Empty REDIS_URL also falls through to the localhost default."""
    monkeypatch.delenv("AINDY_REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "")

    from AINDY.kernel.event_bus import resolve_event_bus_redis_url
    assert resolve_event_bus_redis_url() == "redis://localhost:6379/0"


# ---------------------------------------------------------------------------
# get_redis_client — does NOT fall through to the localhost default
# ---------------------------------------------------------------------------

def test_get_redis_client_returns_none_when_neither_set(monkeypatch):
    """
    No auxiliary client when Redis is not explicitly configured.

    The auxiliary client must not use the localhost default — connecting
    somewhere an operator did not ask for would be surprising.
    """
    monkeypatch.delenv("AINDY_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    # Patch the redis import so the test does not require a live Redis.
    monkeypatch.setattr("AINDY.kernel.event_bus.ENABLED", True)

    from AINDY.kernel.event_bus import get_redis_client
    assert get_redis_client() is None


def test_get_redis_client_returns_none_when_bus_disabled(monkeypatch):
    """No auxiliary client when the event bus is disabled regardless of URL."""
    monkeypatch.setenv("REDIS_URL", "redis://some-host:6379/0")
    monkeypatch.setattr("AINDY.kernel.event_bus.ENABLED", False)

    from AINDY.kernel.event_bus import get_redis_client
    assert get_redis_client() is None
