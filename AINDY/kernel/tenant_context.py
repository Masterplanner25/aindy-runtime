"""
Tenant Context — A.I.N.D.Y. OS Isolation Layer

Every execution in A.I.N.D.Y. runs inside a TenantContext. This module
defines the tenant isolation boundary and enforces the invariant that:

  - Memory access is namespaced to the tenant
  - Cross-tenant reads raise PermissionError
  - Every ExecutionUnit carries a tenant_id

Tenant model
------------
A.I.N.D.Y. uses a single-user-per-tenant model: tenant_id == user_id.
This is explicit in TenantContext to allow future multi-user tenants
without changing the isolation contract.

Memory path contract
--------------------
All memory operations must be scoped to:

    /memory/{tenant_id}/...

Callers that violate this raise TENANT_VIOLATION.

Usage
-----
    from AINDY.kernel.tenant_context import TenantContext, build_tenant_context

    ctx = build_tenant_context(user_id="user-123", capability_scope=["memory.read"])
    ctx.assert_memory_path(f"/memory/{ctx.tenant_id}/node-abc")  # OK
    ctx.assert_memory_path("/memory/other-tenant/node-xyz")       # PermissionError
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Structured error code returned in RESOURCE_LIMIT_EXCEEDED and TENANT_VIOLATION
TENANT_VIOLATION = "TENANT_VIOLATION"

#: The memory address space root. Owned here so the ONE tenant-path rule below can be stated
#: without importing the memory layer (which imports this module).
MEMORY_ROOT = "/memory"

_MULTI_SLASH = re.compile(r"/+")


def tenant_owns_memory_path(path: str, tenant_id: str) -> bool:
    """THE tenant-path rule: is *path* inside *tenant_id*'s memory namespace?

    One rule, one home. `TenantContext.validate_memory_path` and
    `memory_address_space.validate_tenant_path` both used to implement this independently and
    disagreed on two strings — the exact tenant root (`/memory/t1`: MAS accepted, the kernel
    refused) and a doubled slash (`/memory//x` under an empty tenant: the kernel's raw
    `startswith` accepted, MAS's normalised path refused). Both now ask here.

    * Consecutive slashes collapse and a trailing slash is dropped before comparison, so the
      answer does not depend on how the caller spelled the path.
    * The exact tenant root (`/memory/{tenant}`) IS inside the namespace — it is the tenant's
      own tree, and MAS's tree/list routes address it.
    * An empty tenant owns NOTHING — by construction, not by a branch: its root would be
      `/memory/`, which no normalised path equals or sits under (`/memory//…` cannot survive
      the slash collapse). Pinned by `test_empty_user_id_owns_no_memory_path`; an explicit
      `if not tenant_id` guard was tried and no input could reach it. The dispatcher refuses
      an empty tenant at step 2b anyway; this is the same answer one layer down.
    * The prefix's trailing slash is load-bearing: `t1` never authorises `t12`.
    """
    cleaned = _MULTI_SLASH.sub("/", str(path or "").strip())
    if cleaned != MEMORY_ROOT and cleaned.endswith("/"):
        cleaned = cleaned.rstrip("/")
    root = f"{MEMORY_ROOT}/{tenant_id}"
    return cleaned == root or cleaned.startswith(root + "/")
RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class TenantContext:
    """Immutable tenant isolation context.

    Attributes:
        tenant_id:        The tenant's unique identifier (== user_id in A.I.N.D.Y.).
        user_id:          Authenticated user ID within the tenant.
        namespace:        Canonical tenant namespace prefix: "tenant:{tenant_id}".
        capability_scope: Granted capabilities for this context. A **tuple**, not a
                          list: `frozen=True` prevents attribute rebinding but does
                          not deep-freeze, so a list field could be appended to
                          in place — adding a capability to a live security context
                          that the type claims cannot change (TENANT-FROZEN-SHALLOW-1).
                          `in`, `len()` and iteration are unchanged; only mutation
                          differs, and it now raises AttributeError.
    """

    tenant_id: str
    user_id: str
    namespace: str
    capability_scope: tuple[str, ...] = field(default_factory=tuple)

    # ── Memory path enforcement ───────────────────────────────────────────────

    def memory_prefix(self) -> str:
        """Return the canonical memory namespace prefix for this tenant."""
        return f"{MEMORY_ROOT}/{self.tenant_id}/"

    def validate_memory_path(self, path: str) -> bool:
        """Return True if *path* is within this tenant's memory namespace.

        Delegates to :func:`tenant_owns_memory_path` — the same rule MAS's
        ``validate_tenant_path`` applies, so the two guards cannot disagree.
        """
        return tenant_owns_memory_path(path, self.tenant_id)

    def assert_memory_path(self, path: str) -> None:
        """Raise PermissionError if *path* is outside the tenant namespace.

        Args:
            path: Memory path to validate (e.g. "/memory/{tenant_id}/node-abc").

        Raises:
            PermissionError: TENANT_VIOLATION — path belongs to another tenant.
        """
        if not self.validate_memory_path(path):
            raise PermissionError(
                f"{TENANT_VIOLATION}: memory path {path!r} is outside "
                f"tenant namespace {self.memory_prefix()!r}"
            )

    # ── Cross-tenant guard ────────────────────────────────────────────────────

    def assert_same_tenant(self, other_tenant_id: str) -> None:
        """Raise PermissionError if *other_tenant_id* differs from this tenant.

        Args:
            other_tenant_id: The tenant_id of the resource being accessed.

        Raises:
            PermissionError: TENANT_VIOLATION — cross-tenant access attempted.
        """
        if str(other_tenant_id) != str(self.tenant_id):
            raise PermissionError(
                f"{TENANT_VIOLATION}: tenant {self.tenant_id!r} attempted to "
                f"access resource owned by tenant {other_tenant_id!r}"
            )

    # ── Capability check ──────────────────────────────────────────────────────

    def has_capability(self, cap: str) -> bool:
        """Return True if *cap* is in this context's capability_scope."""
        return cap in self.capability_scope

    def assert_capability(self, cap: str) -> None:
        """Raise PermissionError if *cap* is not in capability_scope.

        Raises:
            PermissionError: TENANT_VIOLATION — capability not granted.
        """
        if not self.has_capability(cap):
            raise PermissionError(
                f"{TENANT_VIOLATION}: tenant {self.tenant_id!r} does not have "
                f"capability {cap!r}; granted: {self.capability_scope}"
            )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TenantContext(tenant_id={self.tenant_id!r}, "
            f"namespace={self.namespace!r}, "
            f"caps={len(self.capability_scope)})"
        )


# ── Builder helpers ───────────────────────────────────────────────────────────

def build_tenant_context(
    user_id: str,
    capability_scope: list[str] | None = None,
    tenant_id: str | None = None,
) -> TenantContext:
    """Build a TenantContext for the given user.

    In A.I.N.D.Y.'s single-user-per-tenant model, tenant_id defaults to
    user_id when not explicitly supplied.

    Args:
        user_id:          Authenticated user ID.
        capability_scope: Granted capabilities. Any iterable; stored as a tuple.
                          Defaults to ().
        tenant_id:        Explicit tenant override. Defaults to user_id.

    Returns:
        A frozen TenantContext ready for use in execution.
    """
    resolved_tenant = str(tenant_id or user_id or "")
    resolved_user = str(user_id or "")
    return TenantContext(
        tenant_id=resolved_tenant,
        user_id=resolved_user,
        namespace=f"tenant:{resolved_tenant}",
        capability_scope=tuple(capability_scope or ()),
    )


def tenant_context_from_syscall_context(syscall_ctx) -> TenantContext:
    """Derive a TenantContext from an existing SyscallContext.

    Args:
        syscall_ctx: A ``SyscallContext`` instance (kernel.syscall_registry).

    Returns:
        TenantContext with tenant_id == syscall_ctx.user_id.
    """
    return build_tenant_context(
        user_id=str(syscall_ctx.user_id or ""),
        capability_scope=tuple(getattr(syscall_ctx, "capabilities", ()) or ()),
    )
