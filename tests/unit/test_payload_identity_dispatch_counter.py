"""PAYLOAD-IDENTITY-DISPATCH-1 / DEC-103 — the dispatcher REFUSES a payload `user_id` that differs
from the caller, and still counts it.

The transports with an outside writer refuse before dispatch (DEC-099, FR-53). What reaches the
dispatcher with a mismatch is in-process code. DEC-100 counted it first:
`aindy_payload_identity_mismatch_total{syscall}` read 0 over 2,289 app dispatches across two windows.
So the dispatcher now refuses (`TENANT_VIOLATION`, `failure_class: permission`) and still counts
the mismatch. `AINDY_PAYLOAD_IDENTITY_DISPATCH=observe` restores DEC-100's count-only behaviour.
Every test here drives the REAL dispatcher with a temporary syscall registered in the kernel
registry.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only

PROBE = "sys.v1.payloadidprobe.echo"
ENV = "AINDY_PAYLOAD_IDENTITY_DISPATCH"


def _count(syscall: str = PROBE) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(
        "aindy_payload_identity_mismatch_total", {"syscall": syscall}
    ) or 0.0


@pytest.fixture
def probe(monkeypatch):
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    monkeypatch.delenv(ENV, raising=False)
    seen: list[dict] = []

    def _handler(payload, context):
        seen.append(dict(payload))
        return {"echo": payload.get("user_id")}

    register_syscall(PROBE, _handler, capability="payloadidprobe.echo")
    try:
        yield seen
    finally:
        SYSCALL_REGISTRY.pop(PROBE, None)


def _dispatch(payload: dict, caller: str) -> dict:
    from AINDY.kernel.syscall_dispatcher import get_dispatcher, make_syscall_ctx_from_tool

    ctx = make_syscall_ctx_from_tool(caller, capabilities=["payloadidprobe.echo"])
    return get_dispatcher().dispatch(PROBE, payload, ctx)


def test_a_mismatch_is_refused_by_default_and_still_counted(probe):
    caller, other = str(uuid.uuid4()), str(uuid.uuid4())
    before = _count()
    result = _dispatch({"user_id": other}, caller)
    assert result["status"] == "error", result
    assert result["error"].startswith("TENANT_VIOLATION"), result
    assert result.get("failure_class") == "permission", result
    assert probe == [], "refused before the handler ran"
    assert _count() == before + 1


def test_the_callers_own_id_and_an_absent_id_run_and_are_not_counted(probe):
    # The liveness control for the refusal above: the same dispatch path, no mismatch.
    caller = str(uuid.uuid4())
    before = _count()
    assert _dispatch({"user_id": caller.upper()}, caller)["status"] == "success"
    assert _dispatch({"user_id": ""}, caller)["status"] == "success"
    assert _dispatch({"q": 1}, caller)["status"] == "success"
    assert len(probe) == 3
    assert _count() == before


@pytest.mark.parametrize("value", ["", "refuse", "REFUSE", "obsrve", "off", "0"])
def test_only_observe_turns_refusal_off(probe, monkeypatch, value):
    # A typo or any other value must not open the gate.
    monkeypatch.setenv(ENV, value)
    result = _dispatch({"user_id": str(uuid.uuid4())}, str(uuid.uuid4()))
    assert result["status"] == "error", (value, result)
    assert probe == []


def test_observe_restores_count_only(probe, monkeypatch):
    monkeypatch.setenv(ENV, " Observe ")
    caller, other = str(uuid.uuid4()), str(uuid.uuid4())
    before = _count()
    result = _dispatch({"user_id": other}, caller)
    assert result["status"] == "success", result
    assert probe == [{"user_id": other}], "observed, not refused — the handler ran unchanged"
    assert _count() == before + 1


def test_a_metrics_failure_never_changes_the_decision(probe, monkeypatch):
    from AINDY.platform_layer import metrics

    class _Broken:
        def labels(self, **_):
            raise RuntimeError("metrics backend down")

    monkeypatch.setattr(metrics, "payload_identity_mismatch_total", _Broken())
    caller = str(uuid.uuid4())
    refused = _dispatch({"user_id": str(uuid.uuid4())}, caller)
    assert refused["status"] == "error" and refused["error"].startswith("TENANT_VIOLATION")
    monkeypatch.setenv(ENV, "observe")
    assert _dispatch({"user_id": str(uuid.uuid4())}, caller)["status"] == "success"
    assert len(probe) == 1


def test_a_comparison_that_raises_refuses(probe, monkeypatch):
    from AINDY.kernel import payload_identity

    def _boom(*_a, **_k):
        raise ValueError("unparseable")

    monkeypatch.setattr(payload_identity, "payload_user_id_mismatch", _boom)
    result = _dispatch({"user_id": "x"}, str(uuid.uuid4()))
    assert result["status"] == "error" and result["error"].startswith("TENANT_VIOLATION")
    assert probe == []
