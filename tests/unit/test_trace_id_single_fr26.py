"""FR-26 — one request, one trace id.

`log_requests` mints a trace id, sets `request.state.trace_id` and the trace contextvar, and
writes it to the response's `X-Trace-ID`. `ExecutionContext.from_request` read only the
INCOMING headers — which a browser never sends — so it minted a second one, and every
enveloped response carried two ids that resolved to two different event graphs: the
pipeline's own `execution.*` under the body's `trace_id`, and everything the handler did
(flow run, syscalls, memory writes — reading the contextvar) under the header's.

The app team found it 2026-07-22, root-caused it 2026-09-11, and asked for one default.

★ The first test is the contract and goes through the real middleware + a real pipeline
route (`ROUTE-GUARD-1`: a route test must call the route). The rest pin the boundaries the
fix must not cross: the explicit override still wins; a Request that never met the
middleware still honours its headers; a context built with no Request still mints.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.core.execution_pipeline import ExecutionContext

pytestmark = pytest.mark.runtime_only


def _app_with_pipeline_route(metadata_factory=None):
    from AINDY.middleware import register_middleware

    router = APIRouter()
    seen: dict = {}

    @router.get("/enveloped")
    async def enveloped(request: Request):
        async def handler(ctx):
            seen["ctx_request_id"] = ctx.request_id
            seen["state_trace_id"] = request.state.trace_id
            return {"ok": True}

        return await execute_with_pipeline(
            request=request,
            route_name="apps.fr26.enveloped",
            handler=handler,
            metadata=metadata_factory() if metadata_factory else None,
        )

    app = FastAPI()
    app.include_router(router)
    register_middleware(app)
    return app, seen


def test_the_body_trace_id_is_the_header_trace_id():
    """The contract. Before FR-26 these were two different uuids on every request."""
    app, seen = _app_with_pipeline_route()

    with TestClient(app) as client:
        response = client.get("/enveloped")

    assert response.status_code == 200
    body = response.json()
    header_id = response.headers["X-Trace-ID"]
    assert body["trace_id"] == header_id, (
        f"body trace_id {body['trace_id']!r} != X-Trace-ID {header_id!r} — the pipeline "
        "minted a second id instead of adopting the middleware's"
    )
    assert seen["ctx_request_id"] == seen["state_trace_id"] == header_id


def test_the_pipeline_events_land_under_the_same_id():
    """The half that actually hurt: `execution.started/completed` were emitted under the
    pipeline's private id, so the body's trace resolved to a route that produced nothing."""
    from unittest.mock import MagicMock

    # events are emitted only when the context carries a db session; the spy below replaces
    # the emitter, so the session is never touched.
    app, _ = _app_with_pipeline_route(lambda: {"db": MagicMock()})
    emitted: list[str] = []

    def _spy(*args, **kwargs):
        emitted.append(kwargs.get("trace_id"))
        return None

    with patch("AINDY.core.system_event_service.emit_system_event", side_effect=_spy):
        with TestClient(app) as client:
            response = client.get("/enveloped")

    header_id = response.headers["X-Trace-ID"]
    assert emitted, "liveness: the pipeline emitted no events, so this proves nothing"
    assert set(emitted) == {header_id}, emitted


def test_an_explicit_metadata_trace_id_still_wins():
    """`execute_with_pipeline(metadata={"trace_id": …})` is the documented override; the
    default must not out-rank it."""
    app, seen = _app_with_pipeline_route(lambda: {"trace_id": "caller-chosen-trace"})

    with TestClient(app) as client:
        response = client.get("/enveloped")

    assert response.json()["trace_id"] == "caller-chosen-trace"
    assert seen["ctx_request_id"] == "caller-chosen-trace"


def test_a_client_sent_x_trace_id_does_not_become_the_pipeline_id_under_the_middleware():
    """The trust boundary the app explicitly did NOT ask us to cross. `log_requests` never
    honoured an incoming `X-Trace-ID`; before this fix the PIPELINE quietly did, so a client
    could choose the id half its request was recorded under. Now both halves use the
    middleware's."""
    app, seen = _app_with_pipeline_route()

    with TestClient(app) as client:
        response = client.get("/enveloped", headers={"X-Trace-ID": "client-chosen"})

    assert response.headers["X-Trace-ID"] != "client-chosen"
    assert response.json()["trace_id"] == response.headers["X-Trace-ID"]
    assert seen["ctx_request_id"] != "client-chosen"


class TestFromRequestOutsideTheMiddleware:
    """A Request that never passed through `log_requests` has no `state.trace_id`; the
    header fallbacks keep their old meaning there (a mounted app, a harness)."""

    @staticmethod
    def _bare_request(headers: dict[str, str]) -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        return Request(scope)

    def test_x_trace_id_header_is_honoured(self):
        ctx = ExecutionContext.from_request(self._bare_request({"X-Trace-ID": "hdr-1"}), "r")
        assert ctx.request_id == "hdr-1"

    def test_x_request_id_is_the_second_fallback(self):
        ctx = ExecutionContext.from_request(self._bare_request({"X-Request-ID": "req-1"}), "r")
        assert ctx.request_id == "req-1"

    def test_state_outranks_the_header_when_both_exist(self):
        request = self._bare_request({"X-Trace-ID": "hdr-1"})
        request.state.trace_id = "mw-1"
        assert ExecutionContext.from_request(request, "r").request_id == "mw-1"

    def test_no_headers_and_no_state_still_mints(self):
        a = ExecutionContext.from_request(self._bare_request({}), "r").request_id
        b = ExecutionContext.from_request(self._bare_request({}), "r").request_id
        assert a and b and a != b

    def test_no_request_at_all_still_mints(self):
        a = ExecutionContext.from_request(None, "r").request_id
        b = ExecutionContext.from_request(None, "r").request_id
        assert a and b and a != b
