"""`FLOW-PARALLEL-1` phase 0 — `FlowHistory` ordinals are allocated per SUPERSTEP, at the barrier.

The allocation this replaces read `max(sequence_number) + 1` under a comment stating its own
precondition:

    "max()+1 is safe: a run's nodes execute sequentially (no concurrent writers), and it
     continues correctly across a resume (seeds from the highest existing sequence)."

**Fan-out is precisely what removes that precondition.** `DUR-4`'s fold depends on the ordinal
being a deterministic total order, so allocating per branch as it finishes would make history
order equal completion order — the same defect `state_merge` exists to prevent one layer up.

Phase 0 is inert: a superstep is one node, `count=1`, and the result is the number `max()+1`
produced. What changes is that the allocation is now expressible for N, at the barrier, in
declaration order.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.runtime_only


def _runner(highest):
    """A runner with just enough shape to allocate — no DB, no flow, no execution."""
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    runner = object.__new__(PersistentFlowRunner)
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = highest
    runner.db = db
    return runner


def _run():
    run = MagicMock()
    run.id = "11111111-1111-1111-1111-111111111111"
    return run


# --------------------------------------------------------------------------------------
# Phase 0 equivalence — the property that makes this safe to land
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("highest", [0, 1, 7, 999])
def test_count_of_one_is_exactly_the_max_plus_one_it_replaces(highest):
    """★★ The inertness guarantee, stated as an equality rather than as prose."""
    allocated = _runner(highest)._allocate_sequence_numbers(_run(), 1)

    assert allocated == [highest + 1]


def test_an_empty_history_starts_at_one():
    """`scalar()` returns None on a run with no history; `or 0` made that 1, and still must."""
    assert _runner(None)._allocate_sequence_numbers(_run(), 1) == [1]


# --------------------------------------------------------------------------------------
# The property fan-out will need
# --------------------------------------------------------------------------------------


def test_a_superstep_gets_a_contiguous_ascending_block():
    assert _runner(7)._allocate_sequence_numbers(_run(), 3) == [8, 9, 10]


def test_the_block_is_allocated_in_one_read():
    """★ One read at the barrier, not one per branch.

    Not a performance assertion — a *correctness* one. N separate `max()+1` reads is exactly the
    interleaving that collides once branches run concurrently, and it would still pass a test
    that only checked the returned numbers.
    """
    runner = _runner(7)

    runner._allocate_sequence_numbers(_run(), 5)

    assert runner.db.query.call_count == 1, (
        f"allocated a 5-branch superstep with {runner.db.query.call_count} reads; per-branch "
        f"allocation is the race this function exists to remove"
    )


def test_zero_branches_allocates_nothing_and_does_not_query():
    """An empty superstep must not consume an ordinal — a gap in the sequence breaks DUR-4's fold."""
    runner = _runner(7)

    assert runner._allocate_sequence_numbers(_run(), 0) == []
    assert runner.db.query.call_count == 0


def test_ordinals_are_unique_and_sorted():
    """Both properties are load-bearing: uniqueness for the primary-key-ish ordering, sortedness
    because consumers fold in ascending order."""
    allocated = _runner(3)._allocate_sequence_numbers(_run(), 6)

    assert len(set(allocated)) == len(allocated)
    assert allocated == sorted(allocated)


def test_the_runner_still_writes_history_with_an_allocated_ordinal():
    """★ Liveness — the allocator must be ON the live path, not beside it.

    A helper the runner does not call is `ROUTE-AST-UNWIRED-1`. Checked over the AST so a
    commented-out call cannot satisfy it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("AINDY/runtime/flow_engine/runner.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_allocate_sequence_numbers"
    ]

    assert calls, (
        "runner.py never calls _allocate_sequence_numbers — the barrier allocation exists "
        "beside the execution path instead of on it"
    )


def test_no_max_plus_one_allocation_survives_in_the_runner():
    """★ The old inline allocation must be GONE, not merely bypassed.

    Two allocators would drift, and the inline one is the racy shape. Matched over the AST for
    a `max(FlowHistory.sequence_number)` call outside the allocator itself.
    """
    import ast
    from pathlib import Path

    source = Path("AINDY/runtime/flow_engine/runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    allocator = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_allocate_sequence_numbers"
        ),
        None,
    )
    assert allocator is not None, "the allocator is gone"
    allocator_lines = set(range(allocator.lineno, (allocator.end_lineno or allocator.lineno) + 1))

    stray = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "sequence_number"
        and node.lineno not in allocator_lines
    ]

    assert not stray, (
        f"sequence_number is still read outside the allocator at line(s) {stray}; a second "
        f"allocation path is the race the barrier allocation removes"
    )
