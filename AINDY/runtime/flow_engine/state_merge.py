"""`FLOW-PARALLEL-1` — a declared per-cell conflict policy, before there is a second writer.

The flow engine merges a node's output into run state with ``state.update(patch)``. That is
**last-write-wins**, and it is harmless today only because **there is never a second writer**:
plan steps are strictly sequential, so no two patches ever contend for a cell.

★★ WHY THIS LANDS BEFORE FAN-OUT EXISTS, AND NOT AFTER
--------------------------------------------------------
The moment fan-out is added, ``state.update`` silently becomes a **completion-order** race: two
branches writing one cell produce whichever value finished last, which varies run to run and
cannot be reproduced from the record. **Determinism is the load-bearing part of any fix here,
not speed** — a parallel flow that returns a different answer on replay is worse than a slow
sequential one, and the failure is invisible in exactly the cases people fan out for.

Adding the policy afterwards is far more expensive: by then flows exist that depend on the
accidental ordering, and every one of them has to be audited to find out which. Landing the
vocabulary first means fan-out arrives into a runtime that already refuses the ambiguous case.

★ IT DOES NOT PICK A DEFAULT, DELIBERATELY
--------------------------------------------
An undeclared double-write **raises**. Choosing a default here would be choosing one flow's
correct answer for every flow: last-write-wins is right for a "latest reading" cell and wrong
for a counter, and a silently-wrong merge produces a plausible number nobody checks. LangGraph
reached the same conclusion with `LastValue` / `BinaryOperatorAggregate` / `NamedBarrierValue`
— three declared channel types, no implicit one. The declaration is the point.

★ THE THREE POLICIES, AND WHY EACH IS DETERMINISTIC
-----------------------------------------------------
- ``last_write_wins`` — resolved in **declaration order**, never completion order. Explicit
  opt-in, because it is the one policy whose answer depends on ordering at all.
- ``reduce`` — restricted to **commutative and associative** operators, so the result does not
  depend on order in the first place. A non-commutative operator is refused rather than
  supported: it would reintroduce exactly the nondeterminism this module exists to remove.
- ``barrier`` — every declared writer must have written. Not a merge rule so much as a
  completeness assertion, and it fails loudly when a branch is missing rather than resolving a
  cell from partial input.

★ RELATION TO `EFFECT-PARTIAL-1`, WHICH IS THE OPEN DECISION THIS SETTLES
---------------------------------------------------------------------------
The open question was *barrier-as-commit-boundary vs independent branch commits*. Settled by the
same vocabulary: a superstep in which some branches succeed and others fail is a **partial**
outcome, and the runtime now has a word for that. The barrier is where the outcome is decided,
and it is decided with the per-unit detail `EFFECT-PARTIAL-1` requires — a branch is a unit.

★ NOT part of the graph signature. `FLOW-GRAPH-SIGNATURE-1` hashes topology, not semantics, and
excludes `node_configs` for the same reason: a policy edited between suspend and resume must not
quarantine every in-flight run.
"""
from __future__ import annotations

import functools
import operator
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

POLICY_LAST_WRITE_WINS = "last_write_wins"
POLICY_REDUCE = "reduce"
POLICY_BARRIER = "barrier"

POLICIES = frozenset({POLICY_LAST_WRITE_WINS, POLICY_REDUCE, POLICY_BARRIER})

#: The key a flow definition uses to declare its policies: ``{cell_name: {...}}``.
STATE_POLICIES_KEY = "state_policies"


class StateMergeConflict(Exception):
    """Two writers wrote one cell and no policy says how to resolve it.

    ★ Loud by design. The alternative is a plausible value produced by whichever branch happened
    to finish last — wrong in a way that survives review, reproduces intermittently, and is
    indistinguishable from a correct answer in the record.
    """


#: Reduce operators. ★ Every one is **commutative and associative**, which is the property that
#: makes the merge order-independent and therefore deterministic. Adding a non-commutative
#: operator (subtraction, string concatenation of unordered branches) would silently reintroduce
#: completion-order dependence through the one policy that is supposed to be immune to it.
_REDUCERS: dict[str, Callable[[Any, Any], Any]] = {
    "sum": operator.add,
    "max": max,
    "min": min,
    "and": lambda a, b: bool(a) and bool(b),
    "or": lambda a, b: bool(a) or bool(b),
    "union": lambda a, b: sorted(set(a) | set(b)),
}


def _cells_by_writer(patches: Sequence[tuple[str, Mapping[str, Any]]]) -> dict[str, list[str]]:
    """``{cell: [writer, ...]}`` in declaration order."""
    seen: dict[str, list[str]] = {}
    for writer, patch in patches:
        for cell in patch or {}:
            seen.setdefault(str(cell), []).append(str(writer))
    return seen


def _resolve(
    cell: str,
    writers: list[str],
    values: list[Any],
    policy: Optional[Mapping[str, Any]],
) -> Any:
    if policy is None:
        raise StateMergeConflict(
            f"state cell {cell!r} was written by {writers} in one superstep and no conflict "
            f"policy is declared for it. Declare one under "
            f"{STATE_POLICIES_KEY!r} — the runtime will not pick a default, because "
            f"last-write-wins is correct for a latest-reading cell and wrong for a counter, "
            f"and guessing produces a plausible value nobody checks."
        )

    name = str(policy.get("policy") or "")
    if name not in POLICIES:
        raise StateMergeConflict(
            f"state cell {cell!r} declares unknown policy {name!r}; expected one of "
            f"{sorted(POLICIES)}"
        )

    if name == POLICY_LAST_WRITE_WINS:
        # ★ Declaration order, never completion order — that distinction is the whole point.
        return values[-1]

    if name == POLICY_REDUCE:
        op_name = str(policy.get("op") or "")
        reducer = _REDUCERS.get(op_name)
        if reducer is None:
            raise StateMergeConflict(
                f"state cell {cell!r} declares reduce op {op_name!r}; expected one of "
                f"{sorted(_REDUCERS)}. Only commutative, associative operators are accepted — "
                f"anything else makes the result depend on which branch finished first."
            )
        return functools.reduce(reducer, values)

    # POLICY_BARRIER
    expected = [str(w) for w in (policy.get("writers") or [])]
    if not expected:
        raise StateMergeConflict(
            f"state cell {cell!r} declares a barrier with no 'writers' list; a barrier that "
            f"names no writers cannot tell a complete superstep from an incomplete one"
        )
    missing = [w for w in expected if w not in writers]
    if missing:
        raise StateMergeConflict(
            f"state cell {cell!r} is barrier-gated on {expected} and {missing} did not write. "
            f"Resolving it now would decide the cell from partial input."
        )
    return values[-1]


def merge_state(
    state: dict[str, Any],
    patches: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    policies: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Merge ordered ``(writer, patch)`` pairs into ``state``, in place.

    ``patches`` must be in **declaration order** — the order the branches appear in the flow
    definition, not the order they completed. Passing completion order would make
    ``last_write_wins`` nondeterministic and defeat the module.

    ★ With a single patch this is exactly ``state.update(patch)``: no policy is consulted and
    none is required. That is the sequential path the engine takes today, and keeping it
    byte-for-byte equivalent is what makes this safe to wire in before fan-out exists.
    """
    ordered = [(str(w), dict(p or {})) for w, p in patches]
    if len(ordered) <= 1:
        for _, patch in ordered:
            state.update(patch)
        return state

    policies = policies or {}
    writers_by_cell = _cells_by_writer(ordered)

    for cell, writers in writers_by_cell.items():
        values = [p[cell] for _, p in ordered if cell in p]
        if len(writers) == 1:
            # ★ Disjoint writes need no declaration. Requiring one would make fan-out
            # unusable for its most common shape — branches that touch different cells.
            state[cell] = values[0]
            continue
        state[cell] = _resolve(cell, writers, values, policies.get(cell))
    return state


def declared_policies(flow: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Read a flow definition's declared per-cell policies, tolerating absence."""
    raw = (flow or {}).get(STATE_POLICIES_KEY)
    return dict(raw) if isinstance(raw, Mapping) else {}
