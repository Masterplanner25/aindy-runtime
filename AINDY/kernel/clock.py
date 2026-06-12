"""
Clock — injectable time source for deterministic replay.

Usage
-----
Production code calls ``utcnow()`` instead of ``datetime.now(timezone.utc)``::

    from AINDY.kernel.clock import utcnow
    ts = utcnow()

Tests freeze time with ``frozen_at``::

    from AINDY.kernel.clock import frozen_at
    from datetime import datetime, timezone

    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with frozen_at(fixed) as t:
        assert utcnow() == fixed

``frozen_at`` uses a ContextVar so it is async-safe and thread-safe: each
coroutine or thread has its own override without interference.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Generator

_override: ContextVar[datetime | None] = ContextVar(
    "_aindy_clock_override", default=None
)


def utcnow() -> datetime:
    t = _override.get()
    return t if t is not None else datetime.now(timezone.utc)


@contextmanager
def frozen_at(t: datetime) -> Generator[datetime, None, None]:
    token = _override.set(t)
    try:
        yield t
    finally:
        _override.reset(token)
