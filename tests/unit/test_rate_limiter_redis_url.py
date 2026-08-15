"""Rate limiter Redis resolution — REDIS_URL only.

EVENTBUS-REDIS-URL-CONSOLIDATION-1 (2026-06-06) removed the ``AINDY_REDIS_URL``
alias from ``event_bus.py``, ``config.py`` and ``.env.example``, and the CHANGELOG
recorded it as *"fully removed — all components now read REDIS_URL exclusively"*.

That was false by exactly one file: ``platform_layer/rate_limiter.py`` resolved
``REDIS_URL or AINDY_REDIS_URL`` and had done since the repo's first commit. It was
never in the consolidation's scope, so it also never got the ``DeprecationWarning``
that ``event_bus.py`` received in the preceding change.

The alias was dropped 2026-08-14. These tests exist so the last reader cannot come
back — the module reads its URL at *import* time, which is what let the omission go
unnoticed: nothing runtime-visible changes when the alias is honoured, so no test
that inspects the running limiter would have caught it.
"""
from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.runtime_only

MODULE = "AINDY.platform_layer.rate_limiter"


def _reload(monkeypatch, **env):
    """Re-import the module with a controlled environment.

    ``_redis_url`` is evaluated at module scope, so the value can only be observed
    by reloading — setting the env var afterwards has no effect.
    """
    for key in ("REDIS_URL", "AINDY_REDIS_URL", "TEST_MODE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(importlib.import_module(MODULE))


def test_redis_url_is_used(monkeypatch):
    mod = _reload(monkeypatch, REDIS_URL="redis://example:6379/3")
    assert mod._redis_url == "redis://example:6379/3"


def test_aindy_redis_url_alias_is_not_honoured(monkeypatch):
    """The alias must not resolve. This is the regression the file exists for."""
    mod = _reload(monkeypatch, AINDY_REDIS_URL="redis://legacy:6379/9")
    assert mod._redis_url is None, (
        "rate_limiter resolved AINDY_REDIS_URL. That alias was removed by "
        "EVENTBUS-REDIS-URL-CONSOLIDATION-1; honouring it here makes this module "
        "the only component reading a name the docs say does not exist."
    )


def test_canonical_wins_when_both_are_set(monkeypatch):
    mod = _reload(monkeypatch, REDIS_URL="redis://canonical:6379/0", AINDY_REDIS_URL="redis://legacy:6379/9")
    assert mod._redis_url == "redis://canonical:6379/0"


def test_neither_set_means_in_memory_storage(monkeypatch):
    """No Redis → slowapi falls back to in-process storage (per-instance limits)."""
    mod = _reload(monkeypatch)
    assert mod._redis_url is None


def test_empty_redis_url_is_treated_as_unset(monkeypatch):
    """Compose renders ``${VAR:-}`` as an empty string, not an absent variable.

    Same class as FR-10, which crash-looped a container on an empty typed bool.
    An empty string here must not be handed to the limiter as a storage URI.
    """
    mod = _reload(monkeypatch, REDIS_URL="")
    assert not mod._redis_url
    assert mod.limiter is not None


@pytest.fixture(autouse=True)
def _restore_module():
    """Leave the module as the rest of the suite expects to find it."""
    yield
    importlib.reload(importlib.import_module(MODULE))
