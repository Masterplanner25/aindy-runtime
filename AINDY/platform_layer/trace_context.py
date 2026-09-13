from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator


_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")
_parent_event_id_ctx: ContextVar[str] = ContextVar("parent_event_id", default="-")
_pipeline_active_ctx: ContextVar[bool] = ContextVar("pipeline_active", default=False)
_current_request_ctx: ContextVar[Any] = ContextVar("current_request", default=None)
_current_execution_context_ctx: ContextVar[Any] = ContextVar(
    "current_execution_context", default=None
)


def get_trace_id(default: str | None = None) -> str | None:
    trace_id = _trace_id_ctx.get()
    if trace_id == "-":
        return default
    return trace_id


def set_trace_id(trace_id: str) -> Token:
    return _trace_id_ctx.set(str(trace_id))


def reset_trace_id(token: Token) -> None:
    _trace_id_ctx.reset(token)


def ensure_trace_id(trace_id: str | None = None) -> str:
    """Return the current trace id, establishing one FOR THE REST OF THIS CONTEXT if absent.

    ★ The establish half has no token and is never released — on a long-lived thread (a
    scheduler worker) the first caller pins that thread's trace id for every later unit of
    work it runs, so unrelated runs share a `trace_id` (TEST-ORDER-CONTEXTVAR-1, where
    `PersistentFlowRunner.start` did exactly that). Inside a request or a flow node the trace
    is already set with a token, so this only reads. Kept because app flow nodes call it in
    that read-only position; **for new code use :func:`trace_scope`**, which releases what it
    establishes.
    """
    current = get_trace_id()
    if current:
        return current
    generated = str(trace_id or uuid.uuid4())
    _trace_id_ctx.set(generated)
    return generated


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    """Yield the current trace id, establishing one for the block if none is current.

    The token-holding form of :func:`ensure_trace_id`: an ambient trace is reused untouched;
    an absent one is set for the duration of the block and reset on exit, whatever the block
    did. Nothing outlives the caller's frame, so a scheduler thread that runs many units of
    work never carries one unit's trace id into the next.
    """
    current = get_trace_id()
    if current:
        yield current
        return
    generated = str(trace_id or uuid.uuid4())
    token = _trace_id_ctx.set(generated)
    try:
        yield generated
    finally:
        _trace_id_ctx.reset(token)


def get_parent_event_id(default: str | None = None) -> str | None:
    parent_event_id = _parent_event_id_ctx.get()
    if parent_event_id == "-":
        return default
    return parent_event_id


def set_parent_event_id(parent_event_id: str | None) -> Token:
    return _parent_event_id_ctx.set("-" if not parent_event_id else str(parent_event_id))


def reset_parent_event_id(token: Token) -> None:
    _parent_event_id_ctx.reset(token)


def is_pipeline_active() -> bool:
    return bool(_pipeline_active_ctx.get())


def set_pipeline_active(active: bool = True) -> Token:
    return _pipeline_active_ctx.set(bool(active))


def reset_pipeline_active(token: Token) -> None:
    _pipeline_active_ctx.reset(token)


def get_current_request(default: Any = None) -> Any:
    current = _current_request_ctx.get()
    if current is None:
        return default
    return current


def set_current_request(request: Any) -> Token:
    return _current_request_ctx.set(request)


def reset_current_request(token: Token) -> None:
    _current_request_ctx.reset(token)


def get_current_execution_context(default: Any = None) -> Any:
    current = _current_execution_context_ctx.get()
    if current is None:
        return default
    return current


def set_current_execution_context(context: Any) -> Token:
    return _current_execution_context_ctx.set(context)


def reset_current_execution_context(token: Token) -> None:
    _current_execution_context_ctx.reset(token)


def get_current_trace_id(default: str | None = None) -> str | None:
    return get_trace_id(default=default)


def set_current_trace_id(trace_id: str) -> Token:
    return set_trace_id(trace_id)


def reset_current_trace_id(token: Token) -> None:
    reset_trace_id(token)
