from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_ASYNC_EXECUTION_CONTEXT: ContextVar[bool] = ContextVar(
    "_ASYNC_EXECUTION_CONTEXT", default=False
)


def activate_async_execution_context() -> ContextVar.Token[bool]:
    """Mark the current execution as running inside an async job."""
    return _ASYNC_EXECUTION_CONTEXT.set(True)


def deactivate_async_execution_context(token: ContextVar.Token[bool]) -> None:
    """Restore the async execution context token."""
    _ASYNC_EXECUTION_CONTEXT.reset(token)


def is_async_execution_active() -> bool:
    """Return True when the context is currently executing an async job."""
    return _ASYNC_EXECUTION_CONTEXT.get()


@contextmanager
def async_execution_scope() -> Iterator[None]:
    """Mark a block as an async-job execution boundary, restoring the previous state.

    FR-17 — the execution-contract gate in ``system_event_service`` rejects any
    ``execution.*`` event emitted with neither a pipeline nor this context active. That
    is right for a route that skipped the pipeline and wrong for the async-job path,
    which *is* an execution boundary and frequently has no HTTP request behind it (a
    scheduler job, the event-bus subscriber thread, an app bootstrap). Without this the
    submit-time ``execution.started`` was discarded, leaving an async job with no start
    row and a trace timeline with a silent gap exactly where the work began.

    Same ContextVar as :func:`activate_async_execution_context`, so it composes with the
    inline-execution path rather than competing with it; nesting restores the outer
    value rather than clearing the flag.
    """
    token = activate_async_execution_context()
    try:
        yield
    finally:
        deactivate_async_execution_context(token)
