"""
agents/capability_policy.py - Declarative per-capability policy (AGENT-HARDEN-8).

Beyond the coarse per-verb capability check + per-EU/tenant quota, a capability may
declare a bound the dispatcher enforces per call:

  - recipients — an allowlist of permitted targets (e.g. email addresses / handles);
                 recipient targets in the call args outside it are denied.
  - domains    — a domain egress allowlist (upgrading the coarse ``egress_scope``
                 label to an enforced list); URL hosts in the call args outside it
                 are denied. Complements the SSRF ``validate_outbound_extension_url``
                 blocklist.
  - rate       — a ``"N/period"`` string (enforced via Redis counters — PR2).

Enforcement is **vacuous until a policy is registered**: with no policy for a
capability, ``enforce_capability_policy`` allows the call, so behavior is unchanged
until an operator opts in. Recipient/domain candidates are extracted generically
from the call args (emails and URL hosts), so no per-tool arg schema is required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


@dataclass(frozen=True)
class CapabilityPolicy:
    """A declarative bound attached to a capability. ``None`` = unrestricted."""

    recipients: Optional[tuple[str, ...]] = None
    domains: Optional[tuple[str, ...]] = None
    rate: Optional[str] = None  # "N/period" — enforced in PR2 (Redis counters)


# Process-wide registry. Populated declaratively (register_capability_policy);
# a config/plugin-driven source is a follow-up.
CAPABILITY_POLICIES: dict[str, CapabilityPolicy] = {}


def register_capability_policy(capability: str, policy: CapabilityPolicy) -> None:
    CAPABILITY_POLICIES[str(capability)] = policy


def get_capability_policy(capability: str) -> Optional[CapabilityPolicy]:
    return CAPABILITY_POLICIES.get(str(capability))


def clear_capability_policies() -> None:
    """Test helper — reset the registry."""
    CAPABILITY_POLICIES.clear()


def has_capability_policies() -> bool:
    return bool(CAPABILITY_POLICIES)


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _iter_strings(v)


def extract_recipients(args: Any) -> set[str]:
    """Email-like targets found anywhere in the call args."""
    found: set[str] = set()
    for s in _iter_strings(args):
        found.update(m.lower() for m in _EMAIL_RE.findall(s))
    return found


def extract_domains(args: Any) -> set[str]:
    """URL hosts found anywhere in the call args (lowercased, no port)."""
    found: set[str] = set()
    for s in _iter_strings(args):
        for url in _URL_RE.findall(s):
            host = (urlparse(url).hostname or "").lower()
            if host:
                found.add(host)
    return found


def _recipient_allowed(recipient: str, allowlist: tuple[str, ...]) -> bool:
    recipient = recipient.lower()
    for entry in allowlist:
        entry = str(entry).lower()
        if entry == recipient:
            return True
        # A domain-only entry ("@example.com" or "example.com") allows any address
        # at that domain.
        domain = entry[1:] if entry.startswith("@") else entry
        if "@" not in entry and recipient.endswith("@" + domain):
            return True
        if entry.startswith("@") and recipient.endswith(entry):
            return True
    return False


def _domain_allowed(domain: str, allowlist: tuple[str, ...]) -> bool:
    domain = domain.lower()
    for entry in allowlist:
        entry = str(entry).lower().lstrip(".")
        if domain == entry or domain.endswith("." + entry):
            return True
    return False


def enforce_capability_policy(capabilities: Any, args: Any) -> dict[str, Any]:
    """Enforce the declared recipient/domain bounds for *capabilities* against *args*.

    Returns ``{"allowed": bool, "violations": [{capability, kind, value}]}``. A call
    is denied if any policy-bound capability has a recipient/domain target in the
    args outside its allowlist. Vacuously allowed when no capability has a policy.
    """
    caps = list(capabilities or [])
    policies = [(c, get_capability_policy(c)) for c in caps]
    policies = [(c, p) for c, p in policies if p is not None and (p.recipients is not None or p.domains is not None)]
    if not policies:
        return {"allowed": True, "violations": []}

    recipients = extract_recipients(args)
    domains = extract_domains(args)
    violations: list[dict[str, Any]] = []
    for capability, policy in policies:
        if policy.recipients is not None:
            for r in recipients:
                if not _recipient_allowed(r, policy.recipients):
                    violations.append({"capability": capability, "kind": "recipient", "value": r})
        if policy.domains is not None:
            for d in domains:
                if not _domain_allowed(d, policy.domains):
                    violations.append({"capability": capability, "kind": "domain", "value": d})
    return {"allowed": not violations, "violations": violations}
