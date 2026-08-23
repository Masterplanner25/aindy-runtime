from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from AINDY.platform_layer.registry import get_response_adapter


#: FR-19 — the discriminator. Present exactly on responses whose body IS the canonical
#: execution envelope (`{status, data, trace_id, duration_ms, …}`).
#:
#: Why a header rather than a body marker: the envelope's own keys cannot answer the
#: question. A bare response may legitimately carry `status` or `data`, which is why the
#: app team could not apply a blanket unwrap — doing so corrupts plain bodies. A header
#: is additive, changes no shape, and lets one client helper branch where eleven modules
#: previously each carried per-route knowledge of whether that route happened to enter a
#: pipeline. That knowledge was unobtainable except by trying: the failure signature is a
#: blank surface, because an envelope has no `.length` so the empty-state branch does not
#: fire either.
#:
#: ★ `X-Trace-ID` cannot serve this purpose — `log_requests` sets it on EVERY response.
ENVELOPE_HEADER = "X-AINDY-Envelope"
ENVELOPE_VERSION = "v1"


def _legacy_error_response(canonical: dict[str, Any], *, status_code: int) -> JSONResponse:
    error_status = canonical.get("metadata", {}).get("status_code") or status_code
    return JSONResponse(
        status_code=int(error_status),
        content={"detail": jsonable_encoder(canonical.get("metadata", {}).get("error", "Execution failed"))},
        headers=_trace_headers(canonical),
    )


def _trace_headers(canonical: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    trace_id = str(canonical.get("trace_id") or "")
    if trace_id:
        headers["X-Trace-ID"] = trace_id
    eu_id = str(canonical.get("eu_id") or "")
    if eu_id:
        headers["X-EU-ID"] = eu_id
    return headers


def adapt_response(route_name: str, canonical: dict[str, Any], *, status_code: int = 200) -> Response:
    route_prefix = route_name.split(".", 1)[0]
    underscore_prefix = route_name.split("_", 1)[0]
    exact_adapter = get_response_adapter(route_name)

    if exact_adapter is not None:
        return exact_adapter(
            route_name=route_name,
            canonical=canonical,
            status_code=status_code,
            trace_headers=_trace_headers(canonical),
        )

    if canonical.get("status") == "error":
        return _legacy_error_response(canonical, status_code=status_code)

    payload = canonical.get("data")
    if isinstance(payload, Response):
        trace_id = str(canonical.get("trace_id") or "")
        if trace_id:
            payload.headers.setdefault("X-Trace-ID", trace_id)
        return payload

    adapter = (
        get_response_adapter(route_prefix)
        or get_response_adapter(underscore_prefix)
    )
    if adapter is not None:
        return adapter(
            route_name=route_name,
            canonical=canonical,
            status_code=status_code,
            trace_headers=_trace_headers(canonical),
        )

    # The only exit that returns the canonical envelope as the body, and therefore the
    # only one that may claim the header. Every branch above returns something else — an
    # app-registered adapter's shape, a bare `{detail}` error, or a Response the handler
    # built itself — and marking those would make the discriminator a lie, which is worse
    # than not having one.
    headers = _trace_headers(canonical)
    headers[ENVELOPE_HEADER] = ENVELOPE_VERSION
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(canonical),
        headers=headers,
    )

