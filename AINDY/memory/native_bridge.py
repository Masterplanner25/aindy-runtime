"""Single loader for the compiled `memory_bridge_rs` extension (NATIVE-DISCOVERY-1).

Two modules used to load this crate with *different* search policies:

    runtime/memory/native_scorer.py   target/release, then target/debug
    memory/embedding_service.py       target/debug ONLY

`Native Crate Build (Rust)` builds `--release`, and so would any deployment — so the
C++ cosine kernel was unreachable from the recall fallback path while the scorer, in
the *same process*, used it. One process, two answers about whether native is
available.

This module is the one place that knows where the artifact lives. Both consumers
delegate here.

Artifact naming
---------------
`cargo build` emits a `cdylib` that Python will **not** import as-is:

    Linux    target/<profile>/libmemory_bridge_rs.so   → needs memory_bridge_rs.so
    Windows  target/<profile>/memory_bridge_rs.dll     → needs memory_bridge_rs.pyd
    macOS    target/<profile>/libmemory_bridge_rs.dylib → needs memory_bridge_rs.so

`Runtime Contracts` performs that rename in CI; a local build needs it done by hand.
This loader only adds directories to `sys.path` — it deliberately does not rename or
copy anything, since guessing at build outputs is how you end up loading a stale
artifact.

Caching
-------
The import is attempted **once per process**. A failed attempt latches: the extension
is optional and a missing one must not cost an import attempt on every scoring call.
Consequence worth knowing — building the crate while the process is running has no
effect until restart. `reset_cache()` exists for tests.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

_MODULE_NAME = "memory_bridge_rs"
# Release first: it is what CI builds and what a deployment would ship. Debug second
# so a local `cargo build` still works without a flag.
_PROFILES = ("release", "debug")

_bridge: Any | None = None
_load_attempted = False
_lock = threading.Lock()


def search_paths() -> list[str]:
    """Directories that may contain the compiled extension, in priority order."""
    crate_target = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "native",
        _MODULE_NAME,
        "target",
    )
    return [os.path.join(crate_target, profile) for profile in _PROFILES]


def load_bridge() -> Any | None:
    """Return the compiled extension, or ``None`` when it is not built.

    Never raises: the native path is an optimisation, and every caller has a pure
    Python fallback.
    """
    global _bridge, _load_attempted

    if _bridge is not None:
        return _bridge
    if _load_attempted:
        return None

    with _lock:
        # Re-check inside the lock — two threads can race the fast path above.
        if _bridge is not None:
            return _bridge
        if _load_attempted:
            return None
        _load_attempted = True

        # Insert in REVERSE priority order. `sys.path.insert(0, ...)` puts each entry
        # ahead of the previous one, so iterating in priority order would leave the
        # LAST (lowest-priority) path first — which is exactly what the previous
        # loader did: `for path in (release_path, debug_path): insert(0, path)` made
        # a stale `debug` build silently shadow a fresh `release` one, the opposite of
        # its own docstring. Verified before the fix: with both profiles built, the
        # extension resolved from `target/debug`.
        for path in reversed(search_paths()):
            if os.path.isdir(path) and path not in sys.path:
                sys.path.insert(0, path)

        try:
            import memory_bridge_rs  # noqa: PLC0415 — optional compiled extension

            _bridge = memory_bridge_rs
            logger.info(
                "[native_bridge] loaded %s from %s",
                _MODULE_NAME, getattr(memory_bridge_rs, "__file__", "?"),
            )
            return _bridge
        except Exception as exc:
            logger.info(
                "[native_bridge] %s unavailable (%s) — using the Python fallback. "
                "Build it with `cargo build --release` in AINDY/memory/native/%s and "
                "rename the artifact (lib*.so → *.so, *.dll → *.pyd).",
                _MODULE_NAME, exc, _MODULE_NAME,
            )
            return None


def is_loaded() -> bool:
    return _bridge is not None


def reset_cache() -> None:
    """Clear the cached load so the next call re-attempts. Tests only."""
    global _bridge, _load_attempted
    with _lock:
        _bridge = None
        _load_attempted = False
