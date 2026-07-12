"""
Syscall Registry — A.I.N.D.Y. system call table.

Maps sys.v{N}.{domain}.{action} names to handler functions, required
capabilities, and ABI contracts.  Handlers are plain callables — no HTTP,
no FastAPI dependencies.

Registry structure
------------------
``SYSCALL_REGISTRY`` is a ``VersionedSyscallRegistry`` that supports both:

Flat key access (backward compatible)::

    entry = SYSCALL_REGISTRY["sys.v1.memory.read"]
    SYSCALL_REGISTRY["sys.v2.memory.read"] = SyscallEntry(...)

Versioned view::

    view = SYSCALL_REGISTRY.versioned        # {"v1": {"memory.read": entry}, …}
    v1   = SYSCALL_REGISTRY.get_version("v1")

Handler contract
----------------
Every handler must accept (payload: dict, context: SyscallContext) -> dict.
Handlers may raise — the dispatcher catches and wraps all exceptions.
Handlers may reuse a caller-provided SQLAlchemy session via
``context.metadata["_db"]``. If no session is provided, the handler opens and
owns its own session. Caller-owned sessions must never be committed, rolled
back, or closed by the handler.

ABI contract
------------
Each SyscallEntry carries:
  input_schema  — lightweight schema validated before execution
  output_schema — shape validated after execution (non-fatal)
  stable        — False marks the syscall as experimental
  deprecated    — True causes the dispatcher to emit a warning
  replacement   — full syscall name callers should migrate to
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)


# ── Execution context ─────────────────────────────────────────────────────────

@dataclass
class SyscallContext:
    """Immutable execution context passed into every syscall handler.

    Built by the dispatcher caller (e.g. NodusRuntimeAdapter) before
    dispatching. Handlers must not mutate this object.

    Attributes:
        execution_unit_id: Correlates to the active ExecutionUnit / FlowRun.
        user_id:           Authenticated caller; used for ownership enforcement.
        capabilities:      Explicit set of granted syscall capabilities.
        trace_id:          Propagated trace ID (equals execution_unit_id in
                           standard PersistentFlowRunner runs).
        memory_context:    Pre-loaded memory nodes available to the script.
        metadata:          Arbitrary caller-supplied key/value pairs.
    """
    execution_unit_id: str
    user_id: str
    capabilities: list[str]
    trace_id: str
    memory_context: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def db(self):
        return self.metadata.get("_db")


def _acquire_handler_db(context: SyscallContext) -> tuple[Any, bool]:
    """Return (db, owns_session) for a syscall handler.

    Contract:
    - If ``context.metadata["_db"]`` is present, the caller owns that session.
      Handlers may use it but must not close it and must not commit/rollback it.
    - Otherwise the handler opens ``SessionLocal()`` and owns cleanup.
    """
    external_db = context.metadata.get("_db")
    if external_db is not None:
        return external_db, False

    from AINDY.db.database import SessionLocal

    return SessionLocal(), True


def _finish_handler_write(db: Any, *, owns_session: bool, success: bool) -> None:
    """Finalize DB state for write-capable handlers.

    Caller-owned sessions keep transaction ownership outside the syscall
    boundary: handlers only ``flush()`` on success and never close.
    Handler-owned sessions commit/rollback and close locally.
    """
    if owns_session:
        try:
            if success:
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()
        return

    if success:
        db.flush()
        return

    try:
        db.rollback()
    except Exception:
        logger.debug("[syscall_registry] rollback on external DB session failed", exc_info=True)


def _resolve_tenant_user_id(context: SyscallContext, payload: dict[str, Any]) -> str | None:
    """Return the tenant-scoped user_id a handler may operate on.

    The caller context is authoritative. Handlers may accept an optional
    ``payload["user_id"]`` only when it matches the caller's tenant.
    """
    from AINDY.platform_layer.user_ids import parse_user_id

    context_user_id = parse_user_id(context.user_id)
    if context_user_id is None:
        return None

    requested_user_id = payload.get("user_id")
    if requested_user_id is None:
        return context_user_id

    normalized_requested = parse_user_id(requested_user_id)
    if normalized_requested is None:
        return None
    if normalized_requested != context_user_id:
        raise PermissionError(
            f"TENANT_VIOLATION: syscall may not access user_id {requested_user_id!r} "
            f"from tenant context {context.user_id!r}"
        )
    return context_user_id


# ── Capability constants ──────────────────────────────────────────────────────

# Default capability set granted to all Nodus script executions.
DEFAULT_NODUS_CAPABILITIES: list[str] = [
    "memory.read",
    "memory.write",
    "memory.search",
    "event.emit",
    "execution.read",
]


# ── Registry entry ────────────────────────────────────────────────────────────

class SyscallEntry:
    """Binds a handler callable to its required capability and ABI contract.

    All parameters after *description* are optional and default to safe values
    so existing code that constructs ``SyscallEntry(handler, capability)``
    continues to work without any changes.
    """

    __slots__ = (
        "handler", "capability", "description",
        "input_schema", "output_schema",
        "stable", "deprecated", "deprecated_since", "replacement",
        "compensate", "execution_guarantee",
    )

    def __init__(
        self,
        handler: Callable[[dict, SyscallContext], dict],
        capability: str,
        description: str = "",
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        stable: bool = True,
        deprecated: bool = False,
        deprecated_since: str | None = None,
        replacement: str | None = None,
        compensate: Callable[[dict, "SyscallContext"], dict | None] | None = None,
        execution_guarantee: str = "AT_LEAST_ONCE",
    ) -> None:
        self.handler = handler
        self.capability = capability
        self.description = description
        self.input_schema: dict = input_schema or {}
        self.output_schema: dict = output_schema or {}
        self.stable = stable
        self.deprecated = deprecated
        self.deprecated_since = deprecated_since
        self.replacement = replacement
        # MEB-1b: per-syscall idempotency declaration — "AT_LEAST_ONCE" (default) or
        # "EXACTLY_ONCE". EXACTLY_ONCE opts the syscall into the dispatcher effect
        # boundary (dedup per (execution_unit_id, syscall, payload)), and only when the
        # global AINDY_SYSCALL_IDEMPOTENCY flag is also on. This is the addressable
        # guarantee source that replaces the dead ExecutionUnit.extra lookup (IDEM-10).
        self.execution_guarantee = execution_guarantee
        # AGENT-HARDEN-3: optional compensating-undo hook. When set, a completed
        # effect of this syscall is *reversible* — undo_run_effects invokes it with
        # (effect: dict, context) to roll the effect back. None → the effect is
        # irreversible and is surfaced (never silently skipped) during undo.
        self.compensate = compensate

    @property
    def reversible(self) -> bool:
        """True when this syscall declares a compensating-undo hook (AGENT-HARDEN-3)."""
        return self.compensate is not None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SyscallEntry(capability={self.capability!r}, "
            f"handler={self.handler.__name__!r}, "
            f"deprecated={self.deprecated!r})"
        )


# ── Versioned registry ────────────────────────────────────────────────────────

class VersionedSyscallRegistry(MutableMapping):
    """MutableMapping that supports BOTH flat and versioned access patterns.

    **Flat access (backward compatible)**::

        registry["sys.v1.memory.read"]       # → SyscallEntry
        registry["sys.v1.memory.read"] = e   # write
        del registry["sys.v1.memory.read"]   # delete
        registry.get("sys.v1.memory.read")   # get-with-default
        "sys.v1.memory.read" in registry     # containment
        list(registry.keys())                # all registered full names

    **Versioned access**::

        registry.versioned                   # {"v1": {"memory.read": e}, …}
        registry.get_version("v1")           # {"memory.read": entry, …}
        registry.versions()                  # ["v1", "v2", …]

    Write operations (``__setitem__``, ``__delitem__``, ``pop``) keep
    both views in sync automatically.
    """

    def __init__(self) -> None:
        self._flat: dict[str, SyscallEntry] = {}
        self._versioned: dict[str, dict[str, SyscallEntry]] = {}

    # ── dict-like interface ────────────────────────────────────────────────

    @staticmethod
    def _split(key: str) -> tuple[str | None, str | None]:
        """Return (version, action) from 'sys.v1.memory.read', or (None, None)."""
        if not key.startswith("sys."):
            return None, None
        rest = key[4:]
        dot = rest.find(".")
        if dot == -1:
            return None, None
        return rest[:dot], rest[dot + 1:]

    def __getitem__(self, key: str) -> SyscallEntry:
        return self._flat[key]

    def __setitem__(self, key: str, value: SyscallEntry) -> None:
        if key in self._flat:
            existing = self._flat[key]
            if existing.handler is not value.handler:
                raise ValueError(
                    f"[SyscallRegistry] Syscall '{key}' is already registered with a "
                    f"different handler. Previous: "
                    f"{getattr(existing.handler, '__qualname__', repr(existing.handler))!r}  "
                    f"New: {getattr(value.handler, '__qualname__', repr(value.handler))!r}. "
                    "Each syscall must have exactly one registration point."
                )
        self._flat[key] = value
        version, action = self._split(key)
        if version and action:
            if version not in self._versioned:
                self._versioned[version] = {}
            self._versioned[version][action] = value

    def __delitem__(self, key: str) -> None:
        del self._flat[key]
        version, action = self._split(key)
        if version and action and version in self._versioned:
            self._versioned[version].pop(action, None)
            if not self._versioned[version]:
                del self._versioned[version]

    def __iter__(self) -> Iterator[str]:
        return iter(self._flat)

    def __len__(self) -> int:
        return len(self._flat)

    def __contains__(self, key: object) -> bool:
        return key in self._flat

    def pop(self, key: str, *args) -> SyscallEntry:  # type: ignore[override]
        val = self._flat.pop(key, *args)
        version, action = self._split(key)
        if version and action and version in self._versioned:
            self._versioned[version].pop(action, None)
            if not self._versioned[version]:
                del self._versioned[version]
        return val

    # ── Versioned views ────────────────────────────────────────────────────

    @property
    def versioned(self) -> dict[str, dict[str, SyscallEntry]]:
        """Return a shallow copy of the versioned registry."""
        return {v: dict(actions) for v, actions in self._versioned.items()}

    def get_version(self, version: str) -> dict[str, SyscallEntry]:
        """Return all entries registered under *version*."""
        return dict(self._versioned.get(version, {}))

    def versions(self) -> list[str]:
        """Return a sorted list of registered version strings."""
        return sorted(self._versioned.keys())


# ── Handlers ──────────────────────────────────────────────────────────────────
# Each handler:
#   - Accepts (payload: dict, context: SyscallContext)
#   - Returns a plain dict (becomes the "data" field in the response envelope)
#   - May reuse context.metadata["_db"] when the caller owns the transaction;
#     otherwise the handler opens and owns its own SessionLocal().
#   - May raise ValueError for bad payload; other exceptions = handler failure


def _handle_memory_read(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.read — recall memory nodes for the calling user.

    Payload keys (all optional):
        path      (str)        — MAS path or wildcard expression (overrides tag/query if exact)
        query     (str)        — semantic search string
        tags      (list[str])  — tag filter
        limit     (int)        — max results, default 5
        node_type (str)        — filter by node_type
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    path: str | None = payload.get("path")
    query: str | None = payload.get("query")
    tags: list | None = payload.get("tags")
    limit: int = int(payload.get("limit", 5))
    node_type: str | None = payload.get("node_type")

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        if path:
            nodes = dao.query_path(
                path_expr=path,
                query=query,
                tags=tags,
                user_id=context.user_id,
                limit=limit,
            )
        else:
            nodes = dao.recall(
                query=query,
                tags=tags,
                limit=limit,
                user_id=context.user_id,
                node_type=node_type,
            )
        return {"nodes": nodes, "count": len(nodes)}
    finally:
        if owns_session:
            db.close()


def _handle_memory_write(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.write — persist a new memory node for the calling user.

    Payload keys:
        content      (str)        — required; node text
        tags         (list[str])  — optional; classification tags
        node_type    (str)        — default "insight"
        significance (float)      — relevance weight 0.0-1.0, default 0.5
        path         (str)        — optional MAS path; auto-generated if omitted
        namespace    (str)        — optional namespace segment
        addr_type    (str)        — optional sub-category segment
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
    from AINDY.memory.memory_address_space import path_from_write_payload

    content: str = payload.get("content", "")
    if not content:
        raise ValueError("sys.v1.memory.write requires non-empty 'content'")

    tags: list = payload.get("tags") or []
    node_type: str = payload.get("node_type", "insight")
    source: str = payload.get("source", "syscall")

    full_path, namespace, addr_type = path_from_write_payload(
        {**payload, "node_type": node_type},
        tenant_id=str(context.user_id),
    )
    from AINDY.memory.memory_address_space import parent_path_of
    parent_path = parent_path_of(full_path)

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        node = dao.save(
            content=content,
            tags=tags,
            user_id=context.user_id,
            node_type=node_type,
            source=source,
            source_agent="syscall_dispatcher",
            extra={"execution_unit_id": context.execution_unit_id},
            path=full_path,
            namespace=namespace,
            addr_type=addr_type,
            parent_path=parent_path,
        )
        result = {"node": node, "path": full_path}
    except Exception:
        _finish_handler_write(db, owns_session=owns_session, success=False)
        raise
    _finish_handler_write(db, owns_session=owns_session, success=True)
    return result


def _handle_memory_delete(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.delete — hard-delete a memory node owned by the caller.

    Payload keys:
        node_id (str) — required; UUID of the node to delete.

    Tenant-scoped and idempotent: deleting a missing node, or a node owned by another
    tenant, returns ``{"deleted": False, "node_id": ...}`` without error and without
    revealing existence. Hard delete — the DB cascades to the node's history, trace
    memberships, causal edges, and links (irreversible).
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    node_id = payload.get("node_id", "")
    if not node_id:
        raise ValueError("sys.v1.memory.delete requires 'node_id'")

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        deleted = dao.delete_by_id(str(node_id), user_id=context.user_id)
        result = {"deleted": bool(deleted), "node_id": str(node_id)}
    except Exception:
        _finish_handler_write(db, owns_session=owns_session, success=False)
        raise
    _finish_handler_write(db, owns_session=owns_session, success=True)
    return result


def _handle_memory_search(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.search — semantic search over the user's memory nodes.

    Payload keys:
        query  (str) — required; search string
        limit  (int) — max results, default 5
        path   (str) — optional MAS path prefix to scope the search
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    query: str = payload.get("query", "")
    if not query:
        raise ValueError("sys.v1.memory.search requires non-empty 'query'")
    limit: int = int(payload.get("limit", 5))
    path: str | None = payload.get("path")

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        if path:
            nodes = dao.query_path(
                path_expr=path,
                query=query,
                user_id=context.user_id,
                limit=limit,
            )
        else:
            nodes = dao.recall(
                query=query,
                limit=limit,
                user_id=context.user_id,
            )
        return {"nodes": nodes, "count": len(nodes)}
    finally:
        if owns_session:
            db.close()


def _handle_memory_list(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.list — list nodes at a MAS path (one level or recursive).

    Payload keys:
        path      (str) — required; MAS prefix (use /* for one level, /** for recursive)
        limit     (int) — max results, default 50
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    path: str = payload.get("path", "")
    if not path:
        raise ValueError("sys.v1.memory.list requires 'path'")
    limit: int = int(payload.get("limit", 50))

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        nodes = dao.query_path(path_expr=path, user_id=context.user_id, limit=limit)
        return {"nodes": nodes, "count": len(nodes), "path": path}
    finally:
        if owns_session:
            db.close()


def _handle_memory_tree(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.tree — return a hierarchical tree of nodes under a path.

    Payload keys:
        path  (str) — required; MAS prefix
        limit (int) — max nodes to fetch before building tree, default 200
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
    from AINDY.memory.memory_address_space import build_tree, wildcard_prefix, is_exact, normalize_path

    path: str = payload.get("path", "")
    if not path:
        raise ValueError("sys.v1.memory.tree requires 'path'")
    limit: int = int(payload.get("limit", 200))

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        if is_exact(path):
            nodes = dao.walk_path(normalize_path(path), user_id=context.user_id, limit=limit)
        else:
            nodes = dao.walk_path(wildcard_prefix(path), user_id=context.user_id, limit=limit)
        tree = build_tree(nodes)
        return {"tree": tree, "node_count": len(nodes), "path": path}
    finally:
        if owns_session:
            db.close()


def _handle_memory_trace(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.memory.trace — follow the causal chain from a node at a path.

    Payload keys:
        path   (str) — required; exact MAS path to start from
        depth  (int) — max hops to follow, default 5
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    path: str = payload.get("path", "")
    if not path:
        raise ValueError("sys.v1.memory.trace requires 'path'")
    depth: int = int(payload.get("depth", 5))

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        chain = dao.causal_trace(path=path, depth=depth, user_id=context.user_id)
        return {"chain": chain, "depth": len(chain), "path": path}
    finally:
        if owns_session:
            db.close()


def _handle_flow_run(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.flow.run — execute a registered flow by name.

    Payload keys:
        flow_name     (str)  — required; must exist in FLOW_REGISTRY
        initial_state (dict) — optional; passed to PersistentFlowRunner.start()
        workflow_type (str)  — optional; default "syscall"

    Context metadata keys (internal use):
        _db  — caller-provided SQLAlchemy Session. When present the handler
               uses it directly and skips close() so the caller's transaction
               boundary is preserved. When absent the handler opens and closes
               its own session.
    """
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner

    flow_name: str = payload.get("flow_name", "")
    if not flow_name:
        raise ValueError("sys.v1.flow.run requires 'flow_name'")

    flow = FLOW_REGISTRY.get(flow_name)
    if flow is None:
        raise ValueError(
            f"sys.v1.flow.run: unknown flow '{flow_name}' — "
            f"not registered. Available: {sorted(FLOW_REGISTRY.keys())}"
        )

    initial_state: dict = dict(payload.get("initial_state") or {})
    workflow_type: str = payload.get("workflow_type", flow_name)
    extension_call = (
        dict(context.metadata.get("_extension_call"))
        if isinstance(context.metadata, dict) and isinstance(context.metadata.get("_extension_call"), dict)
        else None
    )
    if extension_call is not None:
        initial_state.setdefault(
            "_runtime_extension_scope",
            {
                "tenant_user_id": str(context.user_id or ""),
                "extension_name": str(extension_call.get("extension_name") or ""),
                "owner_class": str(extension_call.get("owner_class") or ""),
                "operation": str(extension_call.get("operation") or "flow.run"),
            },
        )

    db, owns_session = _acquire_handler_db(context)
    try:
        runner = PersistentFlowRunner(
            flow=flow,
            db=db,
            user_id=context.user_id,
            workflow_type=workflow_type,
        )
        result = runner.start(initial_state, flow_name=flow_name)
        return {"flow_result": result}
    finally:
        if owns_session:
            db.close()


def _handle_event_emit(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.event.emit — emit a SystemEvent on the A.I.N.D.Y. event bus.

    Payload keys:
        event_type (str)  — required; e.g. "operation.completed"
        payload    (dict) — optional; merged into the event payload
    """
    from AINDY.core.system_event_service import emit_system_event

    event_type: str = payload.get("event_type", "")
    if not event_type:
        raise ValueError("sys.v1.event.emit requires 'event_type'")

    event_payload: dict = dict(payload.get("payload") or {})
    extension_call = (
        dict(context.metadata.get("_extension_call"))
        if isinstance(context.metadata, dict) and isinstance(context.metadata.get("_extension_call"), dict)
        else None
    )
    source = "syscall_dispatcher"
    if extension_call is not None:
        event_payload.setdefault(
            "_runtime_extension_scope",
            {
                "tenant_user_id": str(context.user_id or ""),
                "extension_name": str(extension_call.get("extension_name") or ""),
                "owner_class": str(extension_call.get("owner_class") or ""),
                "operation": str(extension_call.get("operation") or "event.emit"),
            },
        )
        extension_name = str(extension_call.get("extension_name") or "").strip()
        if extension_name:
            source = f"extension:{extension_name}"

    db, owns_session = _acquire_handler_db(context)
    try:
        event_id = emit_system_event(
            db=db,
            event_type=event_type,
            user_id=context.user_id,
            trace_id=context.trace_id,
            source=source,
            payload={
                **event_payload,
                "execution_unit_id": context.execution_unit_id,
            },
        )
        result = {"event_id": str(event_id) if event_id else None}
    except Exception:
        _finish_handler_write(db, owns_session=owns_session, success=False)
        raise
    _finish_handler_write(db, owns_session=owns_session, success=True)
    return result


# ── Example v2 handler ────────────────────────────────────────────────────────
# Demonstrates ABI evolution: v2.memory.read adds structured ``filters``
# without breaking the v1 interface.

def _handle_memory_read_v2(payload: dict, context: SyscallContext) -> dict:
    """sys.v2.memory.read — enhanced recall with structured field filters.

    Extends v1 with:
        filters (dict) — optional; key/value field filters applied after recall.
            Supported keys: memory_type, node_type, min_impact (float).

    All v1 payload keys remain valid.  If *filters* is absent the response is
    identical to sys.v1.memory.read.
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    path: str | None = payload.get("path")
    query: str | None = payload.get("query")
    tags: list | None = payload.get("tags")
    limit: int = int(payload.get("limit", 5))
    node_type: str | None = payload.get("node_type")
    filters: dict = payload.get("filters") or {}

    db, owns_session = _acquire_handler_db(context)
    try:
        dao = MemoryNodeDAO(db)
        if path:
            nodes = dao.query_path(path_expr=path, query=query, tags=tags,
                                   user_id=context.user_id, limit=limit)
        else:
            nodes = dao.recall(query=query, tags=tags, limit=limit * 2,
                               user_id=context.user_id, node_type=node_type)

        # Apply structured filters (v2 extension)
        if filters:
            if "memory_type" in filters:
                nodes = [n for n in nodes if n.get("memory_type") == filters["memory_type"]]
            if "node_type" in filters:
                nodes = [n for n in nodes if n.get("node_type") == filters["node_type"]]
            if "min_impact" in filters:
                min_imp = float(filters["min_impact"])
                nodes = [n for n in nodes if (n.get("impact_score") or 0.0) >= min_imp]

        return {"nodes": nodes[:limit], "count": min(len(nodes), limit), "version": "v2"}
    finally:
        if owns_session:
            db.close()


# ── Execution entry-point handlers ───────────────────────────────────────────
# These wrap the four top-level execution entry points so ALL code paths go
# through the syscall layer. Internal proxies (run_flow, execute_intent, …)
# call these handlers; direct callers use the public proxy functions instead.


def _handle_flow_execute_intent(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.flow.execute_intent — top-level intent execution with strategy selection.

    Payload keys:
        intent_data (dict) — required; at minimum {"workflow_type": "..."}

    Context metadata keys (internal use):
        _db — caller-provided SQLAlchemy Session (transaction preserved).
    """
    intent_data: dict = payload.get("intent_data") or {}
    if not intent_data:
        raise ValueError("sys.v1.flow.execute_intent requires non-empty 'intent_data'")

    db, owns_session = _acquire_handler_db(context)
    try:
        from AINDY.runtime.flow_engine import _execute_intent_direct
        result = _execute_intent_direct(
            intent_data=intent_data,
            db=db,
            user_id=context.user_id,
        )
        return {"intent_result": result}
    finally:
        if owns_session:
            db.close()


def _handle_nodus_execute(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.nodus.execute — execute a Nodus script via flow-backed orchestration.

    Payload keys:
        script           (str)        — required; Nodus source code
        input_payload    (dict)       — optional; script input variables
        error_policy     (str)        — optional; "halt" (default) or "continue"
        workflow_type    (str)        — optional; default "nodus_execute"
        trace_id         (str)        — optional; correlation ID
        node_max_retries (int)        — optional; per-node retry override

    Context metadata keys (internal use):
        _db                 — caller-provided SQLAlchemy Session.
        _extra_initial_state — extra keys merged into initial flow state.
    """
    script: str = payload.get("script", "")
    if not script:
        raise ValueError("sys.v1.nodus.execute requires 'script'")

    input_payload: dict = payload.get("input_payload") or {}
    error_policy: str = payload.get("error_policy", "halt")
    workflow_type: str = payload.get("workflow_type", "nodus_execute")
    trace_id: str | None = payload.get("trace_id")
    node_max_retries = payload.get("node_max_retries")
    extra_initial_state: dict | None = context.metadata.get("_extra_initial_state")

    db, owns_session = _acquire_handler_db(context)
    try:
        from AINDY.runtime.nodus_execution_service import _run_nodus_via_flow_direct
        result = _run_nodus_via_flow_direct(
            script=script,
            input_payload=input_payload,
            error_policy=error_policy,
            db=db,
            user_id=context.user_id,
            workflow_type=workflow_type,
            trace_id=trace_id,
            extra_initial_state=extra_initial_state,
            node_max_retries=node_max_retries,
        )
        return {"nodus_result": result}
    finally:
        if owns_session:
            db.close()


def _handle_job_submit(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.job.submit — submit a named async job to the automation pipeline.

    Payload keys:
        task_name    (str)  — required; name registered in _JOB_REGISTRY
        payload      (dict) — optional; forwarded to the job handler
        source       (str)  — optional; label for the AutomationLog
        max_attempts (int)  — optional; retry budget (default 1)
    """
    task_name: str = payload.get("task_name", "")
    if not task_name:
        raise ValueError("sys.v1.job.submit requires 'task_name'")

    job_payload: dict = payload.get("payload") or {}
    source: str = payload.get("source", "syscall")
    max_attempts: int = int(payload.get("max_attempts", 1))

    from AINDY.platform_layer.async_job_service import submit_async_job
    log_id = submit_async_job(
        task_name=task_name,
        payload=job_payload,
        user_id=context.user_id,
        source=source,
        max_attempts=max_attempts,
    )
    return {"log_id": log_id, "task_name": task_name, "source": source}


def _handle_agent_execute(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.execute — execute an approved AgentRun via the deterministic runtime.

    Payload keys:
        run_id (str) — required; ID of an AgentRun with status "approved"

    Context metadata keys (internal use):
        _db — caller-provided SQLAlchemy Session.
    """
    run_id: str = payload.get("run_id", "")
    if not run_id:
        raise ValueError("sys.v1.agent.execute requires 'run_id'")

    db, owns_session = _acquire_handler_db(context)
    try:
        from AINDY.agents.agent_runtime import execute_run
        result = execute_run(run_id=run_id, user_id=context.user_id, db=db)
        return {"run_result": result}
    finally:
        if owns_session:
            db.close()


def _handle_agent_count_runs(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.count_runs - count AgentRun rows for a user, optionally filtered by status."""
    from AINDY.db.models import AgentRun

    db, owns_session = _acquire_handler_db(context)
    try:
        query = db.query(AgentRun.id)
        normalized_user_id = _resolve_tenant_user_id(context, payload)
        if normalized_user_id is None:
            return {"count": 0}
        query = query.filter(AgentRun.user_id == normalized_user_id)

        status_filter = payload.get("status")
        if status_filter:
            statuses = status_filter if isinstance(status_filter, list) else [status_filter]
            query = query.filter(AgentRun.status.in_(statuses))

        return {"count": query.count()}
    finally:
        if owns_session:
            db.close()


def _handle_agent_list_recent_durations(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.list_recent_durations - list recent AgentRun timing fields for duration calculations."""
    from AINDY.db.models import AgentRun

    db, owns_session = _acquire_handler_db(context)
    window_hours = int(payload.get("window_hours", 1))
    try:
        window_start = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        query = db.query(AgentRun).filter(AgentRun.created_at >= window_start)

        normalized_user_id = _resolve_tenant_user_id(context, payload)
        if normalized_user_id is None:
            return {"durations": [], "count": 0}
        query = query.filter(AgentRun.user_id == normalized_user_id)

        rows = query.all()
        durations = [
            {
                "started_at": (row.started_at or row.created_at).isoformat()
                if (row.started_at or row.created_at)
                else None,
                "completed_at": (row.completed_at or row.started_at or row.created_at).isoformat()
                if (row.completed_at or row.started_at or row.created_at)
                else None,
            }
            for row in rows
        ]
        return {"durations": durations, "count": len(durations)}
    finally:
        if owns_session:
            db.close()


def _handle_agent_list_recent_runs(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.list_recent_runs - list recent AgentRun rows for a user as plain dicts."""
    from AINDY.agents.agent_runtime import run_to_dict
    from AINDY.db.models import AgentRun

    db, owns_session = _acquire_handler_db(context)
    try:
        normalized_user_id = _resolve_tenant_user_id(context, payload)
        if normalized_user_id is None:
            return {"runs": []}

        limit = int(payload.get("limit", 10))
        rows = (
            db.query(AgentRun)
            .filter(AgentRun.user_id == normalized_user_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit)
            .all()
        )
        return {"runs": [run_to_dict(row) for row in rows]}
    finally:
        if owns_session:
            db.close()


def _handle_agent_ensure_initial_run(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.ensure_initial_run - find or create the initial signup AgentRun sentinel for a user."""
    from AINDY.db.models import AgentRun

    db, owns_session = _acquire_handler_db(context)
    try:
        normalized_user_id = _resolve_tenant_user_id(context, payload)
        if normalized_user_id is None:
            return {"run_id": None, "created": False}

        existing = (
            db.query(AgentRun)
            .filter(
                AgentRun.user_id == normalized_user_id,
                AgentRun.goal == "Initial agent context",
            )
            .first()
        )
        if existing:
            return {"run_id": str(existing.id), "created": False}

        run = AgentRun(
            user_id=normalized_user_id,
            goal="Initial agent context",
            status="completed",
            overall_risk="low",
            steps_total=0,
        )
        db.add(run)
        _finish_handler_write(db, owns_session=owns_session, success=True)
        db.refresh(run)
        return {"run_id": str(run.id), "created": True}
    except Exception:
        _finish_handler_write(db, owns_session=owns_session, success=False)
        raise


#: AgentRun statuses from which a cooperative cancel may fire (all non-terminal).
_CANCELLABLE_AGENT_RUN_STATUSES: tuple[str, ...] = (
    "pending_approval",
    "approved",
    "executing",
    "waiting",
    "delegated",
)


def _handle_agent_cancel(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.cancel — cooperatively cancel a non-terminal AgentRun (AGENT-HARDEN-1).

    Flips the run to the terminal ``cancelled`` state via an atomic compare-and-set
    from any active status (``pending_approval`` / ``approved`` / ``executing`` /
    ``waiting`` / ``delegated``). The transition is committed here so it is durable
    and visible across threads: a run mid-execution on the VM-backed segment chain
    observes the flip at the next **segment boundary** and halts before the next
    tool call (mid-tool state is never corrupted), and a parked (``waiting``) run
    never resumes because the scheduler resume claim requires ``status='waiting'``.

    Terminal runs (``completed`` / ``failed`` / ``cancelled``) are an idempotent
    no-op — the existing terminal status is returned untouched.

    Payload keys:
        run_id (str) — required; AgentRun id to cancel.
        reason (str) — optional; recorded in error_message and the CANCELLED event.

    Context metadata keys (internal use):
        _db — caller-provided SQLAlchemy Session.
    """
    import uuid as _uuid

    from AINDY.db.models import AgentRun

    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("sys.v1.agent.cancel requires 'run_id'")

    normalized_user_id = _resolve_tenant_user_id(context, {})
    if normalized_user_id is None:
        raise ValueError(
            "sys.v1.agent.cancel requires an authenticated tenant context"
        )

    try:
        run_uuid = _uuid.UUID(run_id)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"sys.v1.agent.cancel: invalid run_id {run_id!r}")

    reason = str(payload.get("reason") or "").strip()

    db, owns_session = _acquire_handler_db(context)
    try:
        run = (
            db.query(AgentRun)
            .filter(AgentRun.id == run_uuid, AgentRun.user_id == normalized_user_id)
            .first()
        )
        if run is None:
            raise ValueError(f"sys.v1.agent.cancel: no agent run {run_id!r}")

        previous_status = run.status
        if previous_status not in _CANCELLABLE_AGENT_RUN_STATUSES:
            # Already terminal — never overwrite a completed/failed/cancelled run.
            return {"cancelled": False, "status": previous_status, "run_id": run_id}

        # Atomic CAS: exactly one caller wins even across a concurrent terminal
        # transition by the execution thread. synchronize_session=False mirrors the
        # resume-claim pattern in nodus_execution_service.
        claimed = (
            db.query(AgentRun)
            .filter(
                AgentRun.id == run_uuid,
                AgentRun.status.in_(_CANCELLABLE_AGENT_RUN_STATUSES),
            )
            .update(
                {
                    "status": "cancelled",
                    "wait_state": None,
                    "completed_at": datetime.now(timezone.utc),
                    "error_message": f"cancelled: {reason}" if reason else "cancelled",
                },
                synchronize_session=False,
            )
        )
        # Commit is load-bearing: the running execution thread reads this row in a
        # separate session and must observe the cancel at its next boundary check.
        db.commit()

        if not claimed:
            # Lost the race to a concurrent terminal transition; report the winner.
            fresh = db.query(AgentRun.status).filter(AgentRun.id == run_uuid).first()
            return {
                "cancelled": False,
                "status": fresh[0] if fresh else None,
                "run_id": run_id,
            }

        # Terminal lifecycle event — best-effort (required=False): a cancel must
        # never be blocked by an event-store hiccup.
        try:
            from AINDY.core.execution_signal_helper import record_agent_event

            record_agent_event(
                run_id=run_id,
                user_id=str(normalized_user_id),
                event_type="CANCELLED",
                db=db,
                correlation_id=None,
                payload={
                    "previous_status": previous_status,
                    "reason": reason or None,
                },
                required=False,
            )
        except Exception:
            logger.debug(
                "[syscall_registry] CANCELLED event emit skipped for %s",
                run_id,
                exc_info=True,
            )

        return {
            "cancelled": True,
            "previous_status": previous_status,
            "status": "cancelled",
            "run_id": run_id,
        }
    except Exception:
        try:
            db.rollback()
        except Exception:
            logger.debug(
                "[syscall_registry] agent.cancel rollback failed", exc_info=True
            )
        raise
    finally:
        if owns_session:
            try:
                db.close()
            except Exception:
                logger.debug(
                    "[syscall_registry] agent.cancel session close failed",
                    exc_info=True,
                )


def _handle_agent_undo(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.undo — reverse a completed AgentRun's reversible effects (AGENT-HARDEN-3).

    Walks the run's successful ``EffectRecord``s newest-first and invokes each
    owning syscall's registered ``compensate`` hook. Effects whose syscall declares
    no compensator are reported as ``irreversible`` (surfaced, never silently
    skipped); compensator failures are reported as ``failed``. Every attempt is
    written to the append-only ``effect_reversals`` audit log.

    Payload keys:
        run_id (str) — required; AgentRun id whose effects to reverse.

    Context metadata keys (internal use):
        _db — caller-provided SQLAlchemy Session.
    """
    import uuid as _uuid

    from AINDY.db.models import AgentRun

    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("sys.v1.agent.undo requires 'run_id'")

    normalized_user_id = _resolve_tenant_user_id(context, {})
    if normalized_user_id is None:
        raise ValueError("sys.v1.agent.undo requires an authenticated tenant context")

    try:
        run_uuid = _uuid.UUID(run_id)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"sys.v1.agent.undo: invalid run_id {run_id!r}")

    db, owns_session = _acquire_handler_db(context)
    try:
        # Tenant scope: the run must belong to the caller before we touch its effects.
        run = (
            db.query(AgentRun)
            .filter(AgentRun.id == run_uuid, AgentRun.user_id == normalized_user_id)
            .first()
        )
        if run is None:
            raise ValueError(f"sys.v1.agent.undo: no agent run {run_id!r}")

        from AINDY.core.effect_compensation import undo_run_effects

        return undo_run_effects(run_id, db=db, context=context, source_type="agent_run")
    finally:
        if owns_session:
            try:
                db.close()
            except Exception:
                logger.debug(
                    "[syscall_registry] agent.undo session close failed", exc_info=True
                )


def _handle_agent_simulate(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.agent.simulate — predicted-effect dry-run of an AgentRun (AGENT-HARDEN-4).

    Runs the run's plan in simulate mode: every tool call is shadowed (no real
    execution), producing a predicted result + a ``would_write`` intent. The report
    is persisted under ``run.result["simulation"]`` for the approval inbox WITHOUT
    changing the run's status — this is a preview, not an execution.

    A capability token is used so the preview reflects real grants: the run's own
    ``capability_token`` if present, otherwise a freshly minted preview token for
    the plan (so a pending-approval run can still be simulated).

    Payload keys:
        run_id (str) — required; AgentRun id to simulate.

    Context metadata keys (internal use):
        _db — caller-provided SQLAlchemy Session.
    """
    import uuid as _uuid

    from AINDY.db.models import AgentRun

    run_id = str(payload.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("sys.v1.agent.simulate requires 'run_id'")

    normalized_user_id = _resolve_tenant_user_id(context, {})
    if normalized_user_id is None:
        raise ValueError("sys.v1.agent.simulate requires an authenticated tenant context")

    try:
        run_uuid = _uuid.UUID(run_id)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"sys.v1.agent.simulate: invalid run_id {run_id!r}")

    db, owns_session = _acquire_handler_db(context)
    try:
        run = (
            db.query(AgentRun)
            .filter(AgentRun.id == run_uuid, AgentRun.user_id == normalized_user_id)
            .first()
        )
        if run is None:
            raise ValueError(f"sys.v1.agent.simulate: no agent run {run_id!r}")

        plan = run.plan or {}
        token = run.capability_token if isinstance(run.capability_token, dict) else None
        if token is None:
            # Mint a preview token so the simulation reflects the grants the run
            # would receive on approval (best-effort — None → tools show as gated).
            try:
                from AINDY.agents.capability_service import mint_token

                token = mint_token(
                    run_id=run_id,
                    user_id=str(normalized_user_id),
                    plan=plan,
                    db=db,
                    approval_mode="manual",
                )
            except Exception as exc:
                logger.debug(
                    "[syscall_registry] agent.simulate preview-token mint skipped: %s", exc
                )
                token = None

        # AGENT-HARDEN-4b — optional fake tool implementations (the simulated world).
        virtual_tools = payload.get("virtual_tools")
        if not isinstance(virtual_tools, dict):
            virtual_tools = None

        from AINDY.runtime.nodus_execution_service import simulate_agent_run

        return simulate_agent_run(
            run_id=run_id,
            plan=plan,
            user_id=str(normalized_user_id),
            db=db,
            execution_token=token,
            correlation_id=run.correlation_id,
            virtual_tools=virtual_tools,
        )
    finally:
        if owns_session:
            try:
                db.close()
            except Exception:
                logger.debug(
                    "[syscall_registry] agent.simulate session close failed", exc_info=True
                )


def _handle_execution_get(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.execution.get — status and resource metrics for an execution unit.

    Read-only introspection of a single ExecutionUnit owned by the calling
    tenant. Resolves the caller-supplied ``execution_id`` against the EU primary
    key, its soft ``source_id`` link (agent_run / flow_run id), or ``flow_run_id``
    — whichever a prior flow.run / nodus.execute / agent call returned.

    Payload keys:
        execution_id (str) — required; ExecutionUnit id, source run id, or flow_run_id.
    """
    import uuid as _uuid

    from sqlalchemy import or_

    from AINDY.db.models import ExecutionUnit

    execution_id = str(payload.get("execution_id", "")).strip()
    if not execution_id:
        raise ValueError("sys.v1.execution.get requires 'execution_id'")

    normalized_user_id = _resolve_tenant_user_id(context, {})
    if normalized_user_id is None:
        raise ValueError(
            "sys.v1.execution.get requires an authenticated tenant context"
        )

    db, owns_session = _acquire_handler_db(context)
    try:
        clauses = [
            ExecutionUnit.source_id == execution_id,
            ExecutionUnit.flow_run_id == execution_id,
        ]
        # ``id`` is a UUID column — only compare when the value is a valid UUID,
        # otherwise Postgres raises on the cast.
        try:
            clauses.insert(0, ExecutionUnit.id == _uuid.UUID(execution_id))
        except (ValueError, AttributeError, TypeError):
            pass

        row = (
            db.query(ExecutionUnit)
            .filter(ExecutionUnit.user_id == normalized_user_id)
            .filter(or_(*clauses))
            .order_by(ExecutionUnit.created_at.desc())
            .first()
        )
        if row is None:
            raise ValueError(
                f"sys.v1.execution.get: no execution unit found for "
                f"execution_id {execution_id!r}"
            )
        return {
            "execution_id": str(row.id),
            "type": row.type,
            "status": row.status,
            "syscall_count": int(row.syscall_count or 0),
            "wall_time_ms": int(row.wall_time_ms or 0),
            "memory_bytes": int(row.memory_bytes or 0),
            "priority": row.priority,
            "quota_group": row.quota_group,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    finally:
        if owns_session:
            db.close()


def _handle_observability_support_metrics(payload: dict, context: SyscallContext) -> dict:
    """sys.v1.observability.support_metrics — tenant-scoped observability + execution rollup.

    INFINITY-RUNTIME-1 item 3. Returns an aggregate the app-side Infinity support
    layer consumes: per-tenant request metrics + a platform-health signal (Step 3)
    and agent-run / async-job / Infinity-loop-event distributions (Step 4), over an
    optional ``window_hours`` window (default 24, max 168). Read-only.

    Payload keys:
        window_hours (int, optional) — lookback window; clamped to [1, 168].
    """
    from AINDY.platform_layer.support_metrics_service import build_support_metrics

    normalized_user_id = _resolve_tenant_user_id(context, {})
    if normalized_user_id is None:
        raise ValueError(
            "sys.v1.observability.support_metrics requires an authenticated tenant context"
        )

    db, owns_session = _acquire_handler_db(context)
    try:
        return build_support_metrics(
            db,
            user_id=normalized_user_id,
            window_hours=payload.get("window_hours"),
        )
    finally:
        if owns_session:
            db.close()


# ── Registry ──────────────────────────────────────────────────────────────────

SYSCALL_REGISTRY: VersionedSyscallRegistry = VersionedSyscallRegistry()

# ── v1 built-in syscalls ──────────────────────────────────────────────────────

SYSCALL_REGISTRY["sys.v1.memory.read"] = SyscallEntry(
    handler=_handle_memory_read,
    capability="memory.read",
    description="Recall memory nodes for the calling user.",
    input_schema={
        "properties": {
            "query": {"type": "string"},
            "tags": {"type": "list"},
            "limit": {"type": "int"},
            "node_type": {"type": "string"},
            "path": {"type": "string"},
        }
    },
    output_schema={
        "required": ["nodes", "count"],
        "properties": {"nodes": {"type": "list"}, "count": {"type": "int"}},
    },
)
SYSCALL_REGISTRY["sys.v1.memory.write"] = SyscallEntry(
    handler=_handle_memory_write,
    capability="memory.write",
    description="Persist a new memory node.",
    input_schema={
        "required": ["content"],
        "properties": {
            "content": {"type": "string"},
            "tags": {"type": "list"},
            "node_type": {"type": "string"},
            "path": {"type": "string"},
        },
    },
    output_schema={
        "required": ["node"],
        "properties": {"node": {"type": "dict"}, "path": {"type": "string"}},
    },
)
SYSCALL_REGISTRY["sys.v1.memory.delete"] = SyscallEntry(
    handler=_handle_memory_delete,
    capability="memory.delete",
    description="Hard-delete a memory node owned by the caller (tenant-scoped, idempotent).",
    input_schema={
        "required": ["node_id"],
        "properties": {"node_id": {"type": "string"}},
    },
    output_schema={
        "required": ["deleted"],
        "properties": {"deleted": {"type": "bool"}, "node_id": {"type": "string"}},
    },
)
SYSCALL_REGISTRY["sys.v1.memory.search"] = SyscallEntry(
    handler=_handle_memory_search,
    capability="memory.read",
    description="Semantic search over user memory nodes.",
    input_schema={
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "int"},
            "path": {"type": "string"},
        },
    },
    output_schema={
        "required": ["nodes", "count"],
        "properties": {"nodes": {"type": "list"}, "count": {"type": "int"}},
    },
)
SYSCALL_REGISTRY["sys.v1.memory.list"] = SyscallEntry(
    handler=_handle_memory_list,
    capability="memory.read",
    description="List nodes at a MAS path prefix.",
    input_schema={
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "limit": {"type": "int"}},
    },
    output_schema={
        "required": ["nodes", "count"],
        "properties": {"nodes": {"type": "list"}, "count": {"type": "int"}},
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.memory.tree"] = SyscallEntry(
    handler=_handle_memory_tree,
    capability="memory.read",
    description="Return a hierarchical tree of nodes under a path.",
    input_schema={
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "limit": {"type": "int"}},
    },
    output_schema={
        "required": ["tree", "node_count"],
        "properties": {"tree": {"type": "dict"}, "node_count": {"type": "int"}},
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.memory.trace"] = SyscallEntry(
    handler=_handle_memory_trace,
    capability="memory.read",
    description="Follow the causal chain from a node at a path.",
    input_schema={
        "required": ["path"],
        "properties": {"path": {"type": "string"}, "depth": {"type": "int"}},
    },
    output_schema={
        "required": ["chain", "depth"],
        "properties": {"chain": {"type": "list"}, "depth": {"type": "int"}},
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.flow.run"] = SyscallEntry(
    handler=_handle_flow_run,
    capability="flow.run",
    description="Execute a registered flow by name.",
    input_schema={
        "required": ["flow_name"],
        "properties": {
            "flow_name": {"type": "string"},
            "initial_state": {"type": "dict"},
        },
    },
)
SYSCALL_REGISTRY["sys.v1.event.emit"] = SyscallEntry(
    handler=_handle_event_emit,
    capability="event.emit",
    description="Emit a SystemEvent on the A.I.N.D.Y. event bus.",
    input_schema={
        "required": ["event_type"],
        "properties": {
            "event_type": {"type": "string"},
            "payload": {"type": "dict"},
        },
    },
)

# ── v2 syscalls ───────────────────────────────────────────────────────────────
# v2 extends v1 capabilities without removing or changing existing fields.

SYSCALL_REGISTRY["sys.v2.memory.read"] = SyscallEntry(
    handler=_handle_memory_read_v2,
    capability="memory.read",
    description="Enhanced memory recall with structured field filters (v2).",
    input_schema={
        "properties": {
            "query": {"type": "string"},
            "tags": {"type": "list"},
            "limit": {"type": "int"},
            "node_type": {"type": "string"},
            "path": {"type": "string"},
            "filters": {"type": "dict"},   # v2 extension — optional
        }
    },
    output_schema={
        "required": ["nodes", "count"],
        "properties": {
            "nodes": {"type": "list"},
            "count": {"type": "int"},
            "version": {"type": "string"},
        },
    },
    stable=False,
)

# ── v1 execution entry-point syscalls ─────────────────────────────────────────
# These mirror the top-level execution entry points so ALL code paths — both
# internal and external — route through the syscall layer.

SYSCALL_REGISTRY["sys.v1.flow.execute_intent"] = SyscallEntry(
    handler=_handle_flow_execute_intent,
    capability="flow.execute",
    description="Top-level intent execution with learned strategy selection.",
    input_schema={
        "required": ["intent_data"],
        "properties": {
            "intent_data": {"type": "dict"},
        },
    },
    output_schema={
        "required": ["intent_result"],
        "properties": {"intent_result": {"type": "dict"}},
    },
)
SYSCALL_REGISTRY["sys.v1.nodus.execute"] = SyscallEntry(
    handler=_handle_nodus_execute,
    capability="nodus.execute",
    description="Execute a Nodus script via flow-backed orchestration.",
    input_schema={
        "required": ["script"],
        "properties": {
            "script": {"type": "string"},
            "input_payload": {"type": "dict"},
            "error_policy": {"type": "string"},
            "workflow_type": {"type": "string"},
            "trace_id": {"type": "string"},
            "node_max_retries": {"type": "int"},
        },
    },
    output_schema={
        "required": ["nodus_result"],
        "properties": {"nodus_result": {"type": "dict"}},
    },
)
SYSCALL_REGISTRY["sys.v1.job.submit"] = SyscallEntry(
    handler=_handle_job_submit,
    capability="job.submit",
    description="Submit a named async job to the automation pipeline.",
    input_schema={
        "required": ["task_name"],
        "properties": {
            "task_name": {"type": "string"},
            "payload": {"type": "dict"},
            "source": {"type": "string"},
            "max_attempts": {"type": "int"},
        },
    },
    output_schema={
        "required": ["log_id"],
        "properties": {
            "log_id": {"type": "string"},
            "task_name": {"type": "string"},
            "source": {"type": "string"},
        },
    },
)
SYSCALL_REGISTRY["sys.v1.agent.execute"] = SyscallEntry(
    handler=_handle_agent_execute,
    capability="agent.execute",
    description="Execute an approved AgentRun via the deterministic runtime.",
    input_schema={
        "required": ["run_id"],
        "properties": {"run_id": {"type": "string"}},
    },
    output_schema={
        "required": ["run_result"],
        "properties": {"run_result": {"type": "dict"}},
    },
)
SYSCALL_REGISTRY["sys.v1.agent.count_runs"] = SyscallEntry(
    handler=_handle_agent_count_runs,
    capability="agent.read",
    description="Count AgentRun rows for a user, optionally filtered by status.",
    input_schema={
        "properties": {
            "user_id": {"type": "string"},
            "status": {"type": "list"},
        },
    },
    output_schema={
        "required": ["count"],
        "properties": {"count": {"type": "int"}},
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.agent.list_recent_durations"] = SyscallEntry(
    handler=_handle_agent_list_recent_durations,
    capability="agent.read",
    description="List recent AgentRun timing fields for duration calculations.",
    input_schema={
        "properties": {
            "user_id": {"type": "string"},
            "window_hours": {"type": "int"},
        },
    },
    output_schema={
        "required": ["durations", "count"],
        "properties": {
            "durations": {"type": "list"},
            "count": {"type": "int"},
        },
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.agent.list_recent_runs"] = SyscallEntry(
    handler=_handle_agent_list_recent_runs,
    capability="agent.read",
    description="List recent AgentRun rows for a user as plain dicts.",
    input_schema={
        "properties": {
            "user_id": {"type": "string"},
            "limit": {"type": "int"},
        },
    },
    output_schema={
        "required": ["runs"],
        "properties": {"runs": {"type": "list"}},
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.agent.ensure_initial_run"] = SyscallEntry(
    handler=_handle_agent_ensure_initial_run,
    capability="agent.write",
    description="Find or create the initial signup AgentRun sentinel for a user.",
    input_schema={
        "properties": {
            "user_id": {"type": "string"},
        },
    },
    output_schema={
        "required": ["run_id", "created"],
        "properties": {
            "run_id": {"type": "string"},
            "created": {"type": "bool"},
        },
    },
    stable=False,
)
SYSCALL_REGISTRY["sys.v1.agent.cancel"] = SyscallEntry(
    handler=_handle_agent_cancel,
    capability="agent.cancel",
    description="Cooperatively cancel a non-terminal AgentRun to a terminal 'cancelled' state.",
    input_schema={
        "required": ["run_id"],
        "properties": {
            "run_id": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
    output_schema={
        "required": ["cancelled", "status"],
        "properties": {
            "cancelled": {"type": "bool"},
            "status": {"type": "string"},
            "previous_status": {"type": "string"},
            "run_id": {"type": "string"},
        },
    },
)
SYSCALL_REGISTRY["sys.v1.agent.undo"] = SyscallEntry(
    handler=_handle_agent_undo,
    capability="agent.undo",
    description="Reverse a completed AgentRun's reversible effects via registered compensators; surface irreversible ones.",
    input_schema={
        "required": ["run_id"],
        "properties": {"run_id": {"type": "string"}},
    },
    output_schema={
        "required": ["reversed", "irreversible", "failed"],
        "properties": {
            "reversed": {"type": "list"},
            "irreversible": {"type": "list"},
            "failed": {"type": "list"},
            "run_id": {"type": "string"},
        },
    },
)
SYSCALL_REGISTRY["sys.v1.agent.simulate"] = SyscallEntry(
    handler=_handle_agent_simulate,
    capability="agent.simulate",
    description="Predicted-effect dry-run of an AgentRun (tools shadowed, zero side effects); persists the report for the approval inbox.",
    input_schema={
        "required": ["run_id"],
        "properties": {
            "run_id": {"type": "string"},
            "virtual_tools": {"type": "dict"},
        },
    },
    output_schema={
        "required": ["simulated"],
        "properties": {
            "simulated": {"type": "bool"},
            "steps": {"type": "list"},
            "simulated_effects": {"type": "list"},
        },
    },
)
SYSCALL_REGISTRY["sys.v1.execution.get"] = SyscallEntry(
    handler=_handle_execution_get,
    capability="execution.read",
    description="Return status and resource metrics for an execution unit.",
    input_schema={
        "required": ["execution_id"],
        "properties": {"execution_id": {"type": "string"}},
    },
    output_schema={
        "required": ["status"],
        "properties": {
            "status": {"type": "string"},
            "syscall_count": {"type": "int"},
            "wall_time_ms": {"type": "int"},
        },
    },
)


SYSCALL_REGISTRY["sys.v1.observability.support_metrics"] = SyscallEntry(
    handler=_handle_observability_support_metrics,
    capability="execution.read",
    description=(
        "Tenant-scoped observability + execution support-metrics rollup "
        "(request metrics, platform health, agent/async execution behavior, "
        "Infinity loop-event counts) for the app-side Infinity support layer."
    ),
    input_schema={
        "properties": {"window_hours": {"type": "int"}},
    },
    output_schema={
        "required": ["observability", "execution", "infinity_events"],
        "properties": {
            "generated_at": {"type": "string"},
            "window_hours": {"type": "int"},
            "observability": {"type": "object"},
            "execution": {"type": "object"},
            "infinity_events": {"type": "object"},
        },
    },
)


def register_syscall(
    name: str,
    handler: Callable[[dict, SyscallContext], dict],
    capability: str,
    description: str = "",
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    stable: bool = True,
    deprecated: bool = False,
    deprecated_since: str | None = None,
    replacement: str | None = None,
    compensate: Callable[[dict, SyscallContext], dict | None] | None = None,
) -> None:
    """Register a syscall at runtime.

    Safe to call multiple times with the same handler (no-op re-registration).
    Raises ValueError if called with a different handler for an already-registered
    name — each syscall has exactly one registration point by design.
    Not thread-safe for concurrent writes (startup-only use case).

    Args:
        name:             Fully-qualified name (must start with ``"sys."``).
        handler:          Callable(payload, context) → dict.
        capability:       Required capability string.
        description:      Human-readable description.
        input_schema:     Optional input validation schema.
        output_schema:    Optional output validation schema.
        stable:           False marks the syscall as experimental.
        deprecated:       True causes the dispatcher to emit a warning.
        deprecated_since: Version string when deprecation was introduced.
        replacement:      Full name of the replacement syscall.

    Raises:
        ValueError: If name does not start with ``"sys."`` or is already registered
            with a different handler.
    """
    if not name.startswith("sys."):
        raise ValueError(
            f"Syscall name must start with 'sys.', got: {name!r}"
        )
    SYSCALL_REGISTRY[name] = SyscallEntry(
        handler=handler,
        capability=capability,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        stable=stable,
        deprecated=deprecated,
        deprecated_since=deprecated_since,
        replacement=replacement,
        compensate=compensate,
    )
    logger.debug(
        "[syscall_registry] registered '%s' (capability=%s, deprecated=%s)",
        name, capability, deprecated,
    )


def get_registered_syscalls() -> list[str]:
    """Return the names of all currently registered syscalls."""
    try:
        return sorted(SYSCALL_REGISTRY.keys())
    except Exception:
        return []


# Minimum number of syscalls expected after a complete boot (all static built-ins).
# Any count below this floor means Phase 8 did not finish, or a registration was lost.
# Add 1 per new static entry added to this file.  Do not lower this value.
SYSCALL_REGISTRY_MIN_COUNT: int = 23


