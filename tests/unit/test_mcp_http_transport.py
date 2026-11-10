"""`aindy-runtime mcp-server --transport http`: streamable HTTP at /mcp (FR-66 phase 1).

The app `build_http_app()` returns is served under uvicorn on a free port and driven with the
SDK's own client through a real `initialize`, `tools/list` and `tools/call`, on whichever SDK
major is installed (CI runs the newest 2.x). Identity comes from the request headers, as over
SSE, and a platform key is held to its scopes (DEC-126). The `Host` check fails closed (DEC-127).
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytest.importorskip("nodus_mcp_aindy")
uvicorn = pytest.importorskip("uvicorn")

from mcp import ClientSession  # noqa: E402

try:
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # mcp 1.x before the rename
    streamable_http_client = None
    from mcp.client.streamable_http import streamablehttp_client

try:  # mcp 2.x's client is built on httpx2; 1.x on httpx
    import httpx2 as _httpx
except ImportError:
    import httpx as _httpx

pytestmark = pytest.mark.runtime_only

ALICE = uuid.UUID("00000000-0000-0000-0000-00000000a11c")
TOOL = "sys.v1.memory.list"


# -- serving -----------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Served:
    """An ASGI app under uvicorn on a daemon thread, for one test (nodus-mcp's test shape)."""

    def __init__(self, app):
        self.port = _free_port()
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/nothing", timeout=1)
                return self
            except urllib.error.HTTPError:
                return self  # the app is answering
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("uvicorn did not come up")

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(10)

    def url(self, path: str = "/mcp") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


async def _with_session(url: str, headers: dict, fn):
    if streamable_http_client is None:
        async with streamablehttp_client(url, headers=headers) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await fn(session)
    async with _httpx.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await fn(session)


def _call(url: str, headers: dict, args: dict):
    async def _fn(session):
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        assert len(names) == 1, names  # the allowlist is one syscall
        result = await session.call_tool(names[0], args)  # the MCP-safe name, e.g. nodus_memory_list
        return names, result

    return asyncio.run(_with_session(url, headers, _fn))


def _envelope(result) -> dict:
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text)


def _is_error(result) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


# -- fixtures ----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _scopes_column_works_on_sqlite(monkeypatch):
    from sqlalchemy import JSON

    from AINDY.db.models.api_key import PlatformAPIKey

    monkeypatch.setattr(PlatformAPIKey.__table__.c.scopes, "type", JSON())


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "mcp_http.db")
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    from AINDY.db.models.user import User

    db = session_factory()
    db.add(User(id=ALICE, email="alice-http@example.test", hashed_password="x"))
    db.commit()
    db.close()
    try:
        yield session_factory
    finally:
        engine.dispose()


@pytest.fixture
def multi_tenant(monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_MULTI_TENANT", "true")
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", TOOL)
    monkeypatch.delenv("AINDY_MCP_SERVER_USER_ID", raising=False)
    monkeypatch.delenv("AINDY_MCP_SERVER_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("AINDY_MCP_SERVER_ALLOWED_ORIGINS", raising=False)


@pytest.fixture(autouse=True)
def _restore_effect_attribution():
    from AINDY.kernel import effect_ledger

    token = effect_ledger.set_effect_attribution(tenant_id=None, session_id=None)
    try:
        yield
    finally:
        effect_ledger.reset_effect_attribution(token)


def _key(factory, *scopes: str) -> dict:
    from AINDY.platform_layer.api_key_service import create_api_key

    db = factory()
    try:
        _, raw = create_api_key(str(ALICE), f"k-{uuid.uuid4()}", list(scopes), db)
        return {"X-Platform-Key": raw}
    finally:
        db.close()


def _app(host: str = "127.0.0.1"):
    from AINDY.platform_layer import mcp_server

    app, _mode = mcp_server.build_http_app(host=host)
    return app


# -- the transport works -----------------------------------------------------------------------


def test_a_key_with_the_scope_lists_and_calls_the_tool_over_http(factory, multi_tenant):
    with _Served(_app()) as served:
        names, result = _call(served.url(), _key(factory, "memory.read"), {"path": f"/memory/{ALICE}/**"})

    assert names == [TOOL.replace("sys.v1.", "nodus_").replace(".", "_")]
    envelope = _envelope(result)
    assert envelope["status"] == "success", envelope
    assert envelope["data"]["count"] == 0


def test_a_key_without_the_scope_is_refused_over_http(factory, multi_tenant):
    with _Served(_app()) as served:
        _, result = _call(served.url(), _key(factory, "flow.read"), {"path": f"/memory/{ALICE}/**"})

    envelope = _envelope(result)
    assert envelope["status"] == "error"
    assert envelope["failure_class"] == "permission"


def test_a_call_with_no_credential_is_refused_over_http(factory, multi_tenant):
    with _Served(_app()) as served:
        _, result = _call(served.url(), {}, {"path": f"/memory/{ALICE}/**"})

    assert _is_error(result)


# -- the Host check (DEC-127) ------------------------------------------------------------------


def _raw_post(port: int, host_header: str, origin: str | None = None) -> int:
    headers = {
        "Host": host_header,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if origin:
        headers["Origin"] = origin
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_on_loopback_a_foreign_host_or_origin_is_refused(factory, multi_tenant):
    with _Served(_app()) as served:
        assert _raw_post(served.port, f"127.0.0.1:{served.port}") not in (403, 421)  # control
        assert _raw_post(served.port, "evil.example") == 421
        assert _raw_post(served.port, f"127.0.0.1:{served.port}", origin="https://evil.example") == 403


def test_a_listed_public_host_is_the_only_one_accepted(factory, multi_tenant, monkeypatch):
    monkeypatch.setenv("AINDY_MCP_SERVER_ALLOWED_HOSTS", "mcp.example.test")

    with _Served(_app(host="0.0.0.0")) as served:
        assert _raw_post(served.port, "mcp.example.test") not in (403, 421)  # control
        assert _raw_post(served.port, f"127.0.0.1:{served.port}") == 421


def test_a_non_loopback_bind_without_a_listed_host_refuses_to_start(factory, multi_tenant):
    from AINDY.platform_layer import mcp_server

    with pytest.raises(RuntimeError, match="AINDY_MCP_SERVER_ALLOWED_HOSTS"):
        mcp_server.build_http_app(host="0.0.0.0")


def test_a_single_identity_server_still_needs_its_identity(factory, monkeypatch):
    from AINDY.platform_layer import mcp_server

    monkeypatch.delenv("AINDY_MCP_SERVER_MULTI_TENANT", raising=False)
    monkeypatch.delenv("AINDY_MCP_SERVER_USER_ID", raising=False)
    with pytest.raises(RuntimeError, match="AINDY_MCP_SERVER_USER_ID"):
        mcp_server.build_http_app()


# -- the CLI -----------------------------------------------------------------------------------


def test_the_cli_offers_http_and_binds_it_to_loopback_by_default():
    import sys

    import AINDY.runtime_only  # noqa: F401

    runtime_only = sys.modules["AINDY.runtime_only"]
    assert runtime_only._MCP_DEFAULT_HOST == {"sse": "0.0.0.0", "http": "127.0.0.1"}
