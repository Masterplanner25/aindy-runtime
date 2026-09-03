"""`FLOW-GRAPH-SIGNATURE-1` — a suspended run must not resume into a different graph.

A `FlowRun` was restored against whatever definition `register_flows()` produced *this* boot.
Nothing recorded what it was planned against, so a node renamed or an edge rerouted between
suspend and resume executed against a definition the run was never planned for — silently, and
reported as success.

`FR-15` widened the window: a resume is now a durable queue message rather than an in-process
closure, so one can sit in Redis **across a deploy** and be dequeued by a worker running
different code.

★★ WHAT THESE TESTS ARE ACTUALLY DEFENDING
--------------------------------------------
Not "does it hash" — any two lines of code hash. The design question is **what goes in**, and it
has exactly two failure modes, in opposite directions:

- **Too sensitive** → the signature changes on every deploy, every in-flight run is quarantined,
  and the guard is switched off within a week. This is the more likely failure and the harder one
  to walk back, so most of the tests below assert that something does *not* move the signature.
- **Too blind** → it validates nothing and the silent resume is still there.

So the file is organised as two halves: what must change the signature, and what must not. A
change to `graph_signature.py` that breaks either half is a design change, not a refactor, and
should be argued rather than absorbed.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

FLOW = {
    "start": "a",
    "edges": {"a": ["b"], "b": ["c"]},
    "end": ["c"],
}


def _sig(flow):
    from AINDY.runtime.flow_engine.graph_signature import flow_topology_signature

    return flow_topology_signature(flow)


# ── half 1: what MUST move the signature ─────────────────────────────────────


def test_a_renamed_node_moves_the_signature():
    """The canonical case from the entry: a node renamed between suspend and resume."""
    renamed = {"start": "a", "edges": {"a": ["b_renamed"], "b_renamed": ["c"]}, "end": ["c"]}
    assert _sig(FLOW) != _sig(renamed)


def test_a_rerouted_edge_moves_the_signature():
    """The other canonical case: same nodes, different wiring."""
    rerouted = {"start": "a", "edges": {"a": ["c"], "b": ["c"]}, "end": ["c"]}
    assert _sig(FLOW) != _sig(rerouted)


def test_a_different_start_node_moves_the_signature():
    assert _sig(FLOW) != _sig({**FLOW, "start": "b"})


def test_a_changed_terminal_set_moves_the_signature():
    assert _sig(FLOW) != _sig({**FLOW, "end": ["b"]})


def test_reordering_one_nodes_targets_moves_the_signature():
    """★ Order is topology here, and it would be easy to sort it away by accident.

    `resolve_next_node` returns the **first** matching edge, so swapping the order of a node's
    targets changes which branch runs. A canonicalisation that sorted them would call two
    genuinely different graphs identical — the "too blind" failure, arriving as a tidiness fix.
    """
    a = {"start": "a", "edges": {"a": ["x", "y"]}, "end": ["x", "y"]}
    b = {"start": "a", "edges": {"a": ["y", "x"]}, "end": ["x", "y"]}
    assert _sig(a) != _sig(b)


def test_making_an_edge_conditional_moves_the_signature():
    """*Whether* an edge is gated is shape, even though *what* gates it is not."""
    plain = {"start": "a", "edges": {"a": ["b"]}, "end": ["b"]}
    gated = {"start": "a", "edges": {"a": [{"condition": lambda s: True, "target": "b"}]}, "end": ["b"]}
    assert _sig(plain) != _sig(gated)


# ── half 2: what MUST NOT move the signature ─────────────────────────────────


def test_a_different_predicate_does_not_move_the_signature():
    """★★ THE MOST IMPORTANT ASSERTION IN THE FILE, and it is a deliberate blind spot.

    Two different callables gating the same edge produce the same signature. That is the design:
    a predicate is an implementation, and hashing implementations means a lambda becoming a named
    function — a refactor — strands every in-flight run.

    **The cost is real and is stated in the module docstring rather than discovered later:** a
    changed predicate that reroutes control flow will NOT be caught. This guard detects a moved
    graph, not a changed decision. Catching the latter needs the predicate to be data, which is
    `FLOW-PARALLEL-1`'s question and deliberately not bundled here.
    """
    def _named(state):  # noqa: ANN001
        return True

    one = {"start": "a", "edges": {"a": [{"condition": lambda s: True, "target": "b"}]}, "end": ["b"]}
    two = {"start": "a", "edges": {"a": [{"condition": _named, "target": "b"}]}, "end": ["b"]}
    assert _sig(one) == _sig(two), (
        "a changed predicate moved the signature. Every refactor of a branch condition would "
        "now quarantine in-flight runs, which is how this guard gets switched off."
    )


def test_node_configs_do_not_move_the_signature():
    """Configuration is not topology. A retry count changing must not strand in-flight runs."""
    assert _sig(FLOW) == _sig({**FLOW, "node_configs": {"a": {"retries": 3}}})


def test_reordering_the_terminal_set_does_not_move_the_signature():
    """`end` is a membership test, so its order carries no meaning."""
    a = {"start": "a", "edges": {"a": ["x"]}, "end": ["x", "y"]}
    b = {"start": "a", "edges": {"a": ["x"]}, "end": ["y", "x"]}
    assert _sig(a) == _sig(b)


def test_the_signature_is_stable_across_calls_and_dict_ordering():
    """It has to survive being computed on a different machine, in a different process.

    A signature that depended on dict insertion order would compare unequal to itself after a
    round trip through the registry, quarantining healthy runs at random — the worst version of
    the "too sensitive" failure, because it would be intermittent.
    """
    reordered = {"end": ["c"], "edges": {"b": ["c"], "a": ["b"]}, "start": "a"}
    assert _sig(FLOW) == _sig(reordered) == _sig(FLOW)


# ── the comparison, where absent must not mean mismatch ──────────────────────


def test_absent_signatures_are_not_a_conflict():
    """★★ THE LINE THAT KEEPS THIS DEPLOYABLE.

    A run created before the column existed has no signature; so does a run whose flow is no
    longer registered. Treating either as a conflict quarantines every in-flight run the moment
    this deploys — the exact failure the entry warned about, and the reason it insisted the hash
    contents be settled first.
    """
    from AINDY.runtime.flow_engine.graph_signature import signatures_conflict

    assert signatures_conflict(None, "abc") is False
    assert signatures_conflict("abc", None) is False
    assert signatures_conflict(None, None) is False
    assert signatures_conflict("", "abc") is False


def test_two_known_and_different_signatures_are_a_conflict():
    """Liveness control for the test above — otherwise "never conflicts" would satisfy it."""
    from AINDY.runtime.flow_engine.graph_signature import signatures_conflict

    assert signatures_conflict("abc", "def") is True
    assert signatures_conflict("abc", "abc") is False


# ── the wiring ───────────────────────────────────────────────────────────────


def test_a_started_run_records_the_signature_of_the_flow_it_ran(db_session):
    """Recorded at start, because that is the only moment the answer is unambiguous."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "sig_probe_node"

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        return {"status": "SUCCESS"}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("sig_probe_flow", flow)
    try:
        runner = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None)
        runner.start({}, flow_name="sig_probe_flow")

        run = (
            db_session.query(FlowRun)
            .filter(FlowRun.flow_name == "sig_probe_flow")
            .order_by(FlowRun.created_at.desc())
            .first()
        )
        assert run is not None
        assert run.graph_signature == _sig(flow), (
            "the run did not record the signature of the flow it was started with — nothing "
            "downstream can detect a change it never wrote down"
        )
    finally:
        reg.FLOW_REGISTRY.pop("sig_probe_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


def test_resuming_into_a_changed_graph_quarantines_instead_of_executing(db_session):
    """★★ THE BEHAVIOUR THE ENTRY ASKED FOR: loud, not silent.

    The run is suspended against one shape and resumed against another. It must be
    dead-lettered with a reason rather than executed — and the node must not run, which is the
    assertion that distinguishes "quarantined" from "quarantined after doing the damage".
    """
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "sig_change_node"
    ran: list[str] = []

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        ran.append(node)
        return {"status": "SUCCESS"}

    planned = {"start": node, "end": [node], "edges": {}}
    changed = {"start": node, "end": [node], "edges": {node: ["somewhere_new"]}}
    reg.register_flow("sig_change_flow", planned)
    try:
        run = FlowRun(
            flow_name="sig_change_flow",
            status="waiting",
            current_node=node,
            state={},
            graph_signature=_sig(planned),
        )
        db_session.add(run)
        db_session.flush()
        run_id = str(run.id)

        # Resumed against the CHANGED definition — the deploy-shaped scenario.
        runner = PersistentFlowRunner(flow=changed, db=db_session, user_id=None, workflow_type=None)
        response = runner.resume(run_id)

        assert ran == [], (
            "the node executed against a graph the run was never planned for — the guard did "
            "not fire, or fired too late to matter"
        )
        db_session.expire_all()
        reloaded = db_session.query(FlowRun).filter(FlowRun.id == run_id).first()
        assert reloaded.status == "dead_letter", (
            f"run left in status {reloaded.status!r}; a topology mismatch must quarantine"
        )
        assert reloaded.dead_letter_reason and "topology changed" in reloaded.dead_letter_reason
        assert response.get("status") == "FAILED"
    finally:
        reg.FLOW_REGISTRY.pop("sig_change_flow", None)
        reg.NODE_REGISTRY.pop(node, None)


def test_resuming_a_run_with_no_recorded_signature_still_works(db_session):
    """★ The liveness control for the quarantine, and the deployability guarantee.

    Without this, a guard that quarantined *everything* would satisfy the test above while
    breaking every pre-existing run on the day it deployed.
    """
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    node = "sig_legacy_node"
    ran: list[str] = []

    @reg.register_node(node)
    def _n(state, context):  # noqa: ANN001
        ran.append(node)
        return {"status": "SUCCESS"}

    flow = {"start": node, "end": [node], "edges": {}}
    reg.register_flow("sig_legacy_flow", flow)
    try:
        run = FlowRun(
            flow_name="sig_legacy_flow",
            status="waiting",
            current_node=node,
            state={},
            graph_signature=None,  # predates the column
        )
        db_session.add(run)
        db_session.flush()

        runner = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None)
        runner.resume(str(run.id))

        assert ran == [node], (
            "a run with no recorded signature was not resumed. Absent must mean 'cannot tell' "
            "and proceed — treating it as a mismatch quarantines every in-flight run on deploy."
        )
    finally:
        reg.FLOW_REGISTRY.pop("sig_legacy_flow", None)
        reg.NODE_REGISTRY.pop(node, None)
