"""KEY-SCOPE-ESCALATION-1 — an API key could mint itself a wider API key.

Starting from a key holding the single scope `flow.read`, this chain ran end to end against
real PostgreSQL:

1. `POST /platform/keys {"scopes": ["platform.admin","memory.delete","event.emit"]}` → **201**
2. `GET /platform/admin/users` with the new key → **200**, every user's email and admin flag
3. `POST /platform/admin/users/{own_id}/promote` → **200**, `is_admin: true`

Step 3 lands in the **user row**, so revoking the minted key does not undo it.

The only validation was membership in `Scopes.ALL` — *"is this a real scope"*, never *"may you
grant it"*. And nothing upstream would have stopped it: `require_platform_admin_access` admits
**any** authenticated API key to the whole `/platform` tree, on the stated assumption that
*"scope enforcement happens per-endpoint or per-syscall"* — which `keys_router` does not do.

★ **Why SQLite could not have found this.** `platform_api_keys.scopes` is a PostgreSQL `ARRAY`;
on SQLite the ORM insert dies at the driver with `type 'list' is not supported` — **after** the
authorization gate has been passed. The harness turns a 201 into a 500 and the finding reads as
an unrelated bug. So the tests below assert **refusals** (which happen before any write) and
`grantable_scopes` directly; the allow-path is asserted as *not refused*, which is all this
harness can honestly say.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

_USER = "00000000-0000-0000-0000-0000000000fe"


def _scopes():
    from AINDY.auth.api_key_auth import Scopes

    return Scopes


def _grantable(principal: dict) -> set[str]:
    from AINDY.auth.api_key_auth import grantable_scopes

    return grantable_scopes(principal)


def _key(*scopes: str) -> dict:
    return {
        "sub": _USER,
        "user_id": _USER,
        "auth_type": "api_key",
        "api_key_id": "k1",
        "api_key_scopes": list(scopes),
    }


def _session(*, is_admin: bool) -> dict:
    from AINDY.auth.api_key_auth import derive_session_scopes

    return {
        "sub": _USER,
        "user_id": _USER,
        "auth_type": "jwt",
        "is_admin": is_admin,
        "session_scopes": derive_session_scopes(is_admin=is_admin),
    }


# --------------------------------------------------------------------------------------
# The rule: you cannot grant what you do not hold
# --------------------------------------------------------------------------------------


def test_a_key_cannot_grant_a_scope_it_does_not_hold():
    """★ The escalation, as a property. `flow.read` must not reach `platform.admin`."""
    S = _scopes()

    grantable = _grantable(_key(S.FLOW_READ))

    assert grantable == {S.FLOW_READ}
    for forbidden in (S.PLATFORM_ADMIN, S.MEMORY_DELETE, S.EVENT_EMIT, S.MEMORY_WRITE):
        assert forbidden not in grantable, f"a flow.read key could still grant {forbidden}"


def test_a_key_can_grant_what_it_holds():
    """Liveness — the rule is delegation, not prohibition.

    Without this, "grants nothing" would satisfy the test above.
    """
    S = _scopes()

    assert _grantable(_key(S.MEMORY_READ, S.MEMORY_WRITE)) == {S.MEMORY_READ, S.MEMORY_WRITE}


def test_a_platform_admin_holder_may_grant_anything():
    """Deliberate, and not a loophole.

    `platform.admin` already satisfies every scope gate and reaches
    `POST /platform/admin/users/{id}/promote`, so refusing it `memory.delete` on a key would be
    theatre. It also preserves the documented affordance that a key *can* carry
    `memory.delete`/`event.emit`, which no session inherits.
    """
    S = _scopes()

    assert _grantable(_key(S.PLATFORM_ADMIN)) == set(S.ALL)


def test_an_ordinary_session_cannot_grant_what_it_does_not_derive():
    """The two scopes a session is deliberately denied must not be mintable by one either.

    Otherwise `JWT_SESSION` excluding `memory.delete` and `event.emit` would be a formality —
    a user could hold them by way of a key they issued themselves.
    """
    S = _scopes()

    grantable = _grantable(_session(is_admin=False))

    assert S.MEMORY_DELETE not in grantable
    assert S.EVENT_EMIT not in grantable
    assert S.PLATFORM_ADMIN not in grantable
    assert S.MEMORY_READ in grantable, "an ordinary user must still be able to issue a usable key"


def test_an_admin_session_may_grant_anything():
    S = _scopes()

    assert _grantable(_session(is_admin=True)) == set(S.ALL)


def test_an_unrecognised_principal_grants_nothing():
    """Fail closed. An unexpected principal shape must not default to wide authority."""
    assert _grantable({"sub": _USER}) <= set(_scopes().JWT_SESSION)


# --------------------------------------------------------------------------------------
# The route — a rule that only exists in a helper is not enforcement
# --------------------------------------------------------------------------------------


@pytest.fixture
def as_principal(runtime_only_app):
    from AINDY.services.auth_service import get_current_user

    def _apply(principal: dict):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: principal

    yield _apply
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


def _mint(client, scopes):
    return client.post("/platform/keys", json={"name": "probe", "scopes": scopes})


def test_the_route_refuses_the_escalation(runtime_only_client, as_principal, mock_db):
    """★ The reproduction, at the seam that shipped it.

    Refusal happens before any write, so this is exact on SQLite even though the *success*
    path is not.
    """
    S = _scopes()
    as_principal(_key(S.FLOW_READ))

    response = _mint(runtime_only_client, [S.PLATFORM_ADMIN, S.MEMORY_DELETE, S.EVENT_EMIT])

    assert response.status_code == 403, (
        f"a flow.read key minted {S.PLATFORM_ADMIN} — status {response.status_code}: "
        f"{response.text[:300]}"
    )
    assert "scope_not_grantable" in response.text


def test_a_partial_escalation_is_refused_whole(runtime_only_client, as_principal, mock_db):
    """Mixing one held scope with one it lacks must not let the request through.

    A subset check written as "any" rather than "all" would pass this request and issue the key
    with both scopes.
    """
    S = _scopes()
    as_principal(_key(S.FLOW_READ))

    response = _mint(runtime_only_client, [S.FLOW_READ, S.PLATFORM_ADMIN])

    assert response.status_code == 403, response.text[:300]

    # The pipeline's exception handler reshapes HTTPException detail into a flat
    # `{"error", "message", "details"}` envelope, so read the body as text rather than
    # assuming FastAPI's default `{"detail": ...}`.
    body = response.text
    refused = body.split("may not grant ")[1].split("]")[0] + "]"

    assert refused == f"['{S.PLATFORM_ADMIN}']", (
        f"the refusal should name only the scopes that were not grantable, got {refused!r} — "
        f"listing a scope the caller does hold sends them looking for the wrong permission"
    )


def test_the_route_still_issues_a_key_the_caller_may_grant(
    runtime_only_client, as_principal, mock_db
):
    """Liveness control at the route.

    Asserted as *not 403* rather than 201: on SQLite the write itself fails on the ARRAY column
    (see the module docstring), so 201 is not available to this harness and claiming it would be
    asserting the harness rather than the guard.
    """
    S = _scopes()
    as_principal(_key(S.MEMORY_READ, S.MEMORY_WRITE))

    response = _mint(runtime_only_client, [S.MEMORY_READ])

    assert response.status_code != 403, (
        f"a key was refused a scope it holds — the rule is too tight: {response.text[:300]}"
    )


def test_an_unknown_scope_is_still_a_422_not_a_403(runtime_only_client, as_principal, mock_db):
    """The pre-existing validation must keep its own answer.

    "That is not a scope" and "you may not grant that scope" are different failures, and
    collapsing them would tell a caller to go find a permission that does not exist.
    """
    S = _scopes()
    as_principal(_key(S.PLATFORM_ADMIN))

    response = _mint(runtime_only_client, ["not.a.real.scope"])

    assert response.status_code == 422, response.text[:300]
