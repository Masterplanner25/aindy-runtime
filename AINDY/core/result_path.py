"""The runtime's one way to address a value inside a step result: a dot path.

`a.b.0.c`: dict keys, and integer indices into lists and tuples. A path that does not resolve
returns `MISSING`, never `None`, because `None` is a legitimate result value.

Used by the plan verifier (`core/verifier.py`, a step's `expects`) and by plan step references
(`agents/step_references.py`, FR-46 / DEC-073). Lifted from the verifier so the two cannot drift
into different grammars for the same thing.
"""
from __future__ import annotations

from typing import Any

MISSING = object()


def path_is_well_formed(path: Any) -> bool:
    """A non-empty string of non-empty dot segments."""
    return isinstance(path, str) and bool(path) and all(part for part in path.split("."))


def resolve_path(payload: Any, path: str) -> Any:
    cur = payload
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return MISSING
        else:
            return MISSING
    return cur
