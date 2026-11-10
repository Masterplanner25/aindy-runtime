"""A handler's exception keeps the failure class it declares (FR-66 side note).

A memory read with the path `/` was refused correctly, but the envelope said `transient`. The
dispatcher classified a handler's exception by its message alone, and "MAS path must start with
'/memory'" matched nothing in the fallback table, whose default is `transient`. So an MCP client
was told to retry a call that can never succeed.

The tool seam already honoured an exception's declared `failure_class` (RETRY-CLASSIFY-1); the
syscall dispatcher did not. Every test here runs the real MCP handler and the real dispatcher.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytest.importorskip("nodus_mcp_aindy")

pytestmark = pytest.mark.runtime_only

USER_ID = str(uuid.uuid4())


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "declared_class.db")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _tool(name: str):
    from AINDY.platform_layer import mcp_server

    registry = mcp_server.build_registry(USER_ID, [name])
    return registry.get(registry.names()[0])


@pytest.mark.parametrize(
    "name,payload",
    [
        ("sys.v1.memory.read", {"path": "/"}),
        ("sys.v1.memory.list", {"path": "/"}),
        ("sys.v1.memory.tree", {"path": "/"}),
        ("sys.v1.memory.search", {"query": "anything", "path": "/"}),
        ("sys.v1.memory.list", {"path": "/notes/**"}),
    ],
)
def test_a_path_outside_memory_is_refused_as_invalid_and_never_retried(name, payload, session_factory):
    from AINDY.core.retry_policy import is_retryable_error

    result = _tool(name).handler(payload)

    assert result["status"] == "error", result
    assert "must start with '/memory'" in result["error"]
    assert result["failure_class"] == "invalid"
    assert is_retryable_error(result) is False


def _raising_handler(monkeypatch, name: str, exc: BaseException) -> None:
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    def _raise(payload, context):
        raise exc

    monkeypatch.setattr(SYSCALL_REGISTRY[name], "handler", _raise)


def test_control_an_undeclared_value_error_still_goes_to_the_fallback_table(session_factory, monkeypatch):
    """Liveness: `invalid` above comes from the declaration, not from every ValueError."""
    _raising_handler(monkeypatch, "sys.v1.memory.list", ValueError("something odd happened"))

    result = _tool("sys.v1.memory.list").handler({"path": f"/memory/{USER_ID}/**"})

    assert result["status"] == "error"
    assert result["failure_class"] == "transient"


@pytest.mark.parametrize("declared", ["permission", "not_found", "fatal"])
def test_any_declared_class_is_kept(declared, session_factory, monkeypatch):
    exc = RuntimeError("declared by the raising site")
    exc.failure_class = declared
    _raising_handler(monkeypatch, "sys.v1.memory.list", exc)

    result = _tool("sys.v1.memory.list").handler({"path": f"/memory/{USER_ID}/**"})

    assert result["failure_class"] == declared


def test_a_declaration_outside_the_vocabulary_is_ignored(session_factory, monkeypatch):
    exc = RuntimeError("declared nonsense")
    exc.failure_class = "maybe"
    _raising_handler(monkeypatch, "sys.v1.memory.list", exc)

    result = _tool("sys.v1.memory.list").handler({"path": f"/memory/{USER_ID}/**"})

    assert result["failure_class"] == "transient"


def test_the_path_error_is_still_a_value_error():
    """Every existing `except ValueError` around `normalize_path` keeps catching it."""
    from AINDY.memory.memory_address_space import InvalidMemoryPath, normalize_path

    with pytest.raises(ValueError) as refused:
        normalize_path("/")
    assert isinstance(refused.value, InvalidMemoryPath)
    with pytest.raises(InvalidMemoryPath):
        normalize_path("")


# -- the HTTP route that catches it as a ValueError still answers 400 ----------------------------


@pytest.fixture
def as_admin(runtime_only_app):
    from AINDY.services.auth_service import get_current_user

    runtime_only_app.dependency_overrides[get_current_user] = lambda: {
        "sub": USER_ID, "user_id": USER_ID, "auth_type": "jwt", "is_admin": True,
        "session_scopes": ["platform.admin"],
    }
    try:
        yield
    finally:
        runtime_only_app.dependency_overrides.pop(get_current_user, None)


def test_the_platform_memory_route_still_answers_400(runtime_only_client, as_admin):
    response = runtime_only_client.get("/platform/memory", params={"path": "/"})

    assert response.status_code == 400, response.text
    assert "must start with '/memory'" in response.text
