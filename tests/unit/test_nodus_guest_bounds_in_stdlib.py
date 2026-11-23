"""A guest's time budget holds inside a stdlib call (nodus-lang 5.17.0).

Through nodus-lang 5.16.0 a call into an imported or stdlib module ran on a child VM that
inherited the guest's authority but none of its bounds: no deadline, no step limit, no memory
ceiling. The worker passes `timeout_ms` (AINDY_NODUS_MAX_EXECUTION_MS) and `max_memory_mb`
(SYSMAX-3 guest half) to `NodusRuntime`; a guest could step past both by doing its work inside
`std:strings`. nodus 5.17.0 hands every child VM the caller's remaining bounds.

These tests drive the real worker entry point, `nodus_worker.run_one`, with a real script.
The control proves the same stdlib call completes when the budget allows it, so the refusal
below is the budget and not a broken import. Under nodus-lang 5.16.0 the bounded test fails:
the call runs to completion (~2.6 s for 200k chars) and the run reports success.
"""
from __future__ import annotations

import time
import uuid

import pytest

STDLIB_HEAVY = """
import "std:strings" as s
let x = s.repeat("a", {n}i)
set_state("n", len(x))
"""


def _run_one(script: str, budget_ms: int) -> dict:
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one({
        "script": script,
        "filename": "bounds.nd",
        "state": {},
        "memory_context": {},
        "input_payload": {},
        "max_execution_ms": budget_ms,
        "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": str(uuid.uuid4()), "trace_id": "t-bounds"},
    })


def test_control_the_stdlib_call_completes_within_a_generous_budget():
    result = _run_one(STDLIB_HEAVY.format(n=500), budget_ms=30_000)

    assert result["status"] == "success", result
    assert result["output_state"]["n"] == 500


def test_the_guest_deadline_stops_work_done_inside_a_stdlib_call():
    started = time.monotonic()
    result = _run_one(STDLIB_HEAVY.format(n=200_000), budget_ms=100)
    elapsed = time.monotonic() - started

    assert result["status"] != "success", result
    assert "timed out" in str(result.get("error") or "").lower(), result
    # Unbounded, this call takes seconds; bounded, it stops near the 100 ms deadline. The
    # margin absorbs worker set-up and a loaded CI runner, not the work itself.
    assert elapsed < 1.5, elapsed
