"""
AINDY.runtime package — flow/Nodus engine boundary helpers.

Exposes engine-registration introspection (``get_engine_status``,
``verify_engine_registration``) and the public-entrypoint guard
(``enforce_engine_boundary``) that keeps Nodus-backed workflows and
Python-DAG flows on their respective run paths.  ``flow_engine`` is lazily
imported via ``__getattr__`` to avoid a heavy import at package load.
"""
from __future__ import annotations

import logging
import sys
from importlib import import_module

logger = logging.getLogger(__name__)


def __getattr__(name: str):
    if name == "flow_engine":
        module = import_module("AINDY.runtime.flow_engine")
        sys.modules.setdefault("runtime.flow_engine", module)
        globals()[name] = module
        return module
    raise AttributeError(name)


def _is_nodus_node(name: str) -> bool:
    return name == "nodus.execute" or name.startswith("nodus.")


def get_engine_status() -> dict:
    """Return registration status of the DAG and Nodus flow engines."""
    from AINDY.runtime.flow_engine import FLOW_REGISTRY, NODE_REGISTRY

    dag_nodes = sorted(name for name in NODE_REGISTRY if not _is_nodus_node(name))
    nodus_nodes = sorted(name for name in NODE_REGISTRY if _is_nodus_node(name))
    try:
        import nodus.runtime.embedding as _nodus_emb
        nodus_configured = hasattr(_nodus_emb, "NodusRuntime")
    except ImportError:
        nodus_configured = False
    return {
        "dag_engine": {
            "registered_nodes": len(dag_nodes),
            "node_names": dag_nodes,
            "available": len(dag_nodes) > 0,
            "registered_flows": len(FLOW_REGISTRY),
        },
        "nodus_engine": {
            "registered_nodes": len(nodus_nodes),
            "node_names": nodus_nodes,
            "available": len(nodus_nodes) > 0,
            "configured": nodus_configured,
        },
    }


def verify_engine_registration() -> dict:
    """Validate that the always-on DAG engine is initialized."""
    status = get_engine_status()
    if status["dag_engine"]["registered_nodes"] <= 0:
        raise RuntimeError("Custom DAG engine registered no nodes at startup")
    return status


def enforce_engine_boundary(
    *,
    entrypoint: str,
    flow_name: str | None = None,
    workflow_type: str | None = None,
) -> None:
    """Reject the wrong public entrypoint for a flow engine boundary."""
    label = str(flow_name or workflow_type or "")
    is_nodus_workflow = (
        label == "nodus_execute"
        or label.startswith("nodus")
        or label.startswith("memory_nodus")
    )
    if entrypoint in {"flow.run", "flow.execute_intent"} and is_nodus_workflow:
        raise ValueError(
            "Nodus workflows must use AINDY.runtime.nodus_execution_service."
            "run_nodus_script_via_flow(), not the generic DAG flow entrypoints."
        )
    if entrypoint == "nodus.run" and label and "nodus" not in label:
        raise ValueError(
            "run_nodus_script_via_flow() is reserved for Nodus-backed workflows; "
            "use AINDY.runtime.flow_engine.run_flow()/execute_intent() for "
            "Python-defined DAG flows."
        )
