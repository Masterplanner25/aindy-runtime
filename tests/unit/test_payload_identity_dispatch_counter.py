"""PAYLOAD-IDENTITY-DISPATCH-1 / DEC-100 — the dispatcher OBSERVES a payload `user_id` that differs
from the caller, and never refuses it (yet).

The transports with an outside writer refuse before dispatch (DEC-099, FR-53). What reaches the
dispatcher with a mismatch is in-process code, and the ~60 app call sites have not been audited.
So the dispatcher counts `aindy_payload_identity_mismatch_total{syscall}` and lets the call run;
refusing waits on that counter reading zero across a window. Every test here drives the REAL
dispatcher with a temporary syscall registered in the kernel registry.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.runtime_only

PROBE = "sys.v1.payloadidprobe.echo"


def _count(syscall: str = PROBE) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    return REGISTRY.get_sample_value(
        "aindy_payload_identity_mismatch_total", {"syscall": syscall}
    ) or 0.0


@pytest.fixture
def probe():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

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


def test_a_mismatch_is_counted_and_the_syscall_still_runs(probe):
    caller, other = str(uuid.uuid4()), str(uuid.uuid4())
    before = _count()
    result = _dispatch({"user_id": other}, caller)
    assert result["status"] == "success", result
    assert probe == [{"user_id": other}], "observed, not refused — the handler ran unchanged"
    assert _count() == before + 1


def test_the_callers_own_id_and_an_absent_id_are_not_counted(probe):
    # The liveness control for the count above: the same dispatch path, no mismatch.
    caller = str(uuid.uuid4())
    before = _count()
    assert _dispatch({"user_id": caller.upper()}, caller)["status"] == "success"
    assert _dispatch({"q": 1}, caller)["status"] == "success"
    assert len(probe) == 2
    assert _count() == before


def test_a_metrics_failure_never_changes_whether_the_syscall_runs(probe, monkeypatch):
    from AINDY.platform_layer import metrics

    class _Broken:
        def labels(self, **_):
            raise RuntimeError("metrics backend down")

    monkeypatch.setattr(metrics, "payload_identity_mismatch_total", _Broken())
    result = _dispatch({"user_id": str(uuid.uuid4())}, str(uuid.uuid4()))
    assert result["status"] == "success", result
    assert len(probe) == 1
