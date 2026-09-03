"""A stable fingerprint of a flow's *shape*, so a suspended run cannot resume into a different one.

`FLOW-GRAPH-SIGNATURE-1`. A `FlowRun` is restored against whatever definition
`register_flows()` produced **this boot**. Nothing recorded what the run was planned against and
nothing detected that it changed, so a node renamed or an edge rerouted between suspend and
resume was executed against a definition the run was never planned for — **silently and
successfully**, with no error, no warning, and no row saying the shape had moved.

That matters more now than when it was filed. `FR-15` made a resume a durable queue message
rather than an in-process closure, so a resume can sit in Redis **across a deploy** and be picked
up by a worker running different code. The window this guards was widened by our own fix.

★★ WHAT GOES IN THE HASH IS THE WHOLE DESIGN, AND IT IS A NARROW TARGET
------------------------------------------------------------------------
A hash that changes on every deploy quarantines every in-flight run and gets switched off within
a week. A hash that ignores too much validates nothing. So this covers **node identities and edge
topology, and nothing else**:

**In:** `start`; the set of terminal nodes; every edge source; each source's targets **in order**,
because `resolve_next_node` takes the first matching edge and order is therefore meaning; and
*whether* an edge is predicate-gated.

**Out — deliberately:**

- **Node bodies.** A node's implementation is not its identity. Rewriting one does not move the
  run's shape, and hashing bodies would trip on every refactor.
- **`node_configs`.** Configuration, not topology. A retry count or a timeout changing must not
  strand in-flight runs.
- **Predicate implementations *and their names*.** A conditional edge contributes "this edge is
  gated" and its target — not which callable gates it. Including the name would trip when a
  lambda becomes a named function, which is a refactor and not a reroute. This is the line MAF
  draws too: the shape is data, the predicate is not.

The cost of that choice, stated plainly rather than discovered later: **a changed predicate that
reroutes control flow will NOT be caught.** The guard detects a moved *graph*, not a changed
*decision*. Catching the latter needs the predicate to be data — which is `FLOW-PARALLEL-1`'s
topology-as-data question, deliberately not bundled here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

#: Bumped only if the canonical form below changes. It is mixed into the digest so a signature
#: computed by an older runtime cannot silently compare equal to one computed by a newer one —
#: two different canonicalisations agreeing by luck is worse than not comparing at all.
SIGNATURE_VERSION = 1


def _canonical_edges(edges: Any) -> dict[str, list[Any]]:
    """Edge topology in a form that is stable across runs and blind to implementations."""
    if not isinstance(edges, dict):
        return {}

    canonical: dict[str, list[Any]] = {}
    for source in sorted(str(k) for k in edges):
        raw = edges.get(source) or []
        if not isinstance(raw, (list, tuple)):
            raw = [raw]

        targets: list[Any] = []
        for edge in raw:
            if isinstance(edge, dict):
                # A conditional edge. The target and the fact that it is gated are topology;
                # the callable under "condition" is an implementation and is not read at all.
                targets.append({"target": str(edge.get("target") or ""), "gated": True})
            else:
                targets.append(str(edge))
        # NOT sorted: `resolve_next_node` returns the first matching edge, so reordering the
        # targets of one node changes which branch runs. Order is topology here.
        canonical[source] = targets
    return canonical


def flow_topology_signature(flow: dict[str, Any]) -> str:
    """A hex digest of ``flow``'s shape. Same shape in, same digest out, on any machine."""
    if not isinstance(flow, dict):
        flow = {}

    canonical = {
        "v": SIGNATURE_VERSION,
        "start": str(flow.get("start") or ""),
        # Sorted: `end` is used as a membership test ("is this node terminal"), so its order
        # carries no meaning and sorting keeps the digest stable across an incidental reorder.
        "end": sorted(str(n) for n in (flow.get("end") or [])),
        "edges": _canonical_edges(flow.get("edges")),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def signatures_conflict(recorded: str | None, current: str | None) -> bool:
    """Whether a recorded signature *positively disagrees* with the current definition.

    ★★ ABSENT IS NOT MISMATCH, AND THIS IS THE MOST IMPORTANT LINE IN THE MODULE.

    A run created before this shipped has no recorded signature. So does a run whose flow is no
    longer registered. Treating either as a conflict would quarantine every in-flight run the
    moment this deploys — which is precisely the failure mode that gets a guard like this
    switched off within a week, and the reason the entry insisted on settling the hash contents
    before writing them.

    A conflict requires **two known signatures that differ**. Anything else is "cannot tell",
    and cannot-tell proceeds exactly as before.
    """
    return bool(recorded) and bool(current) and recorded != current
