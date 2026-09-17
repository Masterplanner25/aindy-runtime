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
mediated-proxy path — see docs/design/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md MEB-2b):
  * A tool that resolves/connects on a **thread that does not inherit the contextvar**
    escapes the scope (raw ``threading.Thread`` does not copy context; asyncio executors
    do). Closing this in-process would require globally wrapping ``threading.Thread`` —
    intentionally not done; the sandbox path is the real fix. ★ In the isolated tool
    WORKER the guard is installed PROCESS-GLOBALLY (``install_process_egress``), so this
    bypass does not apply there — the worker runs one tool and nothing else.
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
from dataclasses import dataclass
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

# EGRESS-INPROC-1 — the PROCESS-GLOBAL form, for the isolated tool worker only. A worker runs
# exactly one tool, so "the whole process" and "this tool call" are the same scope there — and
# unlike the contextvar, a raw `threading.Thread` cannot escape it. `None` = not installed.
_PROCESS_ALLOWLIST: Optional[tuple] = None
_PROCESS_RESOLVED_IPS: Optional[set] = None

_installed = False
_install_lock = threading.Lock()
_orig_getaddrinfo = None
_orig_connect = None
_orig_connect_ex = None


class EgressDenied(OSError):
    """Raised when a hostname resolution is outside the active egress allowlist."""


def egress_enforcement_enabled() -> bool:
    return os.getenv("AINDY_EGRESS_ENFORCEMENT", "").strip().lower() in {"1", "true", "yes"}


def _active_allowlist() -> Optional[tuple]:
    """The contextvar scope wins; the worker's process-global install is the fallback."""
    allow = _EGRESS_ALLOWLIST.get()
    return allow if allow is not None else _PROCESS_ALLOWLIST


def _active_resolved_ips() -> Optional[set]:
    resolved = _RESOLVED_IPS.get()
    return resolved if resolved is not None else _PROCESS_RESOLVED_IPS


@contextlib.contextmanager
def egress_scope(domains, *, deny_all: bool = False):
    """Enforce ``domains`` as the outbound allowlist for the duration of the block.

    ``domains`` empty/None → no enforcement (the block runs unguarded) — UNLESS ``deny_all``,
    which installs an EMPTY allowlist: every resolution and every raw-IP connect is refused.
    That is how `authority.network = "none"` and a `scoped` mode with no policy domains are
    expressed (EGRESS-INPROC-1, fail-closed by construction).
    """
    if deny_all:
        allow: Optional[tuple] = ()
    else:
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


# ── EGRESS-INPROC-1: one decision, enforced per provider ─────────────────────
#
# `execute_tool` used to compute the allowlist and enter `egress_scope` around the IN-PROCESS
# call only; the isolated branch returned before it, so the tool the runtime distrusts enough
# to move out of process ran with no egress enforcement at all. The decision is now a VALUE,
# resolved once before that branch, and each provider enforces what it honestly can:
#
#   in-process   → `egress_scope(...)`, contextvar-scoped          reported "socket_guard"
#   tool worker  → `install_process_egress(...)` from its payload  reported "socket_guard:worker"
#   (a container runner would report "netns" — not built; EXEC-ENV-BIND-1's root note)
#
# The worker gets the DECISION, never the policy (DEC-049): it has no db and re-checking
# authority in the distrusted process is what `tool_worker.py` forbids.

MECHANISM_SOCKET_GUARD = "socket_guard"
MECHANISM_SOCKET_GUARD_WORKER = "socket_guard:worker"
MECHANISM_NONE = "none"


@dataclass(frozen=True)
class EgressDecision:
    """``mode`` is ``none | scoped | open``; ``domains`` is the sorted allowlist (``scoped``
    with an empty tuple is DENY-ALL, fail-closed — never "unconstrained")."""

    mode: str
    domains: tuple = ()

    @property
    def enforces(self) -> bool:
        return self.mode != "open"

    def to_payload(self) -> dict:
        return {"mode": self.mode, "domains": list(self.domains)}

    @classmethod
    def from_payload(cls, raw) -> Optional["EgressDecision"]:
        if not isinstance(raw, dict):
            return None
        mode = str(raw.get("mode") or "open")
        if mode not in ("none", "scoped", "open"):
            return None
        domains = tuple(sorted({str(d).lower() for d in (raw.get("domains") or ()) if d}))
        return cls(mode=mode, domains=domains)


def resolve_egress_decision(network_mode: Optional[str], policy_domains) -> EgressDecision:
    """Fold the spec's ``authority.network`` and the capability policies' domain allowlist into
    one decision (DEC-048).

    * ``none``            → deny-all, whatever the policies say.
    * any domains         → ``scoped`` to exactly those (a policy narrows an ``open`` mode).
    * ``scoped``, no list → deny-all. A tool that asked for a scope and got no allowlist is
                            NOT granted the open internet by the absence of a policy.
    * ``open``, no list   → nothing to enforce.
    """
    mode = str(network_mode or "open")
    domains = tuple(sorted({str(d).lower() for d in (policy_domains or ()) if d}))
    if mode == "none":
        return EgressDecision("none", ())
    if domains:
        return EgressDecision("scoped", domains)
    if mode == "scoped":
        return EgressDecision("scoped", ())
    return EgressDecision("open", ())


def egress_decision_scope(decision: Optional[EgressDecision]):
    """The in-process enforcement of a decision: a context manager, inert for ``open``."""
    if decision is None or not decision.enforces:
        return contextlib.nullcontext()
    install_egress_guard()
    if decision.mode == "none" or not decision.domains:
        return egress_scope((), deny_all=True)
    return egress_scope(decision.domains)


def install_process_egress(decision: EgressDecision) -> str:
    """Enforce ``decision`` for the REST OF THIS PROCESS (the tool worker's form, DEC-049).

    Returns the mechanism name the caller should report. Unlike `egress_scope` there is no
    exit: a worker runs one tool and dies, and a scope a bare thread can leave is the bypass
    this exists to close. An ``open`` decision installs nothing and reports ``none``.
    """
    global _PROCESS_ALLOWLIST, _PROCESS_RESOLVED_IPS
    if not decision.enforces:
        return MECHANISM_NONE
    install_egress_guard()
    _PROCESS_ALLOWLIST = () if (decision.mode == "none" or not decision.domains) else decision.domains
    _PROCESS_RESOLVED_IPS = set()
    logger.info(
        "[egress_guard] process-global egress installed: %s %s", decision.mode, list(decision.domains)
    )
    return MECHANISM_SOCKET_GUARD_WORKER


def clear_process_egress() -> None:
    """Test seam — a worker never clears it."""
    global _PROCESS_ALLOWLIST, _PROCESS_RESOLVED_IPS
    _PROCESS_ALLOWLIST = None
    _PROCESS_RESOLVED_IPS = None


def _check_connect_allowed(sock, address) -> None:
    """Deny a raw IP-literal connect the caller did not obtain from an allowed resolution.

    Inert unless an allowlist is active. Only guards AF_INET/AF_INET6 sockets. A hostname
    target is left to the getaddrinfo guard; an IP that our allowed getaddrinfo produced is
    vouched-for; any other IP literal is fail-closed (it cannot be matched to a hostname
    allowlist). MEB-2b hardening — closes the raw-``socket.connect((ip, port))`` bypass.
    """
    allow = _active_allowlist()
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
    resolved = _active_resolved_ips() or ()
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
            allow = _active_allowlist()
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
                resolved = _active_resolved_ips()
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
