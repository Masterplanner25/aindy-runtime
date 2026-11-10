"""`GET /watcher/signals` filters by the same signal types `POST /watcher/signals` accepts (FR-65).

The query service kept its own list, which had drifted from the ingest vocabulary: it allowed
`app_focus`, `app_switch` and `idle_detected`, which the ingest route refuses, and answered 422 for
`focus_achieved`, `context_switch` and `heartbeat`, which it stores. So the app could not filter
by the signal its focus score reads.

The syscall behind the query, `sys.v1.watcher.query`, is the app's; it is recorded here, not run.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

_SHARED = {"X-API-Key": "test-api-key-for-pytest-only"}


def _ingest_vocabulary() -> list[str]:
    from AINDY.watcher.constants import VALID_SIGNAL_TYPES

    types = sorted(VALID_SIGNAL_TYPES)
    assert types, "the vocabulary is empty: a census over it would pass by saying nothing"
    return types


@pytest.fixture
def dispatched(monkeypatch):
    """Record what reached `sys.v1.watcher.query`. Only the dispatcher is replaced; the route,
    its auth and the service's validation in front of it stay real."""
    seen: list[dict] = []

    class _Recorder:
        def dispatch(self, name, payload, ctx):
            seen.append({"name": name, **payload})
            return {"status": "success", "data": {"signals": [], "total": 0}}

    monkeypatch.setattr("AINDY.kernel.syscall_dispatcher.get_dispatcher", lambda: _Recorder())
    return seen


@pytest.mark.parametrize("signal_type", _ingest_vocabulary())
def test_every_type_the_ingest_route_stores_can_be_filtered(signal_type, runtime_only_client, dispatched):
    response = runtime_only_client.get(
        "/watcher/signals", params={"signal_type": signal_type}, headers=_SHARED
    )

    assert response.status_code == 200, response.text
    assert [(d["name"], d["signal_type"]) for d in dispatched] == [("sys.v1.watcher.query", signal_type)]


@pytest.mark.parametrize("signal_type", ["app_focus", "app_switch", "idle_detected", "nonsense"])
def test_control_a_type_the_ingest_route_refuses_is_still_422(signal_type, runtime_only_client, dispatched):
    response = runtime_only_client.get(
        "/watcher/signals", params={"signal_type": signal_type}, headers=_SHARED
    )

    assert response.status_code == 422, response.text
    assert dispatched == []


def test_the_ingest_route_and_the_query_read_one_list():
    from AINDY.platform_layer import watcher_service
    from AINDY.watcher.constants import VALID_SIGNAL_TYPES

    assert watcher_service._VALID_SIGNAL_TYPES is VALID_SIGNAL_TYPES
