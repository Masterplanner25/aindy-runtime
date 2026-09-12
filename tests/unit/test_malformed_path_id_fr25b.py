"""FR-25 (b) — a malformed id in a path is the caller's mistake, and a 500 says otherwise.

`GET /coordination/runs/not-a-uuid/children` answered **500** with the parser's own message
(``badly formed hexadecimal UUID string``): the parameter was declared ``str``, so FastAPI
validated nothing, and the handler's ``normalize_uuid()`` raised. The app team confirmed that
one route and offered the ``_id: str`` population "as a bound, not a claim".

★ The bound was measured, not audited (2026-09-11): every parameterised runtime-served route
was called with ``not-a-uuid`` through the booted app — on SQLite, and again on live Postgres
with a fresh app per route (the harness binds every session to one transaction, so one
``DataError`` poisons the rest; and a random ``sub`` with no ``users`` row trips FK checks that
SQLite does not enforce — two ways the first Postgres runs lied). **Six routes answered 500 on
both engines; every other one answered 4xx.** The Postgres-only class (a raw string against a
UUID column) turned out to be empty: the memory-node paths normalise before they query.

What these assert:

* the census is DERIVED from `route_inventory.json` — the published list of what the runtime
  serves — and asserted non-empty, so a new route with a ``str`` id that 500s is caught, not a
  hand-typed list of six (variant 12);
* a route test calls the route (`ROUTE-GUARD-1`): the whole stack answers, not the annotation;
* the fix cannot reject good input — a well-formed, unknown id still reaches the handler and
  gets the handler's own 404, never a 422.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only

_REPO = Path(__file__).resolve().parents[2]
_PLACEHOLDER = re.compile(r"{\w+}")

# The six the probe found. This is NOT the census (that is derived below); it is the set whose
# behaviour changed and therefore needs the positive control. A seventh 500 found by the census
# test should be fixed with `UUIDPath` and added here, so it also gets the good-input check.
_FIXED = [
    ("POST", "/apps/coordination/agents/{agent_id}/heartbeat"),
    ("DELETE", "/apps/coordination/agents/{agent_id}"),
    ("GET", "/apps/coordination/runs/{parent_run_id}/children"),
    ("GET", "/platform/keys/{key_id}"),
    ("DELETE", "/platform/keys/{key_id}"),
    ("POST", "/platform/admin/users/{user_id}/promote"),
]


def _served_parameterised_routes() -> list[tuple[str, str]]:
    inventory = json.loads((_REPO / "AINDY" / "route_inventory.json").read_text(encoding="utf-8"))
    routes = inventory["routes"] if isinstance(inventory, dict) else inventory
    found = set()
    for route in routes:
        path = route["path"]
        if "{" not in path:
            continue
        methods = route.get("methods") or [route.get("method")]
        for method in methods:
            if method and method.upper() not in {"HEAD", "OPTIONS"}:
                found.add((method.upper(), path))
    return sorted(found)


def _admin_session() -> dict:
    from AINDY.auth.api_key_auth import derive_session_scopes

    uid = str(uuid.uuid4())
    return {
        "sub": uid,
        "user_id": uid,
        "auth_type": "jwt",
        "is_admin": True,
        "session_scopes": derive_session_scopes(is_admin=True),
    }


def _body_for(method: str, path: str):
    """The smallest body that gets past body validation, so the ID is what is being tested."""
    if method not in {"POST", "PUT", "PATCH"}:
        return None
    if path.endswith("/acknowledge"):
        return {"agent_id": str(uuid.uuid4())}
    if path.endswith("/feedback"):
        return {"outcome": "success"}
    if path.endswith("/resume"):
        return {"event_type": "probe", "payload": {}}
    return {}


@pytest.fixture
def admin_client(runtime_only_app):
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        yield client


def test_no_served_route_answers_500_to_a_malformed_path_id(admin_client):
    routes = _served_parameterised_routes()
    assert len(routes) >= 30, f"census liveness: expected the inventory's parameterised routes, got {len(routes)}"
    assert set(_FIXED) <= set(routes), "the fixed set must be a subset of what the runtime serves"

    offenders = []
    for method, path in routes:
        response = admin_client.request(
            method, _PLACEHOLDER.sub("not-a-uuid", path), json=_body_for(method, path)
        )
        if response.status_code >= 500:
            offenders.append(f"{response.status_code} {method} {path}: {response.text[:100]}")

    assert not offenders, (
        "a malformed path id must be a 4xx — the caller's mistake, not the server's. "
        "Fix with `AINDY.routes.path_params.UUIDPath` (and add the route to _FIXED so it gets "
        "the good-input control):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("method,path", _FIXED, ids=[f"{m} {p}" for m, p in _FIXED])
def test_a_malformed_id_is_a_422_with_a_structured_error(admin_client, method, path):
    response = admin_client.request(
        method, _PLACEHOLDER.sub("not-a-uuid", path), json=_body_for(method, path)
    )
    assert response.status_code == 422, response.text
    body = response.json()
    detail = body.get("details") or body.get("detail")
    assert detail and "UUID" in json.dumps(detail), body


@pytest.mark.parametrize("method,path", _FIXED, ids=[f"{m} {p}" for m, p in _FIXED])
def test_a_well_formed_unknown_id_still_reaches_the_handler(admin_client, method, path):
    """The half that mattered on the app's side: a stricter boundary could start rejecting
    good input. A canonical UUID that matches nothing must get the HANDLER's answer (404), not
    the validator's (422) and not a 500."""
    response = admin_client.request(
        method, _PLACEHOLDER.sub(str(uuid.uuid4()), path), json=_body_for(method, path)
    )
    assert response.status_code == 404, response.text


def test_the_compact_32_hex_form_is_still_accepted():
    """`uuid.UUID` accepts the dash-less form and so did every handler; the boundary must too,
    or an id copied from a log without dashes starts failing where it used to work."""
    from AINDY.routes.path_params import _must_parse_as_uuid

    compact = uuid.uuid4().hex
    assert _must_parse_as_uuid(compact) == compact
    with pytest.raises(ValueError):
        _must_parse_as_uuid("not-a-uuid")
