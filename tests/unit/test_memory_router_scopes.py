"""HTTP-SCOPE-GAP-1 (D) — the memory router now checks authority, not just identity.

Every one of the 22 routes in `memory_router.py` depended on `get_current_user` alone.
`grep -c enforce_api_key_scope AINDY/routes/memory_router.py` was **0** while that file reached
memory writes, graph edits and Nodus script execution. Anyone who could log in could do all of
it, and an API key issued with `flow.read` only could too.

Two things are asserted here, and the second is the one that matters:

* **The routes are gated** — derived from the *registered application*, not from the source
  text, so a route added tomorrow without a gate fails this file rather than shipping open.
* **The routes still work for the people who already use them.** `ROUTE-GUARD-1`'s lesson is
  that a route test must call the route; the same applies to a scope test. Every case below
  goes over HTTP and distinguishes 403 from "reached the handler".

★ The read gate accepts `memory.read` **or** `memory.write`, because
`_DISPATCH_CAPABILITY_SCOPES` already authorizes reads with either. Without that, one API key
would read fine through `POST /platform/syscall` and get a 403 on `GET /memory/nodes` — two
answers to one authority question, from one credential.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

_USER = "00000000-0000-0000-0000-000000000009"


def _session(*scopes: str) -> dict:
    return {"sub": _USER, "user_id": _USER, "auth_type": "jwt", "session_scopes": list(scopes)}


def _scopes():
    from AINDY.auth.api_key_auth import Scopes

    return Scopes


@pytest.fixture
def as_session(runtime_only_app):
    """Authenticate as a JWT session holding exactly the scopes a test names."""
    from AINDY.services.auth_service import get_current_user

    def _apply(*scopes: str):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: _session(*scopes)

    yield _apply
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


# A read, a write, and an execution — one probe per gate. Payloads are deliberately minimal:
# the assertion is 403-or-not, never that the handler succeeds.
_READ = ("GET", "/memory/nodes", None)
_WRITE = ("POST", "/memory/nodes", {"content": "c"})
_EXECUTE = ("POST", "/memory/execute", {"workflow": {}, "input": {}})


def _call(client, probe):
    method, path, body = probe
    return client.request(method, path, json=body)


# --------------------------------------------------------------------------------------
# The gap: identity was enough
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("probe", [_READ, _WRITE, _EXECUTE], ids=["read", "write", "execute"])
def test_a_scopeless_session_is_refused(probe, runtime_only_client, as_session, mock_db):
    """Before this change all three returned the handler's answer to *any* signed-in user."""
    as_session()

    response = _call(runtime_only_client, probe)

    assert response.status_code == 403, (
        f"{probe[0]} {probe[1]} answered {response.status_code} to a caller holding no scopes — "
        f"the route is still gated on identity alone. Body: {response.text[:300]}"
    )


# --------------------------------------------------------------------------------------
# Liveness — "denies" must not mean "denies everything"
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probe, scope_attr",
    [(_READ, "MEMORY_READ"), (_WRITE, "MEMORY_WRITE"), (_EXECUTE, "FLOW_EXECUTE")],
    ids=["read", "write", "execute"],
)
def test_the_right_scope_reaches_the_handler(
    probe, scope_attr, runtime_only_client, as_session, mock_db
):
    """Without this, a gate that rejected every caller would satisfy the test above."""
    as_session(getattr(_scopes(), scope_attr))

    response = _call(runtime_only_client, probe)

    assert response.status_code != 403, (
        f"{probe[0]} {probe[1]} refused a caller holding {scope_attr} — the gate is too tight. "
        f"Body: {response.text[:300]}"
    )


# --------------------------------------------------------------------------------------
# ★ The claim that makes this safe to ship enforcing
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("probe", [_READ, _WRITE, _EXECUTE], ids=["read", "write", "execute"])
def test_an_ordinary_signed_in_user_loses_nothing(
    probe, runtime_only_client, as_session, mock_db
):
    """★ Default-on is defensible only if the people already using these routes keep working.

    An ordinary session derives `memory.read/write` and `flow.execute` from the user row, so
    every gate in this router is satisfiable without any grant being issued to anyone. If that
    stops being true this fails here, not as scattered 403s in a browser — the outcome the app
    team explicitly asked us to avoid.
    """
    from AINDY.auth.api_key_auth import derive_session_scopes

    as_session(*derive_session_scopes(is_admin=False))

    response = _call(runtime_only_client, probe)

    assert response.status_code != 403, (
        f"{probe[0]} {probe[1]} refused an ordinary signed-in user. Body: {response.text[:300]}"
    )


def test_a_write_scoped_caller_can_read(runtime_only_client, as_session, mock_db):
    """★ Agreement with the governed dispatch surface.

    `_DISPATCH_CAPABILITY_SCOPES["memory.read"]` is `{memory.read, memory.write}`. A key holding
    only `memory.write` therefore reads fine through `POST /platform/syscall`. If this route
    demanded a literal `memory.read`, the same credential would get two different answers to one
    authority question depending on which door it used.
    """
    as_session(_scopes().MEMORY_WRITE)

    response = _call(runtime_only_client, _READ)

    assert response.status_code != 403, response.text[:300]


def test_reading_does_not_confer_writing(runtime_only_client, as_session, mock_db):
    """The implication is one-directional, or the split is decorative."""
    as_session(_scopes().MEMORY_READ)

    response = _call(runtime_only_client, _WRITE)

    assert response.status_code == 403, (
        "a read-only caller created a memory node — read must not imply write"
    )


def test_memory_scopes_do_not_confer_script_execution(
    runtime_only_client, as_session, mock_db
):
    """`/memory/execute` and `/memory/nodus/execute` compile and run caller-supplied code.

    That is materially more authority than storing a node, so it is gated on `flow.execute`.
    Filing it under a memory scope would have made "may I remember this" and "may I run this"
    the same permission.
    """
    as_session(_scopes().MEMORY_WRITE, _scopes().MEMORY_READ)

    response = _call(runtime_only_client, _EXECUTE)

    assert response.status_code == 403, (
        "a memory-scoped caller executed a workflow — memory and execution authority are "
        "collapsed into one permission"
    )


def test_platform_admin_still_satisfies_every_gate(runtime_only_client, as_session, mock_db):
    """`platform.admin` is the documented override in `enforce_api_key_scope`; pinned so this
    router does not become the one place it stops working."""
    as_session(_scopes().PLATFORM_ADMIN)

    for probe in (_READ, _WRITE, _EXECUTE):
        assert _call(runtime_only_client, probe).status_code != 403, probe


# --------------------------------------------------------------------------------------
# Coverage — derived from the registered app, not from the source text
# --------------------------------------------------------------------------------------


def _memory_routes(app):
    """Every registered route owned by `memory_router.py`, with its gate names.

    ★ Walks via `_iter_api_routes`, not `app.routes`. FastAPI ≥ 0.137 stores `include_router`
    results as a lazy `_IncludedRouter` rather than flattening them, so a plain scan of
    `app.routes` finds **zero** memory routes — and a coverage test that finds zero routes
    reports "nothing ungated". The first draft of this file did exactly that and passed the
    `not ungated` assertion; only the `checked >= 20` floor caught it.

    Ownership is decided by the endpoint's module rather than by a path prefix, so the test
    cannot be fooled by a prefix change or by an app-side router mounted at `/memory`.
    """
    from AINDY.core.route_execution_guard import _iter_api_routes

    for route, _ in _iter_api_routes(app.routes):
        if getattr(route.endpoint, "__module__", "") != "AINDY.routes.memory_router":
            continue
        names = {
            getattr(dep.call, "__name__", "") for dep in route.dependant.dependencies
        }
        yield route, {n for n in names if n.startswith("enforce_scope_")}


def test_every_registered_memory_route_carries_a_scope_gate(runtime_only_app):
    """★ Route-derived, so a route added without a gate fails here.

    Counting `enforce_api_key_scope` occurrences in the file would pass while a *new* route
    quietly shipped ungated — the source says "enforcement exists", not "enforcement covers
    this route". This walks what FastAPI actually registered and inspects each route's resolved
    dependency tree.
    """
    ungated = []
    checked = 0

    for route, gates in _memory_routes(runtime_only_app):
        checked += 1
        if not gates:
            ungated.append(f"{sorted(route.methods)} {route.path}")

    assert checked >= 20, f"expected the memory router to be mounted, saw {checked} routes"
    assert not ungated, (
        f"these registered memory routes enforce no scope — identity, not authority: {ungated}"
    )


def test_the_gates_used_are_the_three_intended_ones(runtime_only_app):
    """No fourth gate appeared by copy-paste, and none of the three was dropped.

    The dependency's `__name__` encodes its scopes (`enforce_scope_<a>_or_<b>`), so this reads
    the real enforcement rather than a comment about it.
    """
    seen: set[str] = set()
    for _, gates in _memory_routes(runtime_only_app):
        seen |= gates

    assert seen == {
        "enforce_scope_memory_read_or_memory_write",
        "enforce_scope_memory_write",
        "enforce_scope_flow_execute",
    }, f"unexpected gate set on the memory router: {sorted(seen)}"


def test_no_memory_route_carries_two_gates(runtime_only_app):
    """Two gates on one route would be an all-of check nobody asked for.

    `enforce_api_key_scope`'s alternatives are any-of; stacking two dependencies silently
    changes the semantics to and-of, which is the kind of authority change that should be
    deliberate rather than emergent from an edit.
    """
    doubled = [
        f"{sorted(route.methods)} {route.path} -> {sorted(gates)}"
        for route, gates in _memory_routes(runtime_only_app)
        if len(gates) > 1
    ]

    assert not doubled, doubled
