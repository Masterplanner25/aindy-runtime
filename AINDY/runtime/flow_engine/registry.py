from AINDY.runtime.flow_engine.shared import Callable, Optional, Session, logger

NODE_REGISTRY: dict[str, Callable] = {}
FLOW_REGISTRY: dict[str, dict] = {}

# ECOGAP-1: flows whose authors declare their nodes idempotent (or
# EffectRecord-gated), so the single node that re-runs on crash continuation
# cannot double-fire a side effect. Empty by default → nothing is continued
# unless explicitly opted in. Used by `core.flow_continuation`.
CONTINUATION_SAFE_FLOWS: set[str] = set()


def _registry_flow_plan(
    intent_type: str,
    db: Session,
    user_id: str = None,
) -> Optional[dict]:
    from AINDY.platform_layer import registry

    context = {
        "flow_type": intent_type,
        "intent_type": intent_type,
        "db": db,
        "user_id": user_id,
    }
    handler = registry.get_flow_strategy(intent_type)
    value = handler(context) if handler else None
    return value if isinstance(value, dict) else None


select_strategy = _registry_flow_plan


def register_node(name: str):
    def wrapper(fn: Callable):
        NODE_REGISTRY[name] = fn
        return fn

    return wrapper


def register_flow(name: str, flow: dict) -> None:
    FLOW_REGISTRY[name] = flow
    logger.debug("Flow registered: %s", name)


def mark_flow_continuation_safe(name: str) -> None:
    """ECOGAP-1: declare a flow safe to re-drive from its last node after a crash.

    Only declare a flow continuation-safe when the node that could re-run on
    resume is idempotent — its side effects are naturally repeatable or gated
    through the EffectRecord (EXACTLY_ONCE) idempotency layer.
    """
    CONTINUATION_SAFE_FLOWS.add(name)


def is_flow_continuation_safe(name: str) -> bool:
    return name in CONTINUATION_SAFE_FLOWS
