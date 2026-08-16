"""HTTP-SCOPE-GAP-1 — a JWT session must not be more privileged than an API key.

`enforce_api_key_scope` gated API-key callers only. Its own docstring said *"JWT users carry
full trust and are never gated by this check"*, so an interactive browser session was
**strictly more privileged than any API key**. A session now carries `session_scopes` derived
from `User.is_admin`.

**Why this ships enforcing by default**, where most boundary tightening in this repo ships
default-off: the blast radius is *countable*, not hoped-for. Only **7 of 147** route decorators
enforce a scope at all, and the only three scopes any of them require — `flow.read`,
`flow.execute`, `memory.read` — are in the ordinary session set. So every signed-in user still
passes every currently-enforcing route.

`test_every_enforced_scope_is_held_by_an_ordinary_session` is that argument as an executable
assertion. If someone later adds an `enforce_api_key_scope(Scopes.MEMORY_DELETE)` to a route
the SPA calls, it fails **here** rather than as a 403 in someone's browser.

Design constraints taken from the app team's own answer, not invented:
  * admin scopes key on the **existing user-row flag** — one source of truth for "operator";
  * `memory.delete` and `event.emit` are in neither derived set — nothing in their client uses
    them, and a session must not inherit what it never needs.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


def _scopes():
    from AINDY.auth.api_key_auth import Scopes

    return Scopes


def _derive(is_admin: bool):
    from AINDY.auth.api_key_auth import derive_session_scopes

    return derive_session_scopes(is_admin=is_admin)


def _check(scope, user: dict):
    """Run the real dependency body against a resolved user dict."""
    from AINDY.services.auth_service import enforce_api_key_scope

    return enforce_api_key_scope(scope)(current_user=user)


def _session(*, is_admin: bool) -> dict:
    return {
        "auth_type": "jwt",
        "user_id": "u-1",
        "is_admin": is_admin,
        "session_scopes": _derive(is_admin),
    }


# --------------------------------------------------------------------------------------
# The derived sets
# --------------------------------------------------------------------------------------


def test_ordinary_session_gets_the_app_teams_stated_surface():
    S = _scopes()

    assert sorted(_derive(False)) == sorted(
        [S.FLOW_READ, S.FLOW_EXECUTE, S.MEMORY_READ, S.MEMORY_WRITE, S.AGENT_RUN, S.EXECUTION_READ]
    )


def test_admin_session_adds_exactly_the_operator_scopes():
    S = _scopes()
    ordinary, admin = set(_derive(False)), set(_derive(True))

    assert admin - ordinary == {S.WEBHOOK_MANAGE, S.PLATFORM_ADMIN}
    assert ordinary < admin, "admin must be a strict superset of an ordinary session"


def test_neither_derived_set_grants_delete_or_emit():
    """The app team said their client issues neither. A session must not inherit them.

    An API key can still be granted these explicitly — this asserts only that a *browser
    session* cannot acquire them by virtue of being logged in.
    """
    S = _scopes()

    for is_admin in (False, True):
        granted = set(_derive(is_admin))
        assert S.MEMORY_DELETE not in granted
        assert S.EVENT_EMIT not in granted


def test_admin_derives_from_the_user_row_not_a_token_claim():
    """One source of truth for 'is this person an operator', per their explicit request.

    Also why a grant takes effect on the next request rather than the next login: nothing is
    baked into the token, so no session has to be invalidated to change authority.
    """
    from AINDY.auth.api_key_auth import derive_session_scopes
    import inspect

    params = inspect.signature(derive_session_scopes).parameters
    assert set(params) == {"is_admin"}, (
        "derivation must depend on the user-row flag alone; another input would be a second "
        "source of truth for operator status"
    )


# --------------------------------------------------------------------------------------
# Enforcement — the actual gap being closed
# --------------------------------------------------------------------------------------


def test_session_is_denied_a_scope_it_does_not_hold():
    """The gap: this previously passed unconditionally for any JWT."""
    from fastapi import HTTPException

    S = _scopes()

    with pytest.raises(HTTPException) as exc:
        _check(S.PLATFORM_ADMIN, _session(is_admin=False))

    assert exc.value.status_code == 403
    assert "Session scope" in str(exc.value.detail)


def test_session_is_allowed_a_scope_it_holds():
    """Liveness control — without it, 'denies' could just mean 'denies everything'."""
    S = _scopes()

    assert _check(S.MEMORY_READ, _session(is_admin=False)) is None


def test_admin_session_passes_an_operator_scope():
    S = _scopes()

    assert _check(S.PLATFORM_ADMIN, _session(is_admin=True)) is None


def test_api_key_path_is_unchanged():
    """Regression control: the half that already worked must keep working."""
    from fastapi import HTTPException

    S = _scopes()
    key = {"auth_type": "api_key", "api_key_scopes": [S.FLOW_READ]}

    assert _check(S.FLOW_READ, key) is None
    with pytest.raises(HTTPException):
        _check(S.MEMORY_WRITE, key)


def test_platform_admin_scope_still_satisfies_any_check():
    """Pre-existing behaviour for keys, now also reachable by an admin session."""
    S = _scopes()
    key = {"auth_type": "api_key", "api_key_scopes": [S.PLATFORM_ADMIN]}

    assert _check(S.MEMORY_WRITE, key) is None
    assert _check(S.MEMORY_WRITE, _session(is_admin=True)) is None


def test_escape_hatch_restores_the_old_bypass(monkeypatch):
    """`AINDY_JWT_SCOPE_ENFORCEMENT=0` — a hatch for an incident, not an opt-in."""
    S = _scopes()
    monkeypatch.setenv("AINDY_JWT_SCOPE_ENFORCEMENT", "0")

    assert _check(S.PLATFORM_ADMIN, _session(is_admin=False)) is None


def test_enforcement_is_on_by_default(monkeypatch):
    from AINDY.services.auth_service import _jwt_scope_enforcement_enabled

    monkeypatch.delenv("AINDY_JWT_SCOPE_ENFORCEMENT", raising=False)
    assert _jwt_scope_enforcement_enabled() is True


# --------------------------------------------------------------------------------------
# The enumeration that justifies default-on
# --------------------------------------------------------------------------------------


def test_every_enforced_scope_is_held_by_an_ordinary_session():
    """★ This is the safety argument, executable.

    Default-on is only defensible because every scope any route currently enforces is one an
    ordinary session holds. Scans the source for real `enforce_api_key_scope(Scopes.X)` call
    sites and asserts each `X` is in the ordinary set.

    If someone adds an enforcement an ordinary user cannot satisfy, this fails **here** rather
    than as a 403 in a user's browser — which is exactly the "scattered 403s that read as a
    frontend bug" outcome the app team asked us to avoid.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "AINDY"
    ordinary = set(_derive(False))
    offenders: list[str] = []
    found = 0

    for path in root.rglob("*.py"):
        if path.name == "auth_service.py":
            continue  # its own definition + docstring example, not a route
        for match in re.finditer(r"enforce_api_key_scope\(Scopes\.([A-Z_]+)\)", path.read_text(encoding="utf-8")):
            found += 1
            attr = match.group(1)
            value = getattr(_scopes(), attr)
            if value not in ordinary:
                offenders.append(f"{path.relative_to(root)} -> Scopes.{attr}")

    assert found > 0, "found no enforcement call sites — the scan is broken, not the code"
    assert not offenders, (
        "these routes enforce a scope an ordinary session does NOT hold, so signed-in users "
        f"will get 403s: {offenders}. Either widen the derived set deliberately, or confirm "
        f"the route is admin-only and this test needs an allowlist."
    )
