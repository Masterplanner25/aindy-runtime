"""
platform_layer/outbound_http.py - Shared outbound HTTP client (FR-1, part 3).

A single retry + circuit-breaking HTTP entry point for connectors, replacing app-side
raw ``urllib``. Every request is routed through
:func:`AINDY.platform_layer.external_call_service.authorized_external_call`, so it inherits
the capability recipient/domain allowlist, rate limit, socket-level egress guard, credential
scope, and ``external.call.*`` observability — the connector gets authorization + resilience
from one call.

Resilience:
  - **Retry with exponential backoff** on transport errors and retryable statuses
    (408/429/500/502/503/504), bounded by ``max_retries``.
  - **Circuit breaker per service** (``AINDY.kernel.circuit_breaker.CircuitBreaker``): once
    a service trips ``failure_threshold`` consecutive failures the circuit opens and further
    calls fail fast with ``CircuitOpenError`` until the recovery timeout elapses.

Authorization is enforced once per logical request (outside the retry loop) so a retried
request costs exactly one rate-limit hit.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from AINDY.kernel.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


class TransientHTTPError(Exception):
    """A retryable HTTP failure (transport error or a retryable status code)."""


def _breaker_for(service_name: str) -> CircuitBreaker:
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(service_name)
        if breaker is None:
            breaker = CircuitBreaker(name=f"outbound:{service_name}")
            _BREAKERS[service_name] = breaker
        return breaker


def reset_circuit_breakers() -> None:
    """Test helper — drop all per-service breakers."""
    with _BREAKERS_LOCK:
        _BREAKERS.clear()


def outbound_request(
    method: str,
    url: str,
    *,
    service_name: str,
    capability: str,
    user_id: str | None = None,
    db=None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
    data: Any = None,
    timeout: float = 10.0,
    max_retries: int = 2,
    backoff_base: float = 0.2,
    backoff_cap: float = 5.0,
):
    """Issue an authorized, retried, circuit-broken HTTP request; return ``httpx.Response``.

    Raises :class:`AINDY.platform_layer.external_call_service.OutboundCallDenied` when the
    call is denied by policy/rate before it runs, ``CircuitOpenError`` when the service's
    breaker is open, or the last transport error / :class:`TransientHTTPError` when retries
    are exhausted.
    """
    import httpx

    from AINDY.platform_layer.external_call_service import authorized_external_call

    breaker = _breaker_for(service_name)

    def _send() -> "httpx.Response":
        try:
            resp = httpx.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                json=json,
                data=data,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise TransientHTTPError(f"transport error: {exc}") from exc
        if resp.status_code in _RETRYABLE_STATUS:
            raise TransientHTTPError(f"retryable status {resp.status_code}")
        return resp

    def _operation() -> "httpx.Response":
        attempt = 0
        while True:
            try:
                return breaker.call(_send)
            except TransientHTTPError as exc:
                if attempt >= max_retries:
                    logger.warning(
                        "[outbound_http:%s] giving up after %d attempt(s): %s",
                        service_name, attempt + 1, exc,
                    )
                    raise
                delay = min(backoff_base * (2 ** attempt), backoff_cap)
                logger.info(
                    "[outbound_http:%s] attempt %d failed (%s); retrying in %.2fs",
                    service_name, attempt + 1, exc, delay,
                )
                time.sleep(delay)
                attempt += 1

    return authorized_external_call(
        service_name=service_name,
        capability=capability,
        operation=_operation,
        # Domain enforcement extracts hosts from the action; the URL is what we're calling.
        action={"url": url},
        user_id=user_id,
        db=db,
        endpoint=url,
        method=method.upper(),
    )
