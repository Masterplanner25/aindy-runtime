"""Socket-level outbound egress guard (ECOGAP-4 / G4a, MEB-2b).

MEB-2a activated the capability-policy domain allowlist, but its check is **static
arg-string inspection** — a tool that builds a URL at runtime (or reads a host from
config) egresses uncontrolled. MEB-2b closes that gap by enforcing the allowlist at DNS
resolution time: ``socket.getaddrinfo`` is wrapped so any hostname lookup outside the
active allowlist raises ``EgressDenied`` — catching the URL wherever the tool built it.

Scope and safety:
  * The wrapper is installed once, process-wide, but is **inert unless a contextvar
    allowlist is set** (via ``egress_scope``). Outside a policy-bound tool call it just
    delegates to the original ``getaddrinfo`` — zero effect.
  * The allowlist is set only for the duration of the tool ``fn`` call in ``execute_tool``,
    and only when a domain policy applies to the tool's capability and
    ``AINDY_EGRESS_ENFORCEMENT`` is on. Opt-in and off by default.

Honest limits (the truly non-bypassable version is the sandbox ``--network none`` +
mediated-proxy path — see docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md MEB-2b):
  * A connection to an **IP literal** performs no ``getaddrinfo`` and is not covered.
  * A tool that resolves on a **thread that does not inherit the contextvar** escapes the
    scope (raw ``threading.Thread`` does not copy context; asyncio executors do).
  * Only hostname resolution is guarded, not the eventual socket connect.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import socket
import threading
from typing import Optional

from AINDY.agents.capability_policy import _domain_allowed

logger = logging.getLogger(__name__)

# Active domain allowlist for the current context (None = guard inert here).
_EGRESS_ALLOWLIST: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "aindy_egress_allowlist", default=None
)

_installed = False
_install_lock = threading.Lock()
_orig_getaddrinfo = None


class EgressDenied(OSError):
    """Raised when a hostname resolution is outside the active egress allowlist."""


def egress_enforcement_enabled() -> bool:
    return os.getenv("AINDY_EGRESS_ENFORCEMENT", "").strip().lower() in {"1", "true", "yes"}


@contextlib.contextmanager
def egress_scope(domains):
    """Enforce ``domains`` as the outbound allowlist for the duration of the block.

    ``domains`` empty/None → no enforcement (the block runs unguarded).
    """
    allow = tuple(str(d).lower() for d in domains) if domains else None
    token = _EGRESS_ALLOWLIST.set(allow)
    try:
        yield
    finally:
        _EGRESS_ALLOWLIST.reset(token)


def install_egress_guard() -> None:
    """Wrap ``socket.getaddrinfo`` once. Idempotent; inert until an allowlist is set."""
    global _installed, _orig_getaddrinfo
    if _installed:
        return
    with _install_lock:
        if _installed:
            return
        _orig_getaddrinfo = socket.getaddrinfo

        def _guarded_getaddrinfo(host, *args, **kwargs):
            allow = _EGRESS_ALLOWLIST.get()
            if allow is not None and host:
                hostname = str(host).lower()
                if not _domain_allowed(hostname, allow):
                    logger.warning(
                        "[egress_guard] denied outbound resolution of %r (allowlist=%s)",
                        hostname, allow,
                    )
                    raise EgressDenied(
                        f"egress to {hostname!r} is not in the capability allowlist {list(allow)}"
                    )
            return _orig_getaddrinfo(host, *args, **kwargs)

        socket.getaddrinfo = _guarded_getaddrinfo
        _installed = True
        logger.info("[egress_guard] socket.getaddrinfo egress guard installed (inert until scoped)")
