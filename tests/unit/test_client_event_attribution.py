"""`/client/*` accepts reports from anyone, so a report cannot name the user it belongs to."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.runtime_only

_NAMED = "00000000-0000-0000-0000-00000000d001"
_CALLER = "00000000-0000-0000-0000-00000000d002"
_ROUTES = [
    ("/client/error", {"error_message": "boom"}),
    ("/client/vitals", {"lcp_ms": 1}),
]
_IDS = ["error", "vitals"]


@pytest.fixture
def queued(monkeypatch):
    seen: list[dict] = []
    import AINDY.routes.health_router  # noqa: F401  (the package attribute is the router)

    monkeypatch.setattr(
        sys.modules["AINDY.routes.health_router"],
        "queue_system_event",
        lambda **kwargs: seen.append(kwargs),
    )
    return seen


def _bearer(user_id: str) -> dict:
    from AINDY.services.auth_service import create_access_token

    token = create_access_token({"sub": user_id})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path,body", _ROUTES, ids=_IDS)
def test_an_anonymous_report_is_not_attributed_to_who_it_names(path, body, runtime_only_client, queued):
    response = runtime_only_client.post(path, json={**body, "user_id": _NAMED})

    assert response.status_code == 204
    assert len(queued) == 1, "liveness: the report must still be recorded"
    assert queued[0]["user_id"] is None


@pytest.mark.parametrize("path,body", _ROUTES, ids=_IDS)
def test_a_session_is_attributed_to_itself_not_to_who_it_names(path, body, runtime_only_client, queued):
    response = runtime_only_client.post(
        path, json={**body, "user_id": _NAMED}, headers=_bearer(_CALLER)
    )

    assert response.status_code == 204
    assert queued[0]["user_id"] == _CALLER


@pytest.mark.parametrize("path,body", _ROUTES, ids=_IDS)
def test_a_bad_token_is_still_accepted_as_anonymous(path, body, runtime_only_client, queued):
    """Errors during login and boot must still be captured, so a stale token is not a refusal."""
    response = runtime_only_client.post(
        path,
        json={**body, "user_id": _NAMED},
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 204
    assert queued[0]["user_id"] is None
