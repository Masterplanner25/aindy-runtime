"""
Standard HTTP response adapter functions for domain bootstraps.

These adapters convert the canonical execution envelope format into
FastAPI JSONResponse objects. Register them via:
    register_response_adapter("route_name", adapter_fn)
"""
from __future__ import annotations


def _envelope_json(body, *, status_code, trace_headers):
    """A JSONResponse that carries `X-AINDY-Envelope` exactly when ``body`` is an envelope.

    FR-45: these adapters returned envelope-shaped bodies without the header, because
    `adapt_response` stamps its own default exit only. ui-kit 2.1.0 (FR-37) latches after the
    first stamped response and treats every unstamped body as bare from then on, so the same
    route's envelope was unwrapped or not depending on which page the session opened first.

    The test is the one ui-kit applies to a stamped body: it resolves it to ``body["data"]``.
    A body without a top-level ``data`` key (the legacy adapter's passthrough of a payload
    that is not an envelope, `raw_json_adapter`, an ``{error, details}`` body) must stay
    unstamped. An over-claiming header would make the client unwrap a plain body.
    """
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    from AINDY.core.response_adapter import ENVELOPE_HEADER, ENVELOPE_VERSION

    headers = dict(trace_headers or {})
    if isinstance(body, dict) and "data" in body:
        headers[ENVELOPE_HEADER] = ENVELOPE_VERSION
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body), headers=headers)


def raw_json_adapter(*, route_name, canonical, status_code, trace_headers):
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(canonical.get("data")),
        headers=trace_headers,
    )


def legacy_envelope_adapter(*, route_name, canonical, status_code, trace_headers):
    from AINDY.core.execution_envelope import success as legacy_success

    payload = canonical.get("data")
    if isinstance(payload, dict) and "status" in payload and "trace_id" in payload:
        body = payload
    else:
        body = legacy_success(
            payload,
            canonical.get("metadata", {}).get("events") or [],
            str(canonical.get("trace_id") or ""),
            next_action=canonical.get("metadata", {}).get("next_action"),
        )
    return _envelope_json(body, status_code=status_code, trace_headers=trace_headers)


def raw_canonical_adapter(*, route_name, canonical, status_code, trace_headers):
    return _envelope_json(canonical, status_code=status_code, trace_headers=trace_headers)


def memory_execute_adapter(*, route_name, canonical, status_code, trace_headers):
    payload = canonical.get("data")
    if not isinstance(payload, dict):
        return raw_canonical_adapter(
            route_name=route_name,
            canonical=canonical,
            status_code=status_code,
            trace_headers=trace_headers,
        )
    merged = dict(payload)
    merged["status"] = canonical.get("status")
    merged["trace_id"] = canonical.get("trace_id")
    merged["data"] = payload
    metadata = canonical.get("metadata", {})
    if metadata.get("events") is not None:
        merged["events"] = metadata.get("events")
    if metadata.get("next_action") is not None:
        merged["next_action"] = metadata.get("next_action")
    return _envelope_json(merged, status_code=status_code, trace_headers=trace_headers)


def memory_completion_adapter(*, route_name, canonical, status_code, trace_headers):
    if canonical.get("status") == "error":
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import JSONResponse

        error_status = canonical.get("metadata", {}).get("status_code") or status_code
        detail = canonical.get("metadata", {}).get("error", "Execution failed")
        return JSONResponse(
            status_code=int(error_status),
            content={"error": "http_error", "details": jsonable_encoder(detail)},
            headers=trace_headers,
        )
    return raw_canonical_adapter(
        route_name=route_name,
        canonical=canonical,
        status_code=status_code,
        trace_headers=trace_headers,
    )
