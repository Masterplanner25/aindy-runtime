"""An MCP call made with a platform key is held to the key's scopes, on every transport (DEC-126).

The multi-tenant hook resolves the caller from the request headers. For a platform key it used
to keep only the owner, so the tool ran with the capability it declares (DEC-112) whatever the
key's scopes were. The rule is now `POST /platform/syscall`'s, from one shared map
(`key_scopes_authorize`): the key must hold a scope that authorizes the tool's capability, or
`platform.admin`. A session JWT is not scope-gated, as on that route.

Everything is real here: keys minted into a private engine, the hook, the resolver, the MCP
handler and the dispatcher. Where the question is "did the call reach the syscall", the
syscall's handler is swapped for a recorder and the dispatcher in front of it stays real.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytest.importorskip("nodus_mcp_aindy")

pytestmark = pytest.mark.runtime_only

ALICE = uuid.UUID("00000000-0000-0000-0000-0000000a11ce")


@pytest.fixture(autouse=True)
def _scopes_column_works_on_sqlite(monkeypatch):
    """`scopes` is a Postgres ARRAY, which SQLite can't bind (as in `test_watcher_user_keys.py`)."""
    from sqlalchemy import JSON

    from AINDY.db.models.api_key import PlatformAPIKey

    monkeypatch.setattr(PlatformAPIKey.__table__.c.scopes, "type", JSON())


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "mcp_scopes.db")
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    from AINDY.db.models.user import User

    db = session_factory()
    db.add(User(id=ALICE, email="alice@example.test", hashed_password="x"))
    db.commit()
    db.close()
    try:
        yield session_factory
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_session_context():
    """The hook sets ContextVars and the effect attribution; restore them after every test."""
    from AINDY.kernel import effect_ledger
    from AINDY.platform_layer import mcp_server

    identity = mcp_server._SESSION_IDENTITY.set(None)
    scopes = mcp_server._SESSION_SCOPES.set(None)
    attribution = effect_ledger.set_effect_attribution(tenant_id=None, session_id=None)
    try:
        yield
    finally:
        effect_ledger.reset_effect_attribution(attribution)
        mcp_server._SESSION_SCOPES.reset(scopes)
        mcp_server._SESSION_IDENTITY.reset(identity)


def _key(factory, *scopes: str) -> dict:
    from AINDY.platform_layer.api_key_service import create_api_key

    db = factory()
    try:
        _, raw = create_api_key(str(ALICE), f"k-{uuid.uuid4()}", list(scopes), db)
        return {"x-platform-key": raw}
    finally:
        db.close()


def _jwt() -> dict:
    from AINDY.services.auth_service import create_access_token

    return {"authorization": f"Bearer {create_access_token({'sub': str(ALICE)})}"}


def _call(name: str, payload: dict, headers: dict) -> dict:
    """One multi-tenant MCP call: the hook on the request's headers, then the tool's handler."""
    from AINDY.platform_layer import mcp_server

    mcp_server.build_auth_hook()(name, payload, {"headers": headers})
    registry = mcp_server.build_registry(None, [name])
    return registry.get(registry.names()[0]).handler(payload)


@pytest.fixture
def reached(monkeypatch):
    """Record each call that reaches a syscall's handler, then run the real handler."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    seen: list[str] = []

    def _record(name, original):
        def _handler(payload, context):
            seen.append(name)
            return original(payload, context)
        return _handler

    for name in ("sys.v1.memory.read", "sys.v1.memory.write", _unmapped_syscall()):
        monkeypatch.setattr(SYSCALL_REGISTRY[name], "handler", _record(name, SYSCALL_REGISTRY[name].handler))
    return seen


def _unmapped_syscall() -> str:
    """A registered syscall whose capability no key scope authorizes (only platform.admin)."""
    from AINDY.auth.api_key_auth import DISPATCH_CAPABILITY_SCOPES
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    for name, entry in sorted(SYSCALL_REGISTRY.items()):
        if entry.capability not in DISPATCH_CAPABILITY_SCOPES and not (entry.input_schema or {}).get("required"):
            return name
    raise AssertionError("no unmapped syscall without required inputs: the test would say nothing")


_READ = ("sys.v1.memory.read", {"query": "x"})
_WRITE = ("sys.v1.memory.write", {"content": "a note", "node_type": "insight"})


# -- a key is held to its scopes ----------------------------------------------------------------


def test_a_key_without_the_scope_is_refused_before_the_syscall(factory, reached):
    result = _call(*_READ, _key(factory, "flow.read"))

    assert result["status"] == "error"
    assert result["failure_class"] == "permission"
    assert "do not authorize 'memory.read'" in result["error"]
    assert reached == []


def test_control_a_key_with_the_scope_reaches_the_syscall(factory, reached):
    result = _call(*_READ, _key(factory, "memory.read"))

    assert result["status"] == "success", result
    assert reached == ["sys.v1.memory.read"]


def test_a_write_scope_implies_read(factory, reached):
    assert _call(*_READ, _key(factory, "memory.write"))["status"] == "success"


def test_a_read_scope_does_not_imply_write(factory, reached):
    result = _call(*_WRITE, _key(factory, "memory.read"))

    assert result["failure_class"] == "permission"
    assert reached == []


def test_a_capability_no_scope_names_needs_platform_admin(factory, reached):
    name = _unmapped_syscall()

    refused = _call(name, {}, _key(factory, "memory.read", "memory.write", "flow.execute"))
    assert refused["failure_class"] == "permission"
    assert reached == []

    allowed = _call(name, {}, _key(factory, "platform.admin"))
    # The real handler may refuse an empty payload on its own terms; what matters is that the
    # scope check let it through.
    assert allowed.get("failure_class") != "permission", allowed
    assert reached == [name]


def test_a_watcher_only_key_is_still_refused_outright(factory, reached):
    from AINDY.platform_layer import mcp_server

    with pytest.raises(PermissionError):
        mcp_server.build_auth_hook()(_READ[0], {}, {"headers": _key(factory, "watcher.signals")})
    assert reached == []


# -- a session JWT is not scope-gated, as on POST /platform/syscall ------------------------------


def test_a_session_jwt_is_not_scope_gated(factory, reached):
    assert _call(*_WRITE, _jwt())["status"] == "success"
    assert reached == ["sys.v1.memory.write"]


# -- one call's scopes never reach the next -----------------------------------------------------


def test_a_session_call_after_a_key_call_is_not_held_to_the_key(factory, reached):
    assert _call(*_READ, _key(factory, "flow.read"))["failure_class"] == "permission"
    assert _call(*_READ, _jwt())["status"] == "success"


def test_a_key_call_after_a_session_call_is_held_to_its_own_scopes(factory, reached):
    assert _call(*_READ, _jwt())["status"] == "success"
    assert _call(*_READ, _key(factory, "flow.read"))["failure_class"] == "permission"


def test_a_refused_credential_leaves_no_identity_behind(factory, reached):
    from AINDY.platform_layer import mcp_server

    _call(*_READ, _key(factory, "memory.read"))
    with pytest.raises(PermissionError):
        mcp_server.build_auth_hook()(_READ[0], {}, {"headers": {"x-platform-key": "aindy_wrong"}})

    assert mcp_server._SESSION_IDENTITY.get() is None
    assert mcp_server._SESSION_SCOPES.get() is None


# -- the single configured identity is unchanged ------------------------------------------------


def test_the_single_configured_identity_is_not_scope_gated(factory, reached):
    from AINDY.platform_layer import mcp_server

    registry = mcp_server.build_registry(str(ALICE), [_WRITE[0]])
    result = registry.get(registry.names()[0]).handler(_WRITE[1])

    assert result["status"] == "success", result
    assert reached == ["sys.v1.memory.write"]


# -- one map for both doors ---------------------------------------------------------------------


def test_platform_syscall_and_mcp_read_the_same_map():
    import sys

    import AINDY.routes.platform.platform_ops_router  # noqa: F401  (the package attribute is the router)
    from AINDY.auth.api_key_auth import DISPATCH_CAPABILITY_SCOPES

    router_module = sys.modules["AINDY.routes.platform.platform_ops_router"]
    assert router_module._DISPATCH_CAPABILITY_SCOPES is DISPATCH_CAPABILITY_SCOPES
    assert DISPATCH_CAPABILITY_SCOPES, "an empty map would make every check here vacuous"
