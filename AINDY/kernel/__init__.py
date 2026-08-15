"""A.I.N.D.Y. kernel — core primitives.

Submodules (import these directly):
    syscall_dispatcher, syscall_registry, scheduler_engine, event_bus,
    circuit_breaker, resource_manager, tenant_context, clock, effect_ledger

This file previously contained a **byte-identical copy of `tenant_context.py`** —
all 171 lines, present since the initial repo extraction (0d5d382) and never
touched since. That made `from AINDY.kernel import TenantContext` and
`from AINDY.kernel.tenant_context import TenantContext` return *two different
classes*: `isinstance` across them would silently fail, and a fix applied to one
would not reach the other (which is how it was found — while fixing
TENANT-FROZEN-SHALLOW-1, the second copy would have kept the mutable field).

Nothing depended on it: every `from AINDY.kernel import X` in this repo and in
`aindy-apps-monolith` imports a *submodule*, not a name from here. It is now a
re-export of the single definition, so the package-root path keeps working and
resolves to the same class object.

Re-exporting these names is safe from the `AINDY.routes` shadowing hazard noted in
CLAUDE.md: none of them collides with a submodule name, so
`from AINDY.kernel import tenant_context` still yields the module.
"""
from AINDY.kernel.tenant_context import (
    RESOURCE_LIMIT_EXCEEDED,
    TENANT_VIOLATION,
    TenantContext,
    build_tenant_context,
    tenant_context_from_syscall_context,
)

__all__ = [
    "RESOURCE_LIMIT_EXCEEDED",
    "TENANT_VIOLATION",
    "TenantContext",
    "build_tenant_context",
    "tenant_context_from_syscall_context",
]
