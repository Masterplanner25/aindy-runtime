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


# ── Named predicates (FLOW-PARALLEL-1 phase 3a) ──────────────────────────────
#
# A conditional edge used to be `{"target": …, "condition": <callable>}` — a Python closure over
# in-process state, which the graph signature could only record as "this edge is gated". It could
# not say WHICH decision gated it, so a predicate rerouted between suspend and resume was not
# caught (`FLOW-GRAPH-SIGNATURE-1`'s deliberate blind spot; MAF hit the same wall and answered:
# serialize the shape, NAME the predicate, fail loudly if it is missing on restore).
#
# A predicate is now data: `{"target": …, "when": "<name>"}` names a pure function of `state`
# registered here. The name is the identity — it goes into the signature — so registering a
# name twice with a DIFFERENT callable is refused: a silent overwrite would make the signature
# say one thing and the decision do another. Re-registering the same function (a module
# re-imported) is a no-op.

PREDICATE_REGISTRY: dict[str, Callable[[dict], bool]] = {}

#: The named form of `lambda s: True` — the fall-through edge an ordered list of `when` edges
#: ends with. With it, an ordered `when` list IS a switch-case: first match wins, `default`
#: last, and a non-terminal node with no match already fails the run loudly.
DEFAULT_PREDICATE = "default"


class PredicateRegistrationError(ValueError):
    """A predicate name is already bound to a different callable."""


class UnknownPredicate(KeyError):
    """A `when` edge names a predicate that is not registered — raised at RESOLUTION, loudly.

    Never treated as "does not match": a missing decision that silently falls through would
    reroute the flow, which is the exact thing naming predicates exists to make detectable.
    """

    def __init__(self, name: str, node: str) -> None:
        self.name = name
        self.node = node
        super().__init__(
            f"edge from node {node!r} names predicate {name!r}, which is not registered "
            f"(registered: {sorted(PREDICATE_REGISTRY)}). Register it with "
            f"@register_predicate({name!r}) before the flow runs."
        )

    def __str__(self) -> str:  # KeyError would repr() the message
        return self.args[0]


def _same_predicate(a: Callable, b: Callable) -> bool:
    """The same function, or the same qualified name — a module re-imported (reloaded, or
    imported twice under two names) produces a new function object for the same predicate."""
    if a is b:
        return True
    return (getattr(a, "__module__", None), getattr(a, "__qualname__", None)) == (
        getattr(b, "__module__", None), getattr(b, "__qualname__", None)
    ) and getattr(a, "__qualname__", "<lambda>") != "<lambda>"


def register_predicate(name: str):
    """Decorator: bind ``name`` to a pure ``fn(state) -> bool``."""
    key = str(name)

    def wrapper(fn: Callable[[dict], bool]):
        existing = PREDICATE_REGISTRY.get(key)
        if existing is not None and not _same_predicate(existing, fn):
            raise PredicateRegistrationError(
                f"predicate {key!r} is already registered to "
                f"{getattr(existing, '__qualname__', existing)!r}; a name is the predicate's "
                "identity in the graph signature and cannot be silently rebound."
            )
        PREDICATE_REGISTRY[key] = fn
        return fn

    return wrapper


@register_predicate(DEFAULT_PREDICATE)
def _default_predicate(state: dict) -> bool:  # noqa: ARG001 — the fall-through matches anything
    return True


def resolve_predicate(name: str, *, node: str) -> Callable[[dict], bool]:
    try:
        return PREDICATE_REGISTRY[str(name)]
    except KeyError:
        raise UnknownPredicate(str(name), node) from None


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
