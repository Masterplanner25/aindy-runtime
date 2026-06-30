"""
nodus_workflow_registry.py — Runtime-safe Nodus workflow registration (RTR-1).

The registration surface that lets apps register/select Nodus workflows by name,
at boot, without editing runtime — mirroring ``register_dynamic_flow`` but storing
the ``.nd`` SOURCE (the durable, versioned artifact) rather than a closure-bearing
flow dict. See ``docs/runtime/NODUS_WORKFLOW_CONTRACT.md``.

Two kinds (both Phase 1):

* ``flow-graph`` — a ``flow.step()`` routing script. Compiled via
  ``compile_nodus_flow`` into a multi-node flow over registered nodes and
  registered into ``FLOW_REGISTRY`` under the workflow name.
* ``script`` — one arbitrary ``.nodus`` program. Executed as a single
  ``nodus.execute`` node by reusing the shared ``nodus_execute`` flow with the
  workflow's source injected at run time.

Both kinds are tracked in the authoritative in-memory ``_NODUS_WORKFLOWS`` map and
run uniformly through ``run_nodus_workflow``. Source rows persist to the
``nodus_workflows`` table so registrations survive a restart; on boot,
``rehydrate_nodus_workflows`` recompiles each active row.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from AINDY.platform_layer.extension_abi import (
    SURFACE_FLOW,
    extension_surface_default_version,
    extension_surface_stability,
)
from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    validate_extension_owner_class,
)
from AINDY.platform_layer.extension_provenance import (
    SOURCE_DATA_REGISTRATION,
    derive_structured_extension_provenance,
)
from AINDY.platform_layer.registry_contracts import validate_nodus_workflow

logger = logging.getLogger(__name__)

# Supported workflow kinds.
KIND_FLOW_GRAPH = "flow-graph"
KIND_SCRIPT = "script"
VALID_WORKFLOW_KINDS = (KIND_FLOW_GRAPH, KIND_SCRIPT)

# Protects all writes to _NODUS_WORKFLOWS.
_registry_lock = threading.Lock()

# Serialisable metadata for every registered Nodus workflow. Keyed by name;
# never contains Python callables (the compiled flow lives in FLOW_REGISTRY).
_NODUS_WORKFLOWS: dict[str, dict[str, Any]] = {}


def _content_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _compile_and_register_flow(name: str, source: str, kind: str) -> None:
    """Compile the source and, for flow-graph workflows, register it in FLOW_REGISTRY.

    Script workflows do not get a distinct FLOW_REGISTRY entry — they reuse the
    shared ``nodus_execute`` flow with their source injected at run time.
    """
    if kind == KIND_FLOW_GRAPH:
        from AINDY.runtime.flow_engine import register_flow
        from AINDY.runtime.nodus_flow_compiler import compile_nodus_flow

        flow_dict = compile_nodus_flow(source, name)
        register_flow(name, flow_dict)
    elif kind == KIND_SCRIPT:
        # Ensure the canonical nodus_execute flow is available for run-by-name.
        from AINDY.runtime.nodus_execution_service import (
            ensure_nodus_script_flow_registered,
        )

        ensure_nodus_script_flow_registered()
    else:  # defensive — validate_nodus_workflow already rejects this
        raise ValueError(f"unsupported nodus workflow kind {kind!r}")


def register_nodus_workflow(
    name: str,
    source: str,
    *,
    kind: str = KIND_FLOW_GRAPH,
    version: str | None = None,
    capabilities: list[str] | None = None,
    owner_class: str = OWNER_EXTERNAL_THIRD_PARTY,
    provenance: dict[str, Any] | None = None,
    allow_legacy_missing_provenance: bool = False,
    overwrite: bool = False,
    db: Session | None = None,
) -> dict[str, Any]:
    """Validate, compile, and register a Nodus workflow at runtime.

    Thread-safe. If *db* is provided the ``.nd`` source is also persisted to the
    ``nodus_workflows`` table so the registration survives a restart. Pass
    ``db=None`` from the boot loader (it re-registers from already-persisted rows).
    """
    validate_nodus_workflow(name, source, kind)
    owner_class = validate_extension_owner_class(owner_class)
    capabilities = list(capabilities or [])
    content_hash = _content_hash(source)
    resolved_version = version or content_hash[:12]

    resolved_provenance = derive_structured_extension_provenance(
        owner_class=owner_class,
        surface="nodus-workflow",
        extension_name=name,
        artifact_payload={
            "abi_version": extension_surface_default_version(SURFACE_FLOW),
            "name": name,
            "kind": kind,
            "content_hash": content_hash,
            "version": resolved_version,
            "capabilities": list(capabilities),
            "owner_class": owner_class,
        },
        source_type=SOURCE_DATA_REGISTRATION,
        source_ref=f"nodus-workflow:{name}",
        declared=provenance,
        allow_legacy_missing=allow_legacy_missing_provenance,
    )

    with _registry_lock:
        if name in _NODUS_WORKFLOWS and not overwrite:
            raise ValueError(
                f"nodus workflow {name!r} already exists; set overwrite=true to replace"
            )

        # Compile + register the executable form before recording metadata so a
        # compile failure leaves the registry unchanged.
        _compile_and_register_flow(name, source, kind)

        meta: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "version": resolved_version,
            "content_hash": content_hash,
            "source": source,
            "abi_surface": SURFACE_FLOW,
            "abi_version": extension_surface_default_version(SURFACE_FLOW),
            "abi_stability": extension_surface_stability(SURFACE_FLOW),
            "capabilities": list(capabilities),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": None,
            "dynamic": True,
            "owner_class": owner_class,
            "trust_class": "source-nodus-workflow",
            "provenance": resolved_provenance,
        }
        _NODUS_WORKFLOWS[name] = meta

    if db is not None:
        _persist_workflow(
            name,
            source,
            kind=kind,
            version=resolved_version,
            content_hash=content_hash,
            capabilities=capabilities,
            owner_class=owner_class,
            provenance=resolved_provenance,
            overwrite=overwrite,
            db=db,
        )

    logger.info(
        "platform: nodus workflow registered: %s (kind=%s version=%s)",
        name,
        kind,
        resolved_version,
    )
    return dict(meta)


def _persist_workflow(
    name: str,
    source: str,
    *,
    kind: str,
    version: str,
    content_hash: str,
    capabilities: list[str],
    owner_class: str,
    provenance: dict[str, Any],
    overwrite: bool,
    db: Session,
    created_by: str | None = None,
) -> None:
    """Upsert the workflow source into the nodus_workflows table."""
    from AINDY.db.models.nodus_workflow import NodusWorkflow

    now = datetime.now(timezone.utc)
    try:
        existing = db.query(NodusWorkflow).filter(NodusWorkflow.name == name).first()
        if existing:
            existing.source = source
            existing.kind = kind
            existing.version = version
            existing.content_hash = content_hash
            existing.capabilities = list(capabilities)
            existing.owner_class = owner_class
            existing.provenance = provenance
            existing.is_active = True
            existing.updated_at = now
        else:
            db.add(
                NodusWorkflow(
                    id=uuid.uuid4(),
                    name=name,
                    source=source,
                    kind=kind,
                    version=version,
                    content_hash=content_hash,
                    capabilities=list(capabilities),
                    owner_class=owner_class,
                    provenance=provenance,
                    created_by=str(created_by) if created_by else None,
                    created_at=now,
                    updated_at=now,
                    is_active=True,
                )
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("platform: failed to persist nodus workflow %r: %s", name, exc)


def list_nodus_workflows() -> list[dict[str, Any]]:
    """Return a snapshot of all registered Nodus workflow metadata (no source)."""
    with _registry_lock:
        return [
            {k: v for k, v in meta.items() if k != "source"}
            for meta in _NODUS_WORKFLOWS.values()
        ]


def get_nodus_workflow(name: str) -> dict[str, Any] | None:
    """Return metadata for one registered workflow (includes source), or None."""
    with _registry_lock:
        meta = _NODUS_WORKFLOWS.get(name)
        return dict(meta) if meta else None


def delete_nodus_workflow(name: str, *, db: Session | None = None) -> bool:
    """Remove a workflow from the registry (and FLOW_REGISTRY for flow-graph).

    If *db* is provided, soft-deletes the row (is_active=False). Returns True if
    removed, False if the name was not registered.
    """
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    with _registry_lock:
        meta = _NODUS_WORKFLOWS.pop(name, None)
        if meta is None:
            return False
        if meta.get("kind") == KIND_FLOW_GRAPH:
            FLOW_REGISTRY.pop(name, None)

    if db is not None:
        try:
            from AINDY.db.models.nodus_workflow import NodusWorkflow

            row = db.query(NodusWorkflow).filter(NodusWorkflow.name == name).first()
            if row:
                row.is_active = False
                row.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("platform: failed to soft-delete nodus workflow %r: %s", name, exc)

    logger.info("platform: nodus workflow deleted: %s", name)
    return True


def rehydrate_nodus_workflows(db: Session) -> int:
    """Recompile every active nodus_workflows row into the in-memory registry.

    Runtime-only boot finds zero rows and is a no-op. Per-workflow failures are
    logged and skipped; they never abort boot (mirrors the dynamic-flow loader).
    Returns the number of workflows re-registered.
    """
    try:
        from AINDY.db.models.nodus_workflow import NodusWorkflow

        rows = (
            db.query(NodusWorkflow)
            .filter(NodusWorkflow.is_active.is_(True))
            .all()
        )
    except Exception as exc:
        logger.error("platform: nodus workflow rehydration query failed: %s", exc)
        return 0

    count = 0
    for row in rows:
        try:
            register_nodus_workflow(
                row.name,
                row.source,
                kind=row.kind,
                version=row.version,
                capabilities=list(row.capabilities or []),
                owner_class=row.owner_class or OWNER_EXTERNAL_THIRD_PARTY,
                allow_legacy_missing_provenance=True,
                overwrite=True,
                db=None,  # already persisted; do not re-write
            )
            count += 1
        except Exception as exc:
            logger.error(
                "platform: failed to rehydrate nodus workflow %r: %s", row.name, exc
            )
    if count:
        logger.info("platform: rehydrated %d nodus workflow(s)", count)
    return count


def run_nodus_workflow(
    name: str,
    *,
    db: Session,
    user_id: str,
    input_payload: dict[str, Any] | None = None,
    error_policy: str = "halt",
    trace_id: str | None = None,
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a registered Nodus workflow by name.

    ``flow-graph`` workflows run their compiled FLOW_REGISTRY flow; ``script``
    workflows run their stored source through the shared ``nodus_execute`` flow.
    Both reuse the canonical PersistentFlowRunner → FlowRun + SystemEvent path.
    """
    meta = get_nodus_workflow(name)
    if meta is None:
        raise LookupError(f"nodus workflow {name!r} is not registered")

    kind = meta["kind"]
    if kind == KIND_SCRIPT:
        from AINDY.runtime.nodus_execution_service import run_nodus_script_via_flow

        return run_nodus_script_via_flow(
            script=meta["source"],
            input_payload=input_payload or {},
            error_policy=error_policy,
            db=db,
            user_id=user_id,
            workflow_type=f"nodus_workflow:{name}",
            trace_id=trace_id,
        )

    # flow-graph
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, PersistentFlowRunner
    from AINDY.utils.uuid_utils import normalize_uuid

    flow = FLOW_REGISTRY.get(name)
    if flow is None:
        # Lost from the in-memory registry (e.g. process recycled without
        # rehydration) — recompile from the retained source.
        _compile_and_register_flow(name, meta["source"], kind)
        flow = FLOW_REGISTRY[name]

    runner = PersistentFlowRunner(
        flow=flow,
        db=db,
        user_id=normalize_uuid(user_id) if user_id else None,
        workflow_type=f"nodus_workflow:{name}",
    )
    state = dict(initial_state or {})
    if input_payload is not None:
        state.setdefault("input_payload", input_payload)
    if trace_id is not None:
        state["trace_id"] = trace_id
    return runner.start(initial_state=state, flow_name=name)
