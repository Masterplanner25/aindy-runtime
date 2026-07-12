from AINDY.runtime.flow_engine.shared import Callable, Optional, Session, logger

NODE_REGISTRY: dict[str, Callable] = {}
FLOW_REGISTRY: dict[str, dict] = {}

# ECOGAP-1: flows whose authors declare their nodes idempotent (or
# EffectRecord-gated), so the single node that re-runs on crash continuation
# cannot double-fire a side effect. Empty by default → nothing is continued
# unless explicitly opted in. Used by `core.flow_continuation`.
CONTINUATION_SAFE_FLOWS: set[str] = set()

# DUR-3: flows explicitly declared UNSAFE to continue — used only when default-safe
# continuation is enabled (AINDY_DURABLE_CONTINUATION_ALL). A flow whose nodes have raw,
# un-mediated side effects (a direct external call / a write outside the runtime's effect
# boundary, which the at-most-once layer cannot dedup) belongs here so it is NEVER continued
# even under default-safe. Empty by default.
CONTINUATION_UNSAFE_FLOWS: set[str] = set()


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


def mark_flow_continuation_unsafe(name: str) -> None:
    """DUR-3: declare a flow that must NEVER be continued, even under default-safe mode.

    Use for a flow whose nodes perform raw side effects the runtime cannot mediate/dedup
    (a direct external call, a write outside the EffectRecord boundary).
    """
    CONTINUATION_UNSAFE_FLOWS.add(name)


def is_flow_continuation_unsafe(name: str) -> bool:
    return name in CONTINUATION_UNSAFE_FLOWS
