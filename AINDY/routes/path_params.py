"""Validated path-parameter types shared by the routers (FR-25 b).

A route declared ``run_id: str`` accepts anything, and a handler that then calls
``uuid.UUID(run_id)`` / ``normalize_uuid(run_id)`` turns a typo into a **500** carrying the
parser's own message (``badly formed hexadecimal UUID string``). Measured on 2026-09-11 by
probing every parameterised runtime route with ``not-a-uuid`` — through the booted app on
SQLite and again on live Postgres — six routes answered 500; every other one answered
4xx. The app team had confirmed one of the six and offered the population as a bound.

``UUIDPath`` moves the check to the boundary FastAPI already owns, so a malformed id is a
**422** with a structured validation error and the OpenAPI schema stops advertising the
parameter as free text.

★ It is ``Annotated[str, …]``, NOT ``uuid.UUID``, on purpose. The handlers keep receiving the
string they were written for (several compare it to a string sentinel, pass it into
``input_payload``, or forward it to a service typed ``str``), so nothing downstream changes.
And the acceptance rule is *exactly* ``uuid.UUID(value)`` — the same call the handlers make —
so any id a handler accepted before this is accepted after it. A stricter type could have
started rejecting good input; this one cannot.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Path
from pydantic import AfterValidator


def _must_parse_as_uuid(value: str) -> str:
    """Reject at the boundary what ``uuid.UUID`` would reject in the handler."""
    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"must be a UUID: {exc}") from exc
    return value


UUIDPath = Annotated[
    str,
    AfterValidator(_must_parse_as_uuid),
    Path(description="A UUID (canonical or 32-hex form)."),
]

__all__ = ["UUIDPath"]
