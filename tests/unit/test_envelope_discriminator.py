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


class TestTheRuntimesOwnAdapters:
    """FR-45 — the runtime ships adapters whose body IS an envelope; they must say so.

    `adapt_response` stamps only its default exit, and correctly declines to vouch for an
    adapter's shape. But four of the adapters apps register are the runtime's own, and three
    return a body ui-kit resolves as an envelope. Unstamped, ui-kit 2.1.0's latch unwrapped
    them before the first stamped response of a session and not after it: the app found 26
    of its 78 parameterless GETs blanking depending on which page was opened first.

    The rule is ui-kit's: a stamped body resolves to ``body["data"]``, so the header goes on
    exactly the bodies that carry a top-level ``data`` key.
    """

    STAMPED = {"legacy_envelope_adapter", "raw_canonical_adapter", "memory_execute_adapter",
               "memory_completion_adapter"}
    NEVER_STAMPED = {"raw_json_adapter"}

    @staticmethod
    def _call(adapter, canonical):
        return adapter(route_name="apps.demo.x", canonical=canonical, status_code=200,
                       trace_headers={"X-Trace-ID": "trace-1"})

    def test_every_adapter_the_module_ships_is_classified(self):
        """Derived, not listed: a fifth adapter must be decided here, not default silently."""
        import inspect

        from AINDY.platform_layer import response_adapters

        shipped = {
            name for name, fn in inspect.getmembers(response_adapters, inspect.isfunction)
            if name.endswith("_adapter") and fn.__module__ == response_adapters.__name__
        }
        assert shipped, "census found no adapters: the derivation is broken, not the module"
        assert shipped == self.STAMPED | self.NEVER_STAMPED

    @pytest.mark.parametrize("name", ["raw_canonical_adapter", "memory_completion_adapter"])
    def test_the_canonical_envelope_verbatim_is_stamped(self, name):
        from AINDY.platform_layer import response_adapters

        response = self._call(getattr(response_adapters, name), _canonical())
        assert response.headers[ENVELOPE_HEADER] == ENVELOPE_VERSION
        assert json.loads(bytes(response.body))["data"] == {"items": []}
        assert response.headers["X-Trace-ID"] == "trace-1"

    def test_the_legacy_envelope_is_stamped(self):
        from AINDY.platform_layer.response_adapters import legacy_envelope_adapter

        response = self._call(legacy_envelope_adapter, _canonical())
        body = json.loads(bytes(response.body))
        assert response.headers[ENVELOPE_HEADER] == ENVELOPE_VERSION
        assert body["data"] == {"items": []}

    def test_the_legacy_passthrough_of_a_non_envelope_is_not(self):
        """The payload is passed through verbatim when it carries status + trace_id. With no
        ``data`` in it, ui-kit would resolve a stamped body to null: a blank surface."""
        from AINDY.platform_layer.response_adapters import legacy_envelope_adapter

        payload = {"status": "ok", "trace_id": "t", "items": [1]}
        response = self._call(legacy_envelope_adapter, _canonical(data=payload))
        assert json.loads(bytes(response.body)) == payload
        assert ENVELOPE_HEADER not in response.headers

    def test_the_memory_execute_merge_is_stamped_and_resolves_to_the_payload(self):
        from AINDY.platform_layer.response_adapters import memory_execute_adapter

        response = self._call(memory_execute_adapter, _canonical(data={"node_id": "n1"}))
        body = json.loads(bytes(response.body))
        assert response.headers[ENVELOPE_HEADER] == ENVELOPE_VERSION
        assert body["data"] == {"node_id": "n1"}
        assert body["node_id"] == "n1", "the flat keys older clients read are kept"

    def test_raw_json_and_the_completion_error_body_are_not(self):
        from AINDY.platform_layer.response_adapters import (
            memory_completion_adapter,
            raw_json_adapter,
        )

        # Liveness control: raw_json's body here DOES carry `data`, so only the adapter's
        # decision (not the helper's shape test) keeps the header off.
        bare = self._call(raw_json_adapter, _canonical(data={"data": [1]}))
        assert ENVELOPE_HEADER not in bare.headers
        assert json.loads(bytes(bare.body)) == {"data": [1]}

        error = self._call(memory_completion_adapter, _canonical(
            status="error", metadata={"status_code": 409, "error": "conflict"}))
        assert error.status_code == 409
        assert ENVELOPE_HEADER not in error.headers

    def test_the_caller_trace_headers_dict_is_not_mutated(self):
        """The helper adds the header to a copy; `trace_headers` is built once per call and
        a shared dict would leak the stamp into a later unstamped response."""
        from AINDY.platform_layer.response_adapters import raw_canonical_adapter

        trace_headers = {"X-Trace-ID": "trace-1"}
        raw_canonical_adapter(route_name="r", canonical=_canonical(), status_code=200,
                              trace_headers=trace_headers)
        assert trace_headers == {"X-Trace-ID": "trace-1"}

    def test_through_a_route_that_registers_one(self, monkeypatch):
        """The path the app hit: a pipeline route whose adapter is the runtime's own."""
        from AINDY.platform_layer.response_adapters import raw_canonical_adapter

        monkeypatch.setattr(
            "AINDY.core.response_adapter.get_response_adapter",
            lambda name: raw_canonical_adapter if name == "apps.demo.analytics" else None,
        )
        router = APIRouter()

        @router.get("/analytics")
        async def analytics(request: Request):
            async def handler(ctx):
                return {"rows": [1, 2]}

            return await execute_with_pipeline(
                request=request, route_name="apps.demo.analytics", handler=handler
            )

        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/analytics")

        assert response.status_code == 200
        assert response.headers.get(ENVELOPE_HEADER) == ENVELOPE_VERSION
        assert response.json()["data"]["rows"] == [1, 2]
