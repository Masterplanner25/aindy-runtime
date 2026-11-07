"""An MCP tool is granted the capability its syscall declares (DEC-112).

The grant used to be inferred from the name, so `sys.v1.memory.search` got `memory.search`, while
it declares `memory.read`, and was refused on every call. Three of the five default read tools
could never succeed. Nothing noticed, because `test_mcp_server.py` mocks `dispatch_syscall`: a
mock cannot reach the capability check. Every test here runs the real dispatcher.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytest.importorskip("nodus_mcp_aindy")

pytestmark = pytest.mark.runtime_only

USER_ID = str(uuid.uuid4())
_SAMPLES = {
    "string": "x",
    "str": "x",
    "int": 1,
    "integer": 1,
    "float": 1.0,
    "number": 1.0,
    "bool": True,
    "boolean": True,
    "list": [],
    "array": [],
    "dict": {},
    "object": {},
}


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "mcp_grant.db")
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _registered() -> list[str]:
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    names = sorted(SYSCALL_REGISTRY.keys())
    assert names, "the registry is empty: a census over it would pass by saying nothing"
    return names


def _minimal_payload(name: str) -> dict:
    """Satisfy the syscall's own schema, so a refusal here can only be the capability check."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    schema = SYSCALL_REGISTRY[name].input_schema or {}
    properties = schema.get("properties") or {}
    return {
        field: _SAMPLES.get((properties.get(field) or {}).get("type"), "x")
        for field in schema.get("required") or []
    }


def _tool(name: str):
    from AINDY.platform_layer import mcp_server

    registry = mcp_server.build_registry(USER_ID, [name])
    return registry.get(registry.names()[0])


def _recording_handler(monkeypatch, name: str) -> list:
    """Swap in a handler that records what reached it. The dispatcher in front of it stays real."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    seen: list = []

    def _record(payload, context):
        seen.append(list(context.capabilities))
        return {}

    monkeypatch.setattr(SYSCALL_REGISTRY[name], "handler", _record)
    return seen


@pytest.mark.parametrize("name", _registered())
def test_every_syscall_reaches_its_handler_holding_only_its_declared_capability(
    name, session_factory, monkeypatch
):
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    seen = _recording_handler(monkeypatch, name)

    result = _tool(name).handler(_minimal_payload(name))

    assert seen, f"{name} never reached its handler: {result}"
    assert seen == [[SYSCALL_REGISTRY[name].capability]]


@pytest.mark.parametrize(
    "name", ["sys.v1.memory.search", "sys.v1.memory.tree", "sys.v1.memory.trace"]
)
def test_control_the_name_inferred_grant_is_still_refused(name, session_factory, monkeypatch):
    """Liveness for the census: the check it passes is one that really does refuse.

    It also pins that DEC-112 changed the MCP server and not `dispatch_syscall`, whose other
    callers still infer the capability.
    """
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall

    seen = _recording_handler(monkeypatch, name)

    result = dispatch_syscall(name, _minimal_payload(name), user_id=USER_ID)

    assert seen == []
    assert result["status"] == "error"
    assert "requires capability 'memory.read'" in result["error"]


def test_default_tools_cover_the_ones_that_were_refused():
    """The census above is only about MCP's reach if these are what the server exposes."""
    from AINDY.platform_layer.mcp_server import READ_ONLY_SYSCALLS

    assert {"sys.v1.memory.search", "sys.v1.memory.tree", "sys.v1.memory.trace"} <= set(
        READ_ONLY_SYSCALLS
    )


def _seed_path_node(factory, content: str) -> str:
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
    from AINDY.memory.memory_address_space import path_from_write_payload

    full_path, namespace, addr_type = path_from_write_payload(
        {"node_type": "insight", "namespace": "probe", "addr_type": "note"},
        tenant_id=USER_ID,
    )
    db = factory()
    try:
        MemoryNodeDAO(db).save(
            content=content,
            user_id=USER_ID,
            node_type="insight",
            generate_embedding=False,
            path=full_path,
            namespace=namespace,
            addr_type=addr_type,
        )
        db.commit()
    finally:
        db.close()
    return full_path


def test_the_real_read_handlers_succeed_through_the_mcp_handler(session_factory):
    """These tools had never once succeeded over MCP, so nothing past the capability check had
    ever been reached from here: bind, schema, handler, output validation."""
    node_path = _seed_path_node(session_factory, "a note the probe can find")

    tree = _tool("sys.v1.memory.tree").handler({"path": f"/memory/{USER_ID}/**"})
    assert tree["status"] == "success", tree
    assert tree["data"]["node_count"] == 1

    listed = _tool("sys.v1.memory.list").handler({"path": f"/memory/{USER_ID}/**"})
    assert listed["status"] == "success", listed
    assert listed["data"]["count"] == 1

    trace = _tool("sys.v1.memory.trace").handler({"path": node_path})
    assert trace["status"] == "success", trace


def test_search_succeeds_through_the_mcp_handler(session_factory, monkeypatch):
    """Only `recall` is stubbed, because it needs embeddings. The handler, its schema and the
    output validation stay real."""
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    monkeypatch.setattr(MemoryNodeDAO, "recall", lambda self, **kwargs: [])

    result = _tool("sys.v1.memory.search").handler({"query": "anything", "limit": 1})

    assert result["status"] == "success", result


def test_a_bare_memory_root_is_an_exact_lookup_and_finds_nothing(session_factory):
    """Why `list /memory` proves nothing about whether nodes have paths: pinned for MEM-PATH-NULL-1."""
    _seed_path_node(session_factory, "a note under the tenant")

    listed = _tool("sys.v1.memory.list").handler({"path": "/memory"})

    assert listed["status"] == "success", listed
    assert listed["data"]["count"] == 0
