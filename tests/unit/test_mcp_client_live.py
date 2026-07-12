"""Live end-to-end MCP round-trip (ECOGAP-4 / G4b).

Stands up a real MCP server over SSE, then drives it through the shipped client glue:
discover_and_register -> register_tool -> the registered fn -> sync<->async bridge ->
real MCP client -> real MCP server -> tool handler -> back.

Runs only when the `[mcp]` extra is installed (nodus-mcp + the mcp SDK) and an ASGI
server is available; skips cleanly otherwise, so it no-ops in environments without the
extra and exercises the real wire in CI where the extra is installed.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

pytestmark = pytest.mark.runtime_only

# Skip the whole module unless the real MCP stack + an ASGI server are importable.
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


def _build_sse_app(server):
    """Correct SSE app: /sse stream + the /messages/ POST mount the library omits (nodus-mcp #7)."""
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/messages/")

    async def _handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await server._server.run(r, w, server._server.create_initialization_options())

    return Starlette(
        routes=[Route("/sse", endpoint=_handle_sse), Mount("/messages/", app=sse.handle_post_message)]
    )


def test_live_mcp_round_trip():
    from nodus_mcp_aindy import ToolRegistry, NodusServer, ToolDefinition
    from AINDY.platform_layer import mcp_client
    from AINDY.agents.tool_registry import TOOL_REGISTRY

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="echo",
            description="Echo args back",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            handler=lambda args: {"echoed": args},
        )
    )
    app = _build_sse_app(NodusServer(reg, name="ci-live"))

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    registered: list[str] = []
    try:
        # Wait for the port to accept connections.
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        url = f"http://127.0.0.1:{port}/sse"
        registered = mcp_client.discover_and_register({"name": "ci", "url": url, "timeout": 10})
        assert registered == ["mcp_ci_echo"], f"discovery did not register the tool: {registered}"

        fn = TOOL_REGISTRY["mcp_ci_echo"]["fn"]
        result = fn(args={"msg": "hello over real MCP"}, user_id="u", db=None)
        assert result == {"echoed": {"msg": "hello over real MCP"}}
    finally:
        for name in registered:
            TOOL_REGISTRY.pop(name, None)
        server.should_exit = True
        thread.join(timeout=5)
