"""
Re-entrancy guard for the system-event → memory-capture → async-job cycle.

RT-MEMTXN-LEAK-1 (third site). The runtime has a genuine feedback cycle:

    submit_async_job()                       # opens its own SessionLocal()
      └ emits EXECUTION_STARTED
          └ capture_system_event_as_memory() # EXECUTION_* is auto-captured
              └ MemoryNodeDAO.save()         # commit + refresh
                  └ _enqueue_embedding()     # every new node needs an embedding
                      └ submit_async_job()   # …which is itself an async job

Left unguarded that recurses without bound: every memory node spawns a job whose
lifecycle event becomes another memory node. The recursion is *synchronous*, and
each level holds the session it opened until the descent below it returns, so the
depth is capped only by the connection pool. Once the pool is drained every
further checkout waits the full ``pool_timeout`` — observed as a 42s login with
60 connections stuck ``idle in transaction`` on
``SELECT … FROM memory_nodes WHERE id = …`` (the ``save()`` refresh at each level).

This module bounds the cycle by depth. ``async_submit_scope()`` is entered by
``submit_async_job``; ``memory_capture_suppressed()`` reports whether we are
already *inside* a submission, i.e. whether this capture would be capturing the
lifecycle event of a job that a capture itself spawned.

Depth semantics — the outermost submission (depth 1) still captures normally, so
loop-closure signal (INFINITY-RUNTIME-1) is preserved; only the nested submission
it spawns (depth ≥ 2) has its lifecycle events dropped. Recursion therefore
terminates after one hop instead of running to pool exhaustion.

This is a backstop. The cycle is also cut at its origin by
``RUNTIME_INTERNAL_TASK_NAMES`` below: the runtime's own memory-maintenance jobs
are plumbing, never user signal, and capturing them is what closes the loop.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

__all__ = [
    "RUNTIME_INTERNAL_TASK_NAMES",
    "async_submit_depth",
    "async_submit_scope",
    "fresh_async_submit_depth",
    "memory_capture_suppressed",
]


# Async-job task names that are runtime memory plumbing. A memory node recording
# "the embedding job started" has no recall value, and — because every memory node
# enqueues an embedding job — capturing it is precisely what makes the cycle
# self-feeding. Kept as literals (not imported from ``AINDY.memory.embedding_jobs``)
# to avoid an import cycle; ``tests/unit/test_memory_capture_cascade.py`` asserts
# they stay in sync with the job registrations.
RUNTIME_INTERNAL_TASK_NAMES = frozenset(
    {
        "memory.generate_embedding",
        "memory.embedding_sweep",
    }
)

# Maximum async-submission nesting whose lifecycle events are still captured.
# 1 == only the outermost submission is captured.
_MAX_CAPTURED_SUBMIT_DEPTH = 1

_SUBMIT_DEPTH: ContextVar[int] = ContextVar("aindy_async_submit_depth", default=0)


def async_submit_depth() -> int:
    """Current async-job submission nesting depth (0 when not inside one)."""
    return _SUBMIT_DEPTH.get()


@contextmanager
def async_submit_scope() -> Iterator[int]:
    """Mark the dynamic extent of one async-job submission."""
    depth = _SUBMIT_DEPTH.get() + 1
    token = _SUBMIT_DEPTH.set(depth)
    try:
        yield depth
    finally:
        _SUBMIT_DEPTH.reset(token)


@contextmanager
def fresh_async_submit_depth() -> Iterator[None]:
    """
    Reset the depth for a job that is *executing* rather than being submitted.

    Worker threads inherit the submitter's context (``copy_context()`` in
    ``submit_async_job``), so without this an executing job would run at its
    submitter's depth and needlessly suppress captures for any job it chains.
    Only *synchronous* nesting pins sessions, and a thread hand-off ends that
    chain — so the executing job legitimately starts over at zero.
    """
    token = _SUBMIT_DEPTH.set(0)
    try:
        yield
    finally:
        _SUBMIT_DEPTH.reset(token)


def memory_capture_suppressed() -> bool:
    """
    True when a system-event memory capture must be skipped because it would
    close the submit → capture → submit cycle described in the module docstring.
    """
    return _SUBMIT_DEPTH.get() > _MAX_CAPTURED_SUBMIT_DEPTH
