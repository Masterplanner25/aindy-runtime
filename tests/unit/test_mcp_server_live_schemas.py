"""Live `tools/list` over SSE from the runtime's OWN server path (MCP-SDK-2X-1 closure, #727).

`test_mcp_client_live.py` drives the real wire with nodus-mcp's echo tool. Nothing drove the
runtime's own path — `build_registry` → `syscall_entry_to_tool` → `NodusServer.run_sse_app()`
— and read the schemas back through the runtime's own client path (`discover_tools`, what
`mcp_client.py` uses). That is where the cap's second defect lived: under nodus-mcp 0.1.3 on
mcp 2.x every discovered tool arrived with an EMPTY schema and a 200 (the SDK renamed
`Tool.inputSchema` → `input_schema`; the adapter's `getattr` defaulted to `{}`). A 200 with an
empty schema is exactly the shape "Trusting a green check" exists for, so the assertion is
equality with a NON-EMPTY expected schema, never merely "the call succeeded".

Runs whatever `mcp` major is installed (CI resolves the newest 2.x since #727; a 1.x install
exercises the other branch). Skips cleanly without the `[mcp]` extra.
"""
from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

import pytest

pytestmark = pytest.mark.runtime_only

mcp = pytest.importorskip("mcp")
nodus_mcp_aindy = pytest.importorskip("nodus_mcp_aindy")
uvicorn = pytest.importorskip("uvicorn")
pytest.importorskip("starlette")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_runtime_server_schemas_survive_the_wire(monkeypatch):
    from nodus_mcp_aindy import NodusServer, discover_tools, syscall_entry_to_tool
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY
    from AINDY.platform_layer import mcp_server

    # The default read-only allowlist; the handler never runs for tools/list, so no DB, no
    # identity — but resolve the same list the shipped server would.
    monkeypatch.delenv("AINDY_MCP_SERVER_TOOLS", raising=False)
    allowlist = mcp_server.resolve_allowlist()
    assert allowlist, "the default allowlist is empty — nothing would be on the wire"

    expected: dict[str, dict] = {}
    for name in allowlist:
        entry = SYSCALL_REGISTRY.get(name)
        assert entry is not None, f"allowlisted syscall {name!r} is not registered"
        tool = syscall_entry_to_tool(name, entry, handler=lambda args: None)
        assert tool.input_schema and tool.input_schema.get("properties"), (
            f"{name}: the converter produced an empty schema — the assertion below would be vacuous"
        )
        expected[tool.name] = tool.input_schema

    registry = mcp_server.build_registry("probe-user", allowlist)
    assert sorted(registry.names()) == sorted(expected)

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            NodusServer(registry, name="ci-schemas").run_sse_app(),
            host="127.0.0.1", port=port, log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        tools = asyncio.run(discover_tools(f"http://127.0.0.1:{port}/sse", timeout=10))
        on_wire = {t.name: (t.input_schema or {}) for t in tools}

        assert sorted(on_wire) == sorted(expected), f"tools/list returned {sorted(on_wire)}"
        empty = [n for n, s in on_wire.items() if not s.get("properties")]
        assert not empty, f"empty schema behind a 200 for {empty} (the MCP-SDK-2X-1 second defect)"
        for name, schema in expected.items():
            assert on_wire[name] == schema, f"{name}: wire schema differs from the converter's"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
