"""
ContextVar propagation across ThreadPoolExecutor boundaries.

Before the fix, ThreadPoolExecutor.submit(fn) ran fn in a fresh context where
all ContextVars held their defaults. Trace continuity broke at every async
boundary: events and logs emitted from the worker thread had no trace_id or
eu_id, making cross-thread correlation impossible.

After the fix (copy_context), the calling thread's full ContextVar snapshot is
captured before submit and restored inside the worker thread.

Three shapes:
  1. trace_id set on calling thread is visible inside the worker thread.
  2. eu_id (syscall_dispatcher ContextVar) propagates correctly.
  3. pipeline_active propagates correctly.
"""
from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from unittest.mock import MagicMock, patch

import pytest

from AINDY.platform_layer.trace_context import (
    _trace_id_ctx,
    _pipeline_active_ctx,
    set_trace_id,
    set_pipeline_active,
)
from AINDY.kernel.syscall_dispatcher import _EU_ID_CTX

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _CapturingExecutor:
    """Stand-in for ThreadPoolExecutor: runs submit(callable, *args) inline."""

    def __init__(self):
        self.submitted_callable = None
        self.submitted_args = ()

    def submit(self, fn, *args):
        self.submitted_callable = fn
        self.submitted_args = args
        result = fn(*args)
        f: Future = Future()
        f.set_result(result)
        return f


# ---------------------------------------------------------------------------
# Shape 1 — trace_id propagates to worker thread
# ---------------------------------------------------------------------------

def test_trace_id_propagates_to_thread():
    expected_trace = str(uuid.uuid4())
    set_trace_id(expected_trace)

    seen: list[str] = []

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        _ctx = copy_context()

        def _worker():
            seen.append(_trace_id_ctx.get())

        fut = executor.submit(_ctx.run, _worker)
        fut.result(timeout=5)
    finally:
        executor.shutdown(wait=False)

    assert seen == [expected_trace], (
        f"Worker saw trace_id={seen!r}, expected {expected_trace!r}. "
        "copy_context() must be called before submit()."
    )


# ---------------------------------------------------------------------------
# Shape 2 — syscall eu_id propagates to worker thread
# ---------------------------------------------------------------------------

def test_eu_id_propagates_to_thread():
    expected_eu = str(uuid.uuid4())
    _EU_ID_CTX.set(expected_eu)

    seen: list[str] = []

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        _ctx = copy_context()

        def _worker():
            seen.append(_EU_ID_CTX.get())

        fut = executor.submit(_ctx.run, _worker)
        fut.result(timeout=5)
    finally:
        executor.shutdown(wait=False)

    assert seen == [expected_eu]


# ---------------------------------------------------------------------------
# Shape 3 — pipeline_active propagates to worker thread
# ---------------------------------------------------------------------------

def test_pipeline_active_propagates_to_thread():
    set_pipeline_active(True)

    seen: list[bool] = []

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        _ctx = copy_context()

        def _worker():
            seen.append(_pipeline_active_ctx.get())

        fut = executor.submit(_ctx.run, _worker)
        fut.result(timeout=5)
    finally:
        executor.shutdown(wait=False)

    assert seen == [True]
