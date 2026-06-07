"""
Kernel hardening — syscall dispatch contract fuzz.

SyscallDispatcher.dispatch() must always return the standard envelope and
never raise (except SyscallContractViolation on EXACTLY_ONCE non-dict returns).
Covers: unknown syscalls, malformed names, missing caps, missing user_id,
crashing handlers, non-dict returns (AT_LEAST_ONCE), and adversarial payloads.
"""
from __future__ import annotations

import pytest

from AINDY.kernel.syscall_dispatcher import SyscallContext, SyscallDispatcher
from AINDY.kernel import syscall_registry

pytestmark = pytest.mark.runtime_only

_ENVELOPE_KEYS = frozenset({
    "status", "data", "trace_id", "execution_unit_id",
    "syscall", "version", "duration_ms", "error", "warning",
})


@pytest.fixture
def dispatcher():
    d = SyscallDispatcher()
    d._emit_syscall_event = lambda *a, **kw: None
    return d


def _ctx(*, user_id="user-fuzz", caps=None, eu_id="eu-fuzz", trace_id="trace-fuzz"):
    return SyscallContext(
        execution_unit_id=eu_id,
        user_id=user_id,
        capabilities=caps if caps is not None else ["test.fuzz"],
        trace_id=trace_id,
    )


def _ok_handler(payload, ctx):
    return {"result": "ok"}


def _crash_handler(payload, ctx):
    raise RuntimeError("boom")


def _nondict_handler(payload, ctx):
    return "not a dict"


@pytest.fixture(autouse=True)
def _register_fuzz_syscalls():
    syscall_registry.SYSCALL_REGISTRY["sys.v1.test.fuzz_ok"] = syscall_registry.SyscallEntry(
        handler=_ok_handler,
        capability="test.fuzz",
    )
    syscall_registry.SYSCALL_REGISTRY["sys.v1.test.fuzz_crash"] = syscall_registry.SyscallEntry(
        handler=_crash_handler,
        capability="test.fuzz",
    )
    syscall_registry.SYSCALL_REGISTRY["sys.v1.test.fuzz_nondict"] = syscall_registry.SyscallEntry(
        handler=_nondict_handler,
        capability="test.fuzz",
    )
    yield
    for k in ("sys.v1.test.fuzz_ok", "sys.v1.test.fuzz_crash", "sys.v1.test.fuzz_nondict"):
        syscall_registry.SYSCALL_REGISTRY.pop(k, None)


def _assert_envelope(result: object, *, label: str = "") -> None:
    tag = f"[{label}] " if label else ""
    assert isinstance(result, dict), f"{tag}expected dict, got {type(result)}"
    missing = _ENVELOPE_KEYS - result.keys()
    assert not missing, f"{tag}missing envelope keys: {missing}"
    assert result["status"] in ("success", "error"), f"{tag}bad status {result['status']!r}"
    assert isinstance(result["data"], dict), f"{tag}data must be dict, got {type(result['data'])}"
    assert isinstance(result["duration_ms"], int) and result["duration_ms"] >= 0
    if result["status"] == "success":
        assert result["error"] is None, f"{tag}error must be None on success"
    else:
        assert isinstance(result["error"], str), f"{tag}error must be str on failure"


# ---------------------------------------------------------------------------
# Envelope shape invariants
# ---------------------------------------------------------------------------

def test_unknown_syscall_returns_envelope(dispatcher):
    result = dispatcher.dispatch("sys.v1.test.does_not_exist", {}, _ctx())
    _assert_envelope(result, label="unknown_syscall")
    assert result["status"] == "error"
    assert "Unknown syscall" in result["error"]


@pytest.mark.parametrize("bad_name", [
    "",
    "notasyscall",
    "sys.",
    "sys.v1.",
    "sys.v1.x",
    "!@#$%",
    "sys.v99.test.action",
])
def test_malformed_syscall_name_returns_envelope(dispatcher, bad_name):
    result = dispatcher.dispatch(bad_name, {}, _ctx())
    _assert_envelope(result, label=f"bad_name={bad_name!r}")
    assert result["status"] == "error"


def test_missing_capability_returns_permission_denied(dispatcher):
    result = dispatcher.dispatch("sys.v1.test.fuzz_ok", {}, _ctx(caps=["wrong.cap"]))
    _assert_envelope(result, label="missing_cap")
    assert result["status"] == "error"
    assert "Permission denied" in result["error"]


def test_missing_user_id_returns_tenant_violation(dispatcher):
    result = dispatcher.dispatch("sys.v1.test.fuzz_ok", {}, _ctx(user_id=""))
    _assert_envelope(result, label="missing_user_id")
    assert result["status"] == "error"
    assert "TENANT_VIOLATION" in result["error"]


def test_crashing_handler_returns_error_envelope(dispatcher):
    result = dispatcher.dispatch("sys.v1.test.fuzz_crash", {}, _ctx())
    _assert_envelope(result, label="crash")
    assert result["status"] == "error"
    assert "boom" in result["error"]


def test_non_dict_handler_return_gives_error_envelope_not_raise(dispatcher):
    # AT_LEAST_ONCE path (no EU DB row) → error envelope, not SyscallContractViolation
    result = dispatcher.dispatch("sys.v1.test.fuzz_nondict", {}, _ctx())
    _assert_envelope(result, label="nondict")
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Adversarial payloads — envelope shape must hold regardless
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    None,
    [],
    42,
    "a string",
    b"bytes",
    {"deeply": {"nested": {"garbage": [None, {}, "x"] * 5}}},
    {str(i): i for i in range(60)},
    {"key with spaces": True, "": None},
])
def test_adversarial_payload_always_returns_envelope(dispatcher, payload):
    result = dispatcher.dispatch("sys.v1.test.fuzz_ok", payload, _ctx())
    _assert_envelope(result, label=f"payload={type(payload).__name__}")
    # success or error is fine — what matters is envelope shape never breaks


# ---------------------------------------------------------------------------
# Success path and identity propagation
# ---------------------------------------------------------------------------

def test_successful_dispatch_returns_correct_envelope(dispatcher):
    result = dispatcher.dispatch("sys.v1.test.fuzz_ok", {}, _ctx())
    _assert_envelope(result, label="success")
    assert result["status"] == "success"
    assert result["data"] == {"result": "ok"}
    assert result["error"] is None
    assert result["syscall"] == "sys.v1.test.fuzz_ok"
    assert result["version"] == "v1"


def test_trace_and_eu_id_propagate_into_envelope(dispatcher):
    result = dispatcher.dispatch(
        "sys.v1.test.fuzz_ok", {},
        _ctx(eu_id="eu-abc", trace_id="trace-xyz"),
    )
    _assert_envelope(result, label="propagation")
    assert result["trace_id"] == "trace-xyz"
    assert result["execution_unit_id"] == "eu-abc"
