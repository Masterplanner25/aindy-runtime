"""FR-1 part 3 — shared outbound HTTP client: retry + circuit-breaking + authorization.

Patches ``httpx.request`` (the only real network seam) and ``perform_external_call`` (the
observability wrapper) so the retry loop, per-service circuit breaker, and the fact that
requests flow through the authorized boundary are all exercised without a network or DB.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from AINDY.agents.capability_policy import (
    CapabilityPolicy,
    clear_capability_policies,
    register_capability_policy,
)
from AINDY.kernel.circuit_breaker import CircuitOpenError
from AINDY.platform_layer.external_call_service import OutboundCallDenied
from AINDY.platform_layer.outbound_http import (
    TransientHTTPError,
    outbound_request,
    reset_circuit_breakers,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean():
    reset_circuit_breakers()
    clear_capability_policies()
    yield
    reset_circuit_breakers()
    clear_capability_policies()


def _passthrough():
    def _fake(*, service_name, operation, **kwargs):
        return operation()

    return patch(
        "AINDY.platform_layer.external_call_service.perform_external_call",
        side_effect=_fake,
    )


def test_retries_transport_error_then_succeeds():
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)

    with _passthrough(), patch("httpx.request", side_effect=fake_request):
        resp = outbound_request(
            "GET", "https://api.example.com/v1",
            service_name="ex", capability="outbound.ex",
            max_retries=2, backoff_base=0,
        )
    assert resp.status_code == 200
    assert calls["n"] == 2  # one failure + one success


def test_retryable_status_exhausts_and_raises():
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(503)

    with _passthrough(), patch("httpx.request", side_effect=fake_request):
        with pytest.raises(TransientHTTPError):
            outbound_request(
                "POST", "https://api.example.com/v1",
                service_name="ex", capability="outbound.ex",
                max_retries=1, backoff_base=0,
            )
    assert calls["n"] == 2  # initial + one retry


def test_circuit_opens_after_repeated_failures():
    def fake_request(method, url, **kwargs):
        raise httpx.ConnectError("down")

    with _passthrough(), patch("httpx.request", side_effect=fake_request):
        # Default breaker failure_threshold is 3; each no-retry call is one failure.
        for _ in range(3):
            with pytest.raises(TransientHTTPError):
                outbound_request(
                    "GET", "https://api.down.com/x",
                    service_name="down-svc", capability="outbound.down",
                    max_retries=0, backoff_base=0,
                )
        # Circuit is now open — the next call fails fast, before httpx is touched.
        with pytest.raises(CircuitOpenError):
            outbound_request(
                "GET", "https://api.down.com/x",
                service_name="down-svc", capability="outbound.down",
                max_retries=0, backoff_base=0,
            )


def test_url_host_is_domain_enforced():
    register_capability_policy("outbound.hook", CapabilityPolicy(domains=("allowed.com",)))
    touched = {"http": False}

    def fake_request(method, url, **kwargs):
        touched["http"] = True
        return httpx.Response(200)

    with _passthrough(), patch("httpx.request", side_effect=fake_request):
        with pytest.raises(OutboundCallDenied):
            outbound_request(
                "POST", "https://evil.com/steal",
                service_name="hook", capability="outbound.hook",
                max_retries=0, backoff_base=0,
            )
    assert touched["http"] is False  # denied before any network I/O
