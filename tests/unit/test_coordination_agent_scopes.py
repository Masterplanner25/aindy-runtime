"""HTTP-SCOPE-GAP-1 — the last 18 identity-only routes now check authority.

`coordination_router` (13) and `platform/agents_router` (5) depended on `get_current_user`
alone. Agent registration, heartbeats, deregistration, the inter-agent inbox and user-owned
agent CRUD were reachable by anyone who could authenticate.

`agents_router` is worth calling out: it is mounted on the app directly with
`prefix="/platform"`, **not** through `platform_router`, so it never inherited that router's
`require_platform_admin_access`. That is deliberate — FR-12b exists so an ordinary user can own
an agent — but it left the five routes with no authority check at all.

★ The sharpest test here is `test_the_only_identity_only_routes_left_are_the_self_service_two`.
It turns the entry's closing claim into something executable: after this change the whole
application has exactly two routes gated on identity alone, and both act only on the caller's
own account, where a scope answers nothing.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

_USER = "00000000-0000-0000-0000-00000000c00d"


def _scopes():
    from AINDY.auth.api_key_auth import Scopes

    return Scopes


def _session(*scopes: str) -> dict:
    return {"sub": _USER, "user_id": _USER, "auth_type": "jwt", "session_scopes": list(scopes)}


@pytest.fixture
def as_session(runtime_only_app):
    from AINDY.services.auth_service import get_current_user

    def _apply(*scopes: str):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: _session(*scopes)

    yield _apply
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


# One probe per gate. The assertion is always 403-or-not, never that the handler succeeds.
_AGENT = ("GET", "/coordination/agents", None)
_AGENT_WRITE = ("POST", "/coordination/agents/register", {"agent_id": "a1", "role": "worker"})
_RUNS = ("GET", "/coordination/runs", None)
_SHARED_MEMORY = ("GET", "/coordination/memory/shared", None)
_MY_AGENTS = ("GET", "/platform/agents", None)
_CREATE_AGENT = ("POST", "/platform/agents", {"name": "Helper"})

_ALL = [_AGENT, _AGENT_WRITE, _RUNS, _SHARED_MEMORY, _MY_AGENTS, _CREATE_AGENT]
_IDS = ["coord-read", "coord-register", "coord-runs", "shared-memory", "my-agents", "create-agent"]


def _call(client, probe):
    method, path, body = probe
    return client.request(method, path, json=body)


# --------------------------------------------------------------------------------------
# The gap
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("probe", _ALL, ids=_IDS)
def test_a_scopeless_session_is_refused(probe, runtime_only_client, as_session, mock_db):
    as_session()

    response = _call(runtime_only_client, probe)

    assert response.status_code == 403, (
        f"{probe[0]} {probe[1]} answered {response.status_code} to a caller holding no scopes. "
        f"Body: {response.text[:300]}"
    )


# --------------------------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probe, attr",
    [
        (_AGENT, "AGENT_RUN"),
        (_AGENT_WRITE, "AGENT_RUN"),
        (_RUNS, "EXECUTION_READ"),
        (_SHARED_MEMORY, "MEMORY_READ"),
        (_MY_AGENTS, "AGENT_RUN"),
        (_CREATE_AGENT, "AGENT_RUN"),
    ],
    ids=_IDS,
)
def test_the_right_scope_reaches_the_handler(
    probe, attr, runtime_only_client, as_session, mock_db
):
    """Without this, a gate that refused everyone would satisfy the test above.

    ★ Note the asymmetry that keeps this honest: `!= 403` would also be satisfied by a **404**,
    i.e. by a probe pointing at a path that does not exist. What rules that out is the refusal
    test above asserting `== 403` on the same path — an unmatched route never reaches a
    dependency, so a 403 proves the route is both registered and gated. That matters here
    because these handlers are registered at two prefixes.
    """
    as_session(getattr(_scopes(), attr))

    response = _call(runtime_only_client, probe)

    assert response.status_code != 403, (
        f"{probe[0]} {probe[1]} refused a caller holding {attr}. Body: {response.text[:300]}"
    )


@pytest.mark.parametrize("probe", _ALL, ids=_IDS)
def test_an_ordinary_signed_in_user_loses_nothing(
    probe, runtime_only_client, as_session, mock_db
):
    """★ Why this can ship enforcing.

    `agent.run`, `execution.read` and `memory.read` are all in the ordinary derived set, so no
    signed-in user needs a grant issued to keep working.
    """
    from AINDY.auth.api_key_auth import derive_session_scopes

    as_session(*derive_session_scopes(is_admin=False))

    response = _call(runtime_only_client, probe)

    assert response.status_code != 403, (
        f"{probe[0]} {probe[1]} refused an ordinary user. Body: {response.text[:300]}"
    )


# --------------------------------------------------------------------------------------
# The gates are distinct, not decorative
# --------------------------------------------------------------------------------------


def test_agent_authority_does_not_confer_memory_reads(
    runtime_only_client, as_session, mock_db
):
    """`/coordination/memory/shared` queries `memory_nodes` directly.

    Gating it on `agent.run` because it happens to live in this router would let the agent
    surface become a second door onto memory — the distinction the memory router just drew.
    """
    as_session(_scopes().AGENT_RUN)

    response = _call(runtime_only_client, _SHARED_MEMORY)

    assert response.status_code == 403, (
        "an agent-scoped caller read shared memory — the memory gate is not actually separate"
    )


def test_memory_authority_does_not_confer_agent_registration(
    runtime_only_client, as_session, mock_db
):
    """And the converse, or the split above is one-directional theatre."""
    as_session(_scopes().MEMORY_READ, _scopes().MEMORY_WRITE)

    response = _call(runtime_only_client, _AGENT_WRITE)

    assert response.status_code == 403, (
        "a memory-scoped caller registered a coordination agent"
    )


def test_execution_read_does_not_confer_agent_registration(
    runtime_only_client, as_session, mock_db
):
    as_session(_scopes().EXECUTION_READ)

    response = _call(runtime_only_client, _AGENT_WRITE)

    assert response.status_code == 403, response.text[:300]


# --------------------------------------------------------------------------------------
# Coverage, derived from the registered app
# --------------------------------------------------------------------------------------


def _routes(app):
    """Every registered route with the gates that actually apply to it.

    ★ Accumulates each router's `dependencies` down the nesting. A per-route walk of
    `route.dependant` **excludes** dependencies declared on the router a route was included
    into — which is how the `/platform` tree's admin gate went missing from an earlier census
    and produced a "97 routes enforce nothing" figure that was wrong by 56.
    """
    from fastapi.routing import APIRoute, _IncludedRouter

    def names(dependant):
        out = set()

        def walk(d):
            for sub in d.dependencies:
                out.add(getattr(sub.call, "__name__", ""))
                walk(sub)

        walk(dependant)
        return out

    found = []

    def walk(routes, inherited):
        for route in routes:
            if isinstance(route, APIRoute):
                found.append((route, names(route.dependant) | inherited))
            elif isinstance(route, _IncludedRouter):
                extra = {
                    getattr(d.dependency, "__name__", "")
                    for d in (getattr(route.include_context, "dependencies", None) or [])
                }
                extra |= {
                    getattr(d.dependency, "__name__", "")
                    for d in (route.original_router.dependencies or [])
                }
                walk(route.original_router.routes, inherited | extra)

    walk(app.routes, set())
    return found


_GATED_MODULES = (
    "AINDY.routes.coordination_router",
    "AINDY.routes.platform.agents_router",
)


def test_every_route_in_both_routers_carries_a_gate(runtime_only_app):
    """Deduplicated by (method, path).

    `coordination_router` is reachable through two registrations in a booted app — it is in
    `APP_ROUTERS`, mounted under `/apps`, and appears a second time via `get_routers()`. Both
    copies carry the same gate, so this is a composition detail rather than an authority one;
    the dedup exists so the count means "distinct routes" and not "registration events".
    """
    ungated, checked = [], set()

    for route, deps in _routes(runtime_only_app):
        if getattr(route.endpoint, "__module__", "") not in _GATED_MODULES:
            continue
        key = f"{sorted(route.methods)[0]} {route.path}"
        checked.add(key)
        if not any(n.startswith("enforce_scope_") for n in deps):
            ungated.append(key)

    assert len(checked) == 18, (
        f"expected 13 coordination + 5 agent routes, saw {len(checked)}: {sorted(checked)}"
    )
    assert not ungated, f"still identity-only: {sorted(set(ungated))}"


def test_the_gates_used_are_the_four_intended_ones(runtime_only_app):
    seen = set()
    for route, deps in _routes(runtime_only_app):
        if getattr(route.endpoint, "__module__", "") not in _GATED_MODULES:
            continue
        seen |= {n for n in deps if n.startswith("enforce_scope_")}

    assert seen == {
        "enforce_scope_agent_run",
        "enforce_scope_execution_read",
        "enforce_scope_memory_read_or_memory_write",
    }, f"unexpected gate set: {sorted(seen)}"


def test_the_only_identity_only_routes_left_are_the_self_service_two(runtime_only_app):
    """★ The entry's closing claim, made executable.

    Two routes remain gated on identity alone, and both act **only on the caller's own
    account** — logging yourself out and changing your own password. A scope cannot answer
    "may you do this to yourself"; it would be a permission nobody could ever be denied.

    Written as an equality rather than a count so that adding an ungated route fails here, and
    so that gating one of these two also fails here — the second is a decision someone should
    have to make on purpose, not something that happens while tidying.
    """
    identity_only = set()

    for route, deps in _routes(runtime_only_app):
        if any(n.startswith("enforce_scope_") for n in deps):
            continue
        if any("admin" in n for n in deps):
            continue
        if any("current_user" in n or "principal" in n for n in deps):
            identity_only.add(f"{sorted(route.methods)[0]} {route.path}")

    assert identity_only == {
        "POST /auth/logout",
        "POST /auth/password/change",
    }, f"identity-only routes changed: {sorted(identity_only)}"
