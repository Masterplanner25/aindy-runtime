"""IDEM-11 — every syscall declares an execution guarantee, and the gate degrades safely.

Three things are pinned here.

1. **The audit result.** Each of the 23 built-in syscalls is classified as idempotent or not,
   and the classification is asserted per-name rather than by count — a count-only test passes
   when someone flips the wrong one. The audit found **6** syscalls that a retry would
   double-execute, against the 1 that was declared.

2. **`register_syscall` can express the guarantee at all.** It could not until 2026-08-15:
   `SyscallEntry` accepted `execution_guarantee`, `register_syscall` never forwarded it, so
   every plugin-registered syscall silently got `AT_LEAST_ONCE` with no way to opt in. The
   gate was unreachable for app syscalls *by construction*, not by configuration.

3. **A non-JSON-serializable result cannot break a syscall whose effect already happened.**
   The gate caches the handler's return in a JSONB column. That call sits outside every `try`
   in `dispatch()`, so before the guard a `UUID`/`datetime` return propagated out of
   `dispatch()` **after the effect had landed** — and only with the flag on, i.e. exactly when
   someone flips it. The tool path (MEB-0) already degraded gracefully; its syscall twin did
   not.

These declarations are **inert** unless `AINDY_SYSCALL_IDEMPOTENCY` is on or the run is a
durable continuation, so this suite asserts declarations and degradation, not dedup behaviour.
End-to-end dedup needs a real Postgres effect ledger and belongs in the integration suite.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.runtime_only


# Every retry of these produces a SECOND effect: a duplicate row, job, event or execution.
NON_IDEMPOTENT = {
    "sys.v1.memory.write",         # persists a duplicate MemoryNode
    "sys.v1.event.emit",           # writes a duplicate SystemEvent
    "sys.v1.flow.run",             # PersistentFlowRunner.start() creates a new FlowRun
    "sys.v1.flow.execute_intent",  # re-selects a strategy and starts a second flow
    "sys.v1.nodus.execute",        # re-executes arbitrary guest script source
    "sys.v1.job.submit",           # writes an AutomationLog and enqueues the job again
    "sys.v1.agent.undo",           # re-invokes every compensator (latent: 0 registered today)
}

# Repeating these converges on the same state, so AT_LEAST_ONCE is correct — not merely
# tolerated. Reads are omitted; they are asserted wholesale below.
IDEMPOTENT_WRITES = {
    "sys.v1.agent.cancel",             # CAS to a terminal status; terminal is a no-op
    "sys.v1.agent.ensure_initial_run",  # find-or-create by design
    "sys.v1.agent.simulate",           # overwrites one field, no status change, no real tools
    "sys.v1.memory.delete",            # delete-by-id: same end state either way
    "sys.v1.agent.execute",            # guarded by a status == "approved" precondition
}


def _registry():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    return SYSCALL_REGISTRY


def _guarantee(entry) -> str:
    return str(getattr(entry, "execution_guarantee", "")).upper()


# --------------------------------------------------------------------------------------
# 1. The audit
# --------------------------------------------------------------------------------------


def test_every_non_idempotent_syscall_declares_exactly_once():
    """Asserted per-name, not by count — a count-only test passes on the wrong six."""
    registry = _registry()
    wrong = {
        name: _guarantee(registry[name])
        for name in sorted(NON_IDEMPOTENT)
        if name in registry and _guarantee(registry[name]) != "EXACTLY_ONCE"
    }
    assert not wrong, (
        f"these syscalls double-execute on retry but do not declare EXACTLY_ONCE: {wrong}"
    )


def test_idempotent_syscalls_are_left_at_least_once():
    """The complement. Over-declaring is not free — it puts a ledger write on a hot path."""
    registry = _registry()
    wrong = {
        name: _guarantee(registry[name])
        for name in sorted(IDEMPOTENT_WRITES)
        if name in registry and _guarantee(registry[name]) != "AT_LEAST_ONCE"
    }
    assert not wrong, f"these converge on retry and should stay AT_LEAST_ONCE: {wrong}"


def test_read_syscalls_are_never_exactly_once():
    """A read has no effect to deduplicate; declaring one would be pure overhead."""
    registry = _registry()
    offenders = {
        name: _guarantee(entry)
        for name, entry in registry.items()
        if str(getattr(entry, "capability", "")).endswith(".read")
        and _guarantee(entry) == "EXACTLY_ONCE"
    }
    assert not offenders, f"read syscalls must not declare EXACTLY_ONCE: {offenders}"


def test_every_syscall_declares_a_valid_guarantee():
    """No syscall may carry a typo'd or empty guarantee — that silently reads as AT_LEAST_ONCE."""
    registry = _registry()
    bad = {
        name: _guarantee(entry)
        for name, entry in registry.items()
        if _guarantee(entry) not in {"AT_LEAST_ONCE", "EXACTLY_ONCE"}
    }
    assert not bad, f"invalid execution_guarantee values: {bad}"


# --------------------------------------------------------------------------------------
# 2. register_syscall can express it
# --------------------------------------------------------------------------------------


def test_register_syscall_forwards_execution_guarantee():
    """The gap this closes: the parameter did not exist, so plugins could never opt in."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    name = "sys.test.idem11.forwards"
    try:
        register_syscall(
            name, lambda p, c: {}, "test.cap", execution_guarantee="EXACTLY_ONCE"
        )
        assert _guarantee(SYSCALL_REGISTRY[name]) == "EXACTLY_ONCE"
    finally:
        SYSCALL_REGISTRY.pop(name, None)


def test_register_syscall_defaults_to_at_least_once():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    name = "sys.test.idem11.default"
    try:
        register_syscall(name, lambda p, c: {}, "test.cap")
        assert _guarantee(SYSCALL_REGISTRY[name]) == "AT_LEAST_ONCE"
    finally:
        SYSCALL_REGISTRY.pop(name, None)


def test_register_syscall_rejects_a_typo_rather_than_downgrading():
    """A silently-downgraded typo is indistinguishable from never having declared it."""
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    name = "sys.test.idem11.typo"
    try:
        with pytest.raises(ValueError, match="execution_guarantee must be one of"):
            register_syscall(
                name, lambda p, c: {}, "test.cap", execution_guarantee="EXACTLY ONCE"
            )
        assert name not in SYSCALL_REGISTRY
    finally:
        SYSCALL_REGISTRY.pop(name, None)


# --------------------------------------------------------------------------------------
# 3. The cached result cannot break a syscall whose effect already happened
# --------------------------------------------------------------------------------------


def test_declared_exactly_once_handlers_have_json_safe_static_returns():
    """Every EXACTLY_ONCE syscall's *declared* output schema must be JSON-expressible.

    This is a cheap structural check, not proof: it reads the declared schema, not a live
    return. The runtime guard below is what actually makes a violation non-fatal.
    """
    registry = _registry()
    for name, entry in sorted(registry.items()):
        if _guarantee(entry) != "EXACTLY_ONCE":
            continue
        schema = getattr(entry, "output_schema", None)
        if schema is None:
            continue
        json.dumps(schema)  # raises if the declaration itself is not expressible


@pytest.fixture
def _engaged_gate(monkeypatch):
    """Drive the real dispatcher with the idempotency gate genuinely engaged.

    All four gate conditions must hold or the code under test never runs, which would make
    these assertions vacuous — so the fixture sets the flag, registers an EXACTLY_ONCE
    syscall, and supplies a UUID execution-unit id. `_gate_engaged` below is the liveness
    control proving the gate actually fired.
    """
    import uuid

    from AINDY.kernel import syscall_dispatcher as sd
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, register_syscall

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "1")

    seen: dict = {"resolved": False, "cached": "<unset>"}

    def _fake_resolve(db, action_id, name, payload, **kw):
        seen["resolved"] = True
        return False, None  # not already done, no cached result

    def _fake_complete(db, action_id, status, result_payload):
        seen["cached"] = result_payload
        # Reproduce the real failure mode: JSONB rejects a raw UUID at commit time.
        json.dumps(result_payload)

    monkeypatch.setattr(sd, "_resolve_effect_record", _fake_resolve)
    monkeypatch.setattr(sd, "_complete_effect_record", _fake_complete)
    monkeypatch.setattr(sd, "SessionLocal", object, raising=False)

    class _FakeSession:
        """Faithful enough that a guard regression fails on the assertion, not on the fake.

        The dispatcher's gate-failure path rolls back and closes, so a session missing either
        method turns a real regression into an `AttributeError` — a test that fails for the
        wrong reason still fails, but it stops describing what broke.
        """

        def close(self):
            pass

        def rollback(self):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(
        "AINDY.db.database.SessionLocal", lambda *a, **k: _FakeSession(), raising=False
    )

    created = []

    def _register(name, handler):
        register_syscall(name, handler, "test.cap", execution_guarantee="EXACTLY_ONCE")
        created.append(name)
        return name

    yield {"register": _register, "seen": seen, "eu_id": str(uuid.uuid4())}

    for name in created:
        SYSCALL_REGISTRY.pop(name, None)


def _dispatch(name: str, eu_id: str):
    from AINDY.kernel.syscall_dispatcher import SyscallDispatcher, SyscallContext

    ctx = SyscallContext(
        user_id="idem11-test",
        capabilities=["test.cap"],
        trace_id="",
        execution_unit_id=eu_id,
    )
    return SyscallDispatcher().dispatch(name, {}, ctx)


def test_gate_engages_at_all(_engaged_gate):
    """LIVENESS CONTROL — without this the degradation test below proves nothing.

    If any of the four gate conditions silently stopped holding, a broken guard would look
    identical to a guard that was never reached.
    """
    name = _engaged_gate["register"](
        "sys.test.idem11.live", lambda p, c: {"ok": True}
    )
    envelope = _dispatch(name, _engaged_gate["eu_id"])

    assert envelope.get("status") == "success", envelope
    assert _engaged_gate["seen"]["resolved"] is True, (
        "the idempotency gate never fired — every assertion about it is vacuous"
    )
    assert _engaged_gate["seen"]["cached"] == {"ok": True}


def test_non_serializable_result_degrades_instead_of_failing_the_call(_engaged_gate):
    """A handler whose effect already landed must not be reported as a failure.

    Before the guard this unwound to `dispatch()`'s belt-and-suspenders handler and came back
    as an error envelope — telling the caller a successful side-effecting syscall had failed.
    """
    import uuid

    name = _engaged_gate["register"](
        "sys.test.idem11.unsafe", lambda p, c: {"id": uuid.uuid4()}
    )
    envelope = _dispatch(name, _engaged_gate["eu_id"])

    assert envelope.get("status") == "success", (
        f"a non-serializable result must not fail the call: {envelope}"
    )
    assert _engaged_gate["seen"]["cached"] is None, (
        "the unsafe result must be cached as nothing, not passed through to JSONB"
    )
