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

The guard also covers **raw IP-literal connects** (MEB-2b hardening): a tool that skips DNS
and calls ``socket.connect((ip, port))`` directly is caught at ``socket.socket.connect`` /
``connect_ex``. Any IP the caller did not obtain from an *allowed* ``getaddrinfo`` (tracked
per-context) is denied — a raw IP literal cannot be validated against a hostname allowlist,
so it is fail-closed.

Honest limits (the truly non-bypassable version is the sandbox ``--network none`` +
mediated-proxy path — see docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md MEB-2b):
  * A tool that resolves/connects on a **thread that does not inherit the contextvar**
    escapes the scope (raw ``threading.Thread`` does not copy context; asyncio executors
    do). Closing this in-process would require globally wrapping ``threading.Thread`` —
    intentionally not done; the sandbox path is the real fix.
  * Only the stdlib ``socket`` layer is wrapped; a tool linking its own native resolver/
    socket (ctypes, a C extension) bypasses both hooks.
"""
from __future__ import annotations

import contextlib
import contextvars
import ipaddress
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

# IPs resolved via an *allowed* getaddrinfo in this context — the connect-level guard
# treats these as vouched-for (they came from a permitted hostname) and denies any other
# IP literal. None = no active scope (guard inert). MEB-2b hardening.
_RESOLVED_IPS: contextvars.ContextVar[Optional[set]] = contextvars.ContextVar(
    "aindy_egress_resolved_ips", default=None
)

_installed = False
_install_lock = threading.Lock()
_orig_getaddrinfo = None
_orig_connect = None
_orig_connect_ex = None


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
    # A fresh per-scope set of IPs the allowed getaddrinfo vouches for (MEB-2b). Only
    # meaningful when an allowlist is active; the connect guard reads it.
    ips_token = _RESOLVED_IPS.set(set() if allow is not None else None)
    try:
        yield
    finally:
        _EGRESS_ALLOWLIST.reset(token)
        _RESOLVED_IPS.reset(ips_token)


def _check_connect_allowed(sock, address) -> None:
    """Deny a raw IP-literal connect the caller did not obtain from an allowed resolution.

    Inert unless an allowlist is active. Only guards AF_INET/AF_INET6 sockets. A hostname
    target is left to the getaddrinfo guard; an IP that our allowed getaddrinfo produced is
    vouched-for; any other IP literal is fail-closed (it cannot be matched to a hostname
    allowlist). MEB-2b hardening — closes the raw-``socket.connect((ip, port))`` bypass.
    """
    allow = _EGRESS_ALLOWLIST.get()
    if allow is None:
        return
    if getattr(sock, "family", None) not in (socket.AF_INET, socket.AF_INET6):
        return
    if not (isinstance(address, tuple) and address):
        return
    host = address[0]
    if not host:
        return
    host = str(host)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return  # a hostname — the getaddrinfo guard already gated it
    resolved = _RESOLVED_IPS.get() or ()
    if host in resolved:
        return  # we resolved this IP from an allowed host — vouched-for
    logger.warning(
        "[egress_guard] denied outbound connect to IP literal %r (allowlist=%s)", host, allow,
    )
    raise EgressDenied(
        f"egress to IP literal {host!r} is not permitted; the capability allowlist "
        f"{list(allow)} governs hostnames — connect through an allowed domain"
    )


def install_egress_guard() -> None:
    """Wrap ``socket.getaddrinfo`` + ``socket.socket.connect``/``connect_ex`` once.
    Idempotent; inert until an allowlist is set via ``egress_scope``."""
    global _installed, _orig_getaddrinfo, _orig_connect, _orig_connect_ex
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
            result = _orig_getaddrinfo(host, *args, **kwargs)
            # Record the IPs we resolved for an allowed host so the connect guard can tell
            # them apart from raw IP literals the caller supplies directly (MEB-2b).
            if allow is not None:
                resolved = _RESOLVED_IPS.get()
                if resolved is not None:
                    for entry in result:
                        try:
                            ip = entry[4][0]
                        except (IndexError, TypeError):
                            continue
                        if ip:
                            resolved.add(str(ip))
            return result

        _orig_connect = socket.socket.connect
        _orig_connect_ex = socket.socket.connect_ex

        def _guarded_connect(self, address):
            _check_connect_allowed(self, address)
            return _orig_connect(self, address)

        def _guarded_connect_ex(self, address):
            _check_connect_allowed(self, address)
            return _orig_connect_ex(self, address)

        socket.getaddrinfo = _guarded_getaddrinfo
        socket.socket.connect = _guarded_connect
        socket.socket.connect_ex = _guarded_connect_ex
        _installed = True
        logger.info(
            "[egress_guard] socket getaddrinfo + connect egress guard installed "
            "(inert until scoped)"
        )
