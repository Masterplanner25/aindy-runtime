"""SYSMAX-3, guest half — a declared memory ceiling is ENFORCED on the Nodus guest path.

nodus-lang 5.13 gives ``NodusRuntime`` a ``max_memory_mb``: it reads the worker's RSS when the
run starts and kills the script once the process has grown past that budget (polled, so it
bounds growth over the run, not a single allocation — nodus's own docstring says so, and this
file's tests are shaped by it: a script that grows in FEW instructions can finish before the
poll fires, so the growing script here loops many times). It is supplied by the VM, so the
"requires OS integration" blocker on SYSMAX-3 does not apply on this path.

Wiring: ``AINDY_NODUS_MAX_MEMORY_MB`` puts a ``resources.memory_bytes`` ceiling on the guest
floor (a declared spec may only narrow it); ``nodus_runtime_kwargs`` turns it into
``max_memory_mb``; ``enforced_resources(guest=True)`` says memory is enforced HERE and nowhere
else; a host that cannot meter RSS REFUSES the run instead of running it unbounded.

The kill test drives the real VM through ``nodus_worker.run_one`` — the assertion is that the
script did not finish and the failure names the memory limit. Default (env unset) is pinned
byte-for-byte as before: no ceiling, no kwarg.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")

from AINDY.core.execution_environment import (  # noqa: E402
    GUEST_FLOOR,
    GUEST_MAX_MEMORY_MB_ENV,
    GUEST_RESOURCES_ENFORCED,
    RESOURCES_ENFORCED,
    ExecutionEnvironmentSpec,
    Resources,
    clamp_to_floor,
    enforced_resources,
    guest_floor,
    nodus_runtime_kwargs,
)

MB = 1048576

# Grows in MANY instructions so the VM's memory poll has a chance to fire: ~120k list
# concatenations of four fresh floats each (~15 MB of growth, well past a 1 MB ceiling even
# if the OS trims the worker's working set under pressure — the bound is RSS, and Windows
# trims it) — killed within a second under a 1 MB ceiling, ~40 s to completion unbounded
# (which is why the control below loops far less).
_GROWING = """
let xs = []
let i = 0
while (i < 120000) {
  xs = xs + [i * 1.5, i * 2.5, i * 3.5, i * 4.5]
  i = i + 1
}
set_state("n", i)
"""

_SMALL = """
let xs = []
let i = 0
while (i < 500) {
  xs = xs + [i]
  i = i + 1
}
set_state("n", i)
"""


def _run(script: str, **payload):
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one(
        {"script": script, "state": {}, "context": {"user_id": "memory-ceiling-test"},
         "max_execution_ms": 60000, **payload}
    )


# ── the descriptor → kwargs translation ─────────────────────────────────────


def test_default_floor_declares_no_memory_ceiling(monkeypatch):
    monkeypatch.delenv(GUEST_MAX_MEMORY_MB_ENV, raising=False)
    assert guest_floor().resources.memory_bytes is None
    assert "max_memory_mb" not in nodus_runtime_kwargs(guest_floor(), scratch_root="/tmp/x")


def test_operator_ceiling_lands_on_the_guest_floor_as_bytes(monkeypatch):
    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, "64")
    floor = guest_floor()
    assert floor.resources.memory_bytes == 64 * MB
    # Everything else about the floor is unchanged — the ceiling is additive.
    assert floor.visibility == GUEST_FLOOR.visibility
    assert floor.authority == GUEST_FLOOR.authority
    assert nodus_runtime_kwargs(floor, scratch_root="/tmp/x")["max_memory_mb"] == pytest.approx(64.0)


@pytest.mark.parametrize("raw", ["", "0", "-5", "lots"])
def test_unset_zero_negative_or_garbage_means_no_ceiling(monkeypatch, raw):
    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, raw)
    assert guest_floor().resources.memory_bytes is None


def test_a_declared_spec_can_only_narrow_the_operator_ceiling(monkeypatch):
    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, "64")
    wider = ExecutionEnvironmentSpec(resources=Resources(memory_bytes=512 * MB))
    narrower = ExecutionEnvironmentSpec(resources=Resources(memory_bytes=8 * MB))

    effective, widened = clamp_to_floor(wider, guest_floor())
    assert effective.resources.memory_bytes == 64 * MB
    assert "resources.memory_bytes" in widened

    effective, widened = clamp_to_floor(narrower, guest_floor())
    assert effective.resources.memory_bytes == 8 * MB
    # (a default-constructed spec still gets its visibility/authority clamped — only memory
    # is under test here)
    assert "resources.memory_bytes" not in widened


def test_memory_is_enforced_on_the_guest_path_only():
    spec = ExecutionEnvironmentSpec(resources=Resources(wall_time_ms=1, memory_bytes=1, syscalls=1, tokens=1))
    assert enforced_resources(spec) == ["wall_time_ms", "syscalls", "tokens"]
    assert enforced_resources(spec, guest=True) == ["wall_time_ms", "syscalls", "tokens", "memory_bytes"]
    assert GUEST_RESOURCES_ENFORCED == RESOURCES_ENFORCED + ("memory_bytes",)


# ── the real VM ───────────────────────────────────────────────────────────────


def test_control_a_small_script_completes_under_a_generous_ceiling(monkeypatch):
    """Liveness: the ceiling is wired and a script that stays under it is untouched."""
    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, "256")
    result = _run(_SMALL)
    assert result["status"] == "success", result.get("error")
    assert result["output_state"]["n"] == 500


def test_a_growing_script_is_killed_at_the_ceiling(monkeypatch):
    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, "1")
    result = _run(_GROWING)

    assert result["status"] == "failure"
    assert "Memory limit exceeded" in str(result["error"])
    assert "n" not in (result["output_state"] or {}), "the script ran to completion"


def test_a_declared_spec_narrower_than_the_floor_is_what_the_vm_gets(monkeypatch):
    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, "256")
    spec = ExecutionEnvironmentSpec(resources=Resources(memory_bytes=1 * MB))
    result = _run(_GROWING, env_spec=spec.to_dict())

    assert result["status"] == "failure"
    assert "Memory limit exceeded" in str(result["error"])


def test_an_unmeterable_host_refuses_a_declared_ceiling(monkeypatch):
    """A limit that would not fire is worse than none: refused, named, never dropped."""
    import nodus.runtime.memory as nodus_memory

    monkeypatch.setenv(GUEST_MAX_MEMORY_MB_ENV, "64")
    monkeypatch.setattr(nodus_memory, "memory_metering_available", lambda: False)
    result = _run(_SMALL)

    assert result["status"] == "failure"
    assert "cannot be enforced on this host" in str(result["error"])
    assert "n" not in (result["output_state"] or {})


def test_an_unmeterable_host_runs_normally_when_nothing_is_declared(monkeypatch):
    """The refusal is for a DECLARED ceiling only — no declaration, no metering needed."""
    import nodus.runtime.memory as nodus_memory

    monkeypatch.delenv(GUEST_MAX_MEMORY_MB_ENV, raising=False)
    monkeypatch.setattr(nodus_memory, "memory_metering_available", lambda: False)
    result = _run(_SMALL)
    assert result["status"] == "success", result.get("error")
