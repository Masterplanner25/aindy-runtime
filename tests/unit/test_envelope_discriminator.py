"""FR-19 — a response must say whether its body is the execution envelope.

Only routes that go through `ExecutionPipeline` return `{status, data, trace_id, …}`;
everything else returns a bare body, and both share the `/apps/*` URL space. Nothing on
the wire told them apart, so every consumer carried per-route knowledge of whether that
route happened to enter a pipeline. The app team's client did not: 3 of 11 modules
unwrapped, 8 did not, and they fixed the resulting defect eleven times without ever
asking whether the contract could answer the question.

The failure signature is why it was expensive rather than merely annoying: an envelope
where a list was expected has no `.length`, so the empty-state branch does not fire
either and the surface renders **blank, with no error at all**.

What these assert:

* the header appears exactly on the exit that returns the canonical envelope;
* it does **not** appear on the exits that return something else — an app-registered
  adapter's shape, a bare `{detail}` error, a handler-built Response. A discriminator
  that over-claims is worse than none, because a client would unwrap a plain body;
* it survives the round trip through a real route, not just a direct call;
* it is exposed through CORS, without which a browser client on another origin cannot
  read it and the whole mechanism is invisible to the consumer it exists for.
"""
from __future__ import annotations

import json

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.core.response_adapter import ENVELOPE_HEADER, ENVELOPE_VERSION, adapt_response

pytestmark = pytest.mark.runtime_only


def _canonical(**overrides):
    payload = {
        "status": "success",
        "data": {"items": []},
        "trace_id": "trace-1",
        "duration_ms": 12,
    }
    payload.update(overrides)
    return payload


class TestTheEnvelopeExit:
    def test_the_default_exit_marks_the_response(self):
        response = adapt_response("apps.demo.list", _canonical())
        assert response.headers[ENVELOPE_HEADER] == ENVELOPE_VERSION

    def test_the_trace_headers_still_ride_along(self):
        """Regression: the header was added by rebuilding the headers dict."""
        response = adapt_response("apps.demo.list", _canonical(eu_id="eu-7"))
        assert response.headers["X-Trace-ID"] == "trace-1"
        assert response.headers["X-EU-ID"] == "eu-7"


class TestTheExitsThatMustNotClaimIt:
    """A discriminator that over-claims makes a client unwrap a plain body."""

    def test_an_error_response_is_not_an_envelope(self):
        canonical = _canonical(status="error", metadata={"status_code": 404, "error": "nope"})
        response = adapt_response("apps.demo.get", canonical)
        assert response.status_code == 404
        assert ENVELOPE_HEADER not in response.headers

    def test_a_handler_built_response_is_returned_untouched(self):
        built = JSONResponse(status_code=204, content=None)
        response = adapt_response("apps.demo.delete", _canonical(data=built))
        assert response is built
        assert ENVELOPE_HEADER not in response.headers

    def test_a_registered_adapter_decides_its_own_shape(self, monkeypatch):
        def _adapter(*, route_name, canonical, status_code, trace_headers):
            return JSONResponse(status_code=status_code, content={"bare": True}, headers=trace_headers)

        monkeypatch.setattr(
            "AINDY.core.response_adapter.get_response_adapter",
            lambda name: _adapter if name == "apps.demo.custom" else None,
        )
        response = adapt_response("apps.demo.custom", _canonical())
        assert json.loads(bytes(response.body)) == {"bare": True}
        assert ENVELOPE_HEADER not in response.headers, (
            "an adapter's shape is not the envelope; claiming it would be a lie on the wire"
        )


class TestThroughTheRoute:
    """A route test must call the route — the header has to survive the full stack."""

    def test_a_pipeline_route_answers_with_the_marker(self):
        router = APIRouter()

        @router.get("/enveloped")
        async def enveloped(request: Request):
            async def handler(ctx):
                return {"items": [1, 2]}

            return await execute_with_pipeline(
                request=request, route_name="apps.demo.enveloped", handler=handler
            )

        @router.get("/bare")
        def bare(request: Request):
            return {"items": [1, 2]}

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            enveloped_response = client.get("/enveloped")
            bare_response = client.get("/bare")

        assert enveloped_response.headers.get(ENVELOPE_HEADER) == ENVELOPE_VERSION
        assert "status" in enveloped_response.json()
        # The whole point: the same client can now tell these apart without knowing
        # which route entered a pipeline.
        assert ENVELOPE_HEADER not in bare_response.headers
        assert bare_response.json() == {"items": [1, 2]}


class TestItIsReadableCrossOrigin:
    """Without this the mechanism is invisible to the consumer it exists for.

    `allow_headers` governs the REQUEST direction. A browser exposes only the CORS
    safelist to page JavaScript unless the server names the rest in
    `Access-Control-Expose-Headers` — so a Vite dev server on :5173 talking to :8000
    could not read the discriminator, and has never been able to read `X-Trace-ID`.
    """

    def test_the_runtime_exposes_its_own_headers(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")

        from AINDY.middleware import register_middleware

        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        register_middleware(app)

        with TestClient(app) as client:
            response = client.get("/ping", headers={"Origin": "http://localhost:5173"})

        exposed = {
            value.strip()
            for value in response.headers.get("access-control-expose-headers", "").split(",")
            if value.strip()
        }
        assert ENVELOPE_HEADER in exposed
        assert "X-Trace-ID" in exposed
