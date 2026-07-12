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
import contextlib
import contextvars
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ENV_PREFIX = "AINDY_SECRET_"
_NAME_RE = re.compile(r"[^A-Z0-9_]")
# File / Vault secret names must be simple (no path traversal, no injection).
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]+")


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


class FileSecretBroker(SecretBroker):
    """Container backend: resolve from a mounted secrets directory.

    Matches the Docker / Kubernetes convention where each secret is a file at
    ``<root>/<name>`` (default ``/run/secrets``) — so the secret is never in the
    process env or image. Names are sanitized to a simple charset (no path
    traversal).
    """

    def __init__(self, root: str = "/run/secrets") -> None:
        self._root = Path(root)

    def fetch(self, name: str) -> Optional[str]:
        if not _SAFE_NAME_RE.fullmatch(str(name)):
            return None
        path = self._root / str(name)
        try:
            if not path.is_file():
                return None
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None


class VaultSecretBroker(SecretBroker):
    """HashiCorp Vault KV v2 backend (httpx — no hvac dependency).

    Reads ``{addr}/v1/{mount}/data/{name}`` with an ``X-Vault-Token`` header and
    returns the configured ``field`` from the secret's data (or the sole value when
    the secret has exactly one key). Fetch failures return None (fail-closed at the
    caller). Contract-tested with respx.
    """

    def __init__(
        self,
        *,
        addr: str,
        token: str,
        mount: str = "secret",
        field: str = "value",
        timeout: float = 5.0,
    ) -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._mount = mount.strip("/")
        self._field = field
        self._timeout = timeout

    def fetch(self, name: str) -> Optional[str]:
        if not _SAFE_NAME_RE.fullmatch(str(name)):
            return None
        import httpx

        url = f"{self._addr}/v1/{self._mount}/data/{name}"
        try:
            resp = httpx.get(url, headers={"X-Vault-Token": self._token}, timeout=self._timeout)
        except Exception as exc:
            logger.warning("[SecretBroker] vault fetch failed for %r: %s", name, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()["data"]["data"]
        except Exception:
            return None
        if not isinstance(data, dict) or not data:
            return None
        if self._field in data:
            value = data[self._field]
        elif len(data) == 1:
            value = next(iter(data.values()))
        else:
            return None
        return str(value) if value not in (None, "") else None


class ChainSecretBroker(SecretBroker):
    """Try each backend in order; first non-empty value wins (e.g. env → file → vault)."""

    def __init__(self, *brokers: SecretBroker) -> None:
        self._brokers = brokers

    def fetch(self, name: str) -> Optional[str]:
        for broker in self._brokers:
            try:
                value = broker.fetch(name)
            except Exception:
                value = None
            if value:
                return value
        return None


# ── Ambient capability scope (set by the tool seam) ───────────────────────────
# execute_tool wraps a tool's invocation in capability_scope(token_capabilities);
# a tool then calls resolve_secret(name) and it is gated by the run's grants
# without the tool threading capabilities through every call.
_CAPABILITIES_CTX: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "secret_broker_capabilities", default=()
)


@contextlib.contextmanager
def capability_scope(capabilities: Any):
    token = _CAPABILITIES_CTX.set(tuple(capabilities or ()))
    try:
        yield
    finally:
        _CAPABILITIES_CTX.reset(token)


def current_capabilities() -> tuple:
    return _CAPABILITIES_CTX.get()


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


def load_secret_scopes_from_env(raw: "Optional[str]" = None) -> int:
    """MEB-2a: register secret scopes from ``AINDY_SECRET_SCOPES`` (JSON).

    Format: ``{"<secret_name>": "<required_capability>"}`` — locks a secret name to a
    capability so ``resolve_secret`` is fail-closed unless the run holds that capability.
    Empty/absent config is a no-op. Returns the number of scopes registered.
    """
    import json
    import logging
    import os

    _logger = logging.getLogger(__name__)
    if raw is None:
        raw = os.getenv("AINDY_SECRET_SCOPES", "").strip()
    if not raw:
        return 0
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _logger.error("[secret_broker] AINDY_SECRET_SCOPES is not valid JSON: %s", exc)
        return 0
    if not isinstance(data, dict):
        _logger.error("[secret_broker] AINDY_SECRET_SCOPES must be a JSON object")
        return 0
    count = 0
    for name, cap in data.items():
        if not isinstance(cap, str) or not cap:
            _logger.warning("[secret_broker] skipping secret scope %r: capability must be a string", name)
            continue
        register_secret_scope(str(name), cap)
        count += 1
    if count:
        _logger.info("[secret_broker] registered %d secret scope(s) from AINDY_SECRET_SCOPES", count)
    return count


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

    ``capabilities`` defaults to the ambient ``capability_scope`` set by the tool
    seam, so a tool can call ``resolve_secret(name)`` and be gated by the run's grants.
    """
    name = str(name)
    if capabilities is None:
        capabilities = _CAPABILITIES_CTX.get()
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
