"""The agent registry is shared by every tenant, so its routes need an operator (DEC-114).

Each route is called for real, because the status code is the contract. A non-operator holds
every scope a session can hold, so a 403 here can only come from the operator gate.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only

_USER = "00000000-0000-0000-0000-00000000c00d"
_AGENT_ID = str(uuid.uuid4())

REGISTRY_ROUTES = [
    ("GET", "/coordination/agents", None),
    ("GET", "/coordination/agents/status", None),
    ("POST", "/coordination/agents/register", {"agent_id": _AGENT_ID}),
    ("POST", f"/coordination/agents/{_AGENT_ID}/heartbeat", {}),
    ("DELETE", f"/coordination/agents/{_AGENT_ID}", None),
]
_IDS = ["list", "status", "register", "heartbeat", "deregister"]


def _principal(*, is_admin: bool) -> dict:
    from AINDY.auth.api_key_auth import Scopes

    return {
        "sub": _USER,
        "user_id": _USER,
        "auth_type": "jwt",
        "is_admin": is_admin,
        "session_scopes": [Scopes.AGENT_RUN],
    }


@pytest.fixture
def signed_in(runtime_only_app):
    from AINDY.services.auth_service import get_current_user

    def _apply(principal: dict):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: principal

    yield _apply
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("route", REGISTRY_ROUTES, ids=_IDS)
def test_an_ordinary_user_is_refused(route, runtime_only_client, signed_in, mock_db):
    method, path, body = route
    signed_in(_principal(is_admin=False))

    assert runtime_only_client.request(method, path, json=body).status_code == 403


@pytest.mark.parametrize("route", REGISTRY_ROUTES, ids=_IDS)
def test_control_an_operator_gets_past_the_gate(route, runtime_only_client, signed_in, mock_db):
    method, path, body = route
    signed_in(_principal(is_admin=True))

    assert runtime_only_client.request(method, path, json=body).status_code != 403


def test_an_api_key_without_platform_admin_is_refused(runtime_only_client, signed_in, mock_db):
    from AINDY.auth.api_key_auth import Scopes

    signed_in(
        {"sub": _USER, "user_id": _USER, "auth_type": "api_key", "api_key_scopes": [Scopes.AGENT_RUN]}
    )

    assert runtime_only_client.get("/coordination/agents").status_code == 403


def test_every_route_that_touches_the_registry_is_gated(runtime_only_app):
    """Derived from the router's source, so a new registry route can't arrive without the gate."""
    import ast
    import inspect

    from AINDY.routes import coordination_router as module_router
    import sys

    source = inspect.getsource(sys.modules["AINDY.routes.coordination_router"])
    del module_router
    touching = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not any("router" in ast.unparse(d) for d in node.decorator_list):
            continue
        text = ast.unparse(node)
        reaches = any(
            name in text
            for name in ("list_agents", "get_agent_status", "register_or_update_agent", "AgentRegistry")
        )
        if reaches:
            touching[node.name] = "_REQUIRE_OPERATOR" in text
    assert len(touching) == 5, touching
    assert all(touching.values()), touching


def _seed_registry(db, state):
    from AINDY.db.models.agent_registry import AgentRegistry

    row = AgentRegistry(agent_id=uuid.uuid4(), capabilities=["x"], current_state=state, load=0.1)
    db.add(row)
    db.flush()
    return str(row.agent_id)


def test_the_graph_leaves_the_registry_out_when_asked(db_session):
    from AINDY.agents.agent_coordinator import coordination_graph

    agent_id = _seed_registry(db_session, {"run_id": "someone-elses"})

    without = coordination_graph(db_session, user_id=_USER, include_registry=False)
    with_registry = coordination_graph(db_session, user_id=_USER, include_registry=True)

    assert agent_id not in {node["id"] for node in without["nodes"]}
    assert agent_id in {node["id"] for node in with_registry["nodes"]}


@pytest.mark.parametrize(("is_admin", "expected"), [(False, False), (True, True)])
def test_the_graph_route_passes_the_callers_authority(
    is_admin, expected, runtime_only_client, signed_in, mock_db, monkeypatch
):
    from AINDY.routes import coordination_router  # noqa: F401
    import sys

    seen = {}

    def spy(db, *, user_id=None, include_registry=True, **_):
        seen["include_registry"] = include_registry
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(sys.modules["AINDY.routes.coordination_router"], "coordination_graph", spy)
    signed_in(_principal(is_admin=is_admin))

    assert runtime_only_client.get("/coordination/graph").status_code == 200
    assert seen["include_registry"] is expected


def test_the_runtime_still_registers_its_own_agent(db_session):
    """The gate is on the routes. Agent runs self-register, and must keep doing so."""
    from AINDY.agents.agent_coordinator import list_agents, register_or_update_agent

    agent_id = str(uuid.uuid4())
    register_or_update_agent(db_session, agent_id=agent_id, capabilities=["x"])

    assert agent_id in {str(agent["agent_id"]) for agent in list_agents(db_session)}
