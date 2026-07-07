"""
platform_layer/secret_broker.py - Just-in-time secrets broker (AGENT-HARDEN-9).

Secrets are resolved **at call time**, scoped to the caller's granted capabilities,
and **never persisted** — not in the DB, not on the capability token, not in the
result. Today secrets are process env vars; this adds the broker abstraction so a
tool obtains a secret via a scoped JIT call instead of reading ``os.environ``
directly, and prod can swap the backend (OS keychain / Vault) with no call-site
change.

Deliberately NOT a syscall: the dispatch envelope is trace-logged, so a secret
value must never transit it. Resolution is an in-process call at the tool seam.

Design:
  - ``SecretRef(name, required_capability)`` names a secret and the capability that
    gates it. Resolution is denied unless that capability is in the caller's grants.
  - ``SecretBroker`` fetches a named secret from a backend; ``EnvSecretBroker``
    (default) reads a controlled ``AINDY_SECRET_<NAME>`` namespace — NOT arbitrary
    env vars. Prod swaps in a keychain/Vault backend via ``set_secret_broker`` (PR2).
  - ``resolve_secret(name, capabilities=…)`` is the public JIT entry point.
"""
from __future__ import annotations

import os
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

_ENV_PREFIX = "AINDY_SECRET_"
_NAME_RE = re.compile(r"[^A-Z0-9_]")


@dataclass(frozen=True)
class SecretRef:
    """A reference to a secret — never the value itself."""

    name: str
    required_capability: Optional[str] = None


class SecretBroker(ABC):
    """Backend that fetches a named secret value. Never persists it."""

    @abstractmethod
    def fetch(self, name: str) -> Optional[str]:
        ...


class EnvSecretBroker(SecretBroker):
    """Dev backend: resolve from a controlled ``AINDY_SECRET_<NAME>`` env namespace.

    Only names under the prefix are readable — not arbitrary process env vars — so
    the broker is a deliberate secret surface, and prod can replace it with a
    keychain/Vault backend transparently.
    """

    def __init__(self, prefix: str = _ENV_PREFIX) -> None:
        self._prefix = prefix

    def _env_key(self, name: str) -> str:
        return self._prefix + _NAME_RE.sub("_", str(name).upper())

    def fetch(self, name: str) -> Optional[str]:
        value = os.environ.get(self._env_key(name))
        return value if value else None


# ── Scope registry: secret name → capability required to resolve it ────────────
SECRET_SCOPES: dict[str, str] = {}
_SCOPE_LOCK = threading.Lock()


def register_secret_scope(name: str, required_capability: str) -> None:
    with _SCOPE_LOCK:
        SECRET_SCOPES[str(name)] = str(required_capability)


def clear_secret_scopes() -> None:
    """Test helper."""
    with _SCOPE_LOCK:
        SECRET_SCOPES.clear()


# ── Pluggable broker singleton ─────────────────────────────────────────────────
_BROKER: SecretBroker | None = None
_BROKER_LOCK = threading.Lock()


def get_secret_broker() -> SecretBroker:
    global _BROKER
    if _BROKER is None:
        with _BROKER_LOCK:
            if _BROKER is None:
                _BROKER = EnvSecretBroker()
    return _BROKER


def set_secret_broker(broker: SecretBroker | None) -> None:
    """Install a backend (prod keychain/Vault, or reset to default with None)."""
    global _BROKER
    with _BROKER_LOCK:
        _BROKER = broker


def resolve_secret(
    name: str,
    *,
    capabilities: Any = None,
    required_capability: Optional[str] = None,
    broker: Optional[SecretBroker] = None,
) -> dict[str, Any]:
    """Resolve a secret just-in-time, scoped to *capabilities*.

    Returns ``{"ok": True, "value": <secret>}`` or ``{"ok": False, "error": …}``.
    The value is fetched at call time and returned to the caller only — never
    persisted. Denied (fail-closed) when the gating capability is not granted.

    The gate is ``required_capability`` if given, else the capability registered for
    *name* via ``register_secret_scope``. A secret with no registered gate is
    resolvable by any caller (dev convenience) — register a scope to lock it down.
    """
    name = str(name)
    gate = required_capability if required_capability is not None else SECRET_SCOPES.get(name)
    granted = set(capabilities or [])
    if gate is not None and gate not in granted:
        return {
            "ok": False,
            "error": f"secret {name!r} requires capability {gate!r}",
        }

    active = broker or get_secret_broker()
    try:
        value = active.fetch(name)
    except Exception as exc:  # fail-closed — a backend error never leaks a partial secret
        return {"ok": False, "error": f"secret broker error: {exc}"}
    if not value:
        return {"ok": False, "error": f"secret {name!r} not found"}
    return {"ok": True, "value": value}
