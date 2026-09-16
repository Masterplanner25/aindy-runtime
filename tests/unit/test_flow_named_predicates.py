"""`FLOW-PARALLEL-1` phase 3a — a predicate can be NAMED, and the name is shape.

A conditional edge was `{"target": …, "condition": <callable>}` — a Python closure the graph
signature could only record as "gated". So a predicate rerouted between suspend and resume was
not caught: `FLOW-GRAPH-SIGNATURE-1`'s deliberate blind spot, which names this phase as the fix.
MAF hit the same wall and answered it the same way: serialize the shape, name the predicate,
fail loudly if it is missing on restore.

Now `{"target": …, "when": "<name>"}` names a registered pure function of state.

What is pinned, in the design's two halves:

* **what must NOT move** — a callable-gated flow's digest is byte-for-byte what it was before
  this shipped (a RECORDED digest, computed on the commit before the change: a comparison
  between two encodings that move together cannot pin that — phase 2's lesson), and a named
  edge's digest does not depend on the callable behind the name;
* **what MUST move** — naming an edge, renaming its predicate, rerouting its target. The
  blind-spot closure is the inverse of `test_a_different_predicate_does_not_move_the_signature`,
  which stays true for callables;
* **the registry** — a name is an identity: rebinding it to a different callable is refused,
  re-importing the same function is not;
* **resolution** — first match wins in declaration order; `default` is the named fall-through;
  a `when` naming nothing FAILS THE RUN with the name in the reason (never "does not match",
  never a 500 that leaves the row `executing`);
* **the run** — a real `PersistentFlowRunner` branches by name, and a run suspended under one
  named decision QUARANTINES when resumed under a renamed one — the blind spot, closed, observed
  end to end.

Phase 3b (`SwitchCaseEdgeGroup`) was DECLINED as redundant: an ordered `when` list ending in
`default` already is a switch-case here — first match, explicit default, loud no-match.

Mutation-checked: drop the `when` key from `_canonical_edges` (encode as gated) → the three
must-move tests and the quarantine test fail; make `resolve_predicate` return the default on an
unknown name → the missing-name tests fail; drop the identity check in `register_predicate` →
the rebind test fails; drop the `resolve_frontier` try/except in `_advance_to_next_node` → the
missing-name RUN test fails (exception escapes, row left `executing`).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

# Computed on main at f6e5dbc (before this change) for the fixture below. If this moves, every
# callable-gated flow in flight quarantines on upgrade.
_CALLABLE_GATED_DIGEST_BEFORE = "619f72385b5c199f3e18596c02b47d543655e9651663bdbc5f67179c3158e7a2"


def _callable_gated_flow():
    return {
        "start": "a",
        "edges": {"a": [{"condition": lambda st: True, "target": "b"}, {"condition": lambda st: False, "target": "c"}]},
        "end": ["b", "c"],
    }


def _named_flow(first="go_left", second="default"):
    return {
        "start": "a",
        "edges": {"a": [{"when": first, "target": "b"}, {"when": second, "target": "c"}]},
        "end": ["b", "c"],
    }


def _sig(flow):
    from AINDY.runtime.flow_engine.graph_signature import flow_topology_signature

    return flow_topology_signature(flow)


@pytest.fixture
def predicates():
    """Register predicates for one test and remove them after — the registry is process-global."""
    from AINDY.runtime.flow_engine import registry as reg

    added: list[str] = []

    def _add(name, fn):
        reg.register_predicate(name)(fn)
        added.append(name)
        return fn

    yield _add
    for name in added:
        reg.PREDICATE_REGISTRY.pop(name, None)


# ── the registry ─────────────────────────────────────────────────────────────


def test_default_is_registered_and_always_true():
    from AINDY.runtime.flow_engine.registry import DEFAULT_PREDICATE, PREDICATE_REGISTRY

    assert PREDICATE_REGISTRY[DEFAULT_PREDICATE]({}) is True
    assert PREDICATE_REGISTRY[DEFAULT_PREDICATE]({"anything": 1}) is True


def test_rebinding_a_name_to_a_different_callable_is_refused(predicates):
    from AINDY.runtime.flow_engine.registry import PredicateRegistrationError, register_predicate

    def one(state):  # noqa: ANN001
        return True

    def two(state):  # noqa: ANN001
        return False

    predicates("np_rebind", one)
    with pytest.raises(PredicateRegistrationError, match="np_rebind"):
        register_predicate("np_rebind")(two)


def test_reregistering_the_same_function_is_a_no_op(predicates):
    """The same function object, and the same qualified name from a re-imported module."""
    import types

    from AINDY.runtime.flow_engine.registry import PREDICATE_REGISTRY, register_predicate

    def same(state):  # noqa: ANN001
        return True

    predicates("np_same", same)
    register_predicate("np_same")(same)
    # A re-import: a NEW function object with the same module + qualname.
    twin = types.FunctionType(same.__code__, same.__globals__, same.__name__)
    twin.__qualname__ = same.__qualname__
    twin.__module__ = same.__module__
    register_predicate("np_same")(twin)
    assert PREDICATE_REGISTRY["np_same"] is twin


# ── resolution ───────────────────────────────────────────────────────────────


def test_first_matching_named_edge_wins_and_default_falls_through(predicates):
    from AINDY.runtime.flow_engine.node_executor import resolve_next_node

    predicates("np_go_left", lambda s: s.get("side") == "left")
    flow = _named_flow("np_go_left", "default")
    assert resolve_next_node("a", {"side": "left"}, flow) == "b"
    assert resolve_next_node("a", {"side": "right"}, flow) == "c"


def test_no_match_and_no_default_is_none_like_a_callable_list(predicates):
    from AINDY.runtime.flow_engine.node_executor import resolve_next_node

    predicates("np_never", lambda s: False)
    flow = {"start": "a", "edges": {"a": [{"when": "np_never", "target": "b"}]}, "end": ["b"]}
    assert resolve_next_node("a", {}, flow) is None


def test_an_unknown_name_is_loud_not_a_non_match():
    from AINDY.runtime.flow_engine.node_executor import resolve_next_node
    from AINDY.runtime.flow_engine.registry import UnknownPredicate

    flow = {"start": "a", "edges": {"a": [{"when": "np_missing", "target": "b"}, {"when": "default", "target": "c"}]}, "end": ["b", "c"]}
    with pytest.raises(UnknownPredicate) as exc_info:
        resolve_next_node("a", {}, flow)
    assert "np_missing" in str(exc_info.value) and "'a'" in str(exc_info.value)


@pytest.mark.parametrize(
    "edge",
    [
        {"target": "b", "condition": lambda s: True, "when": "default"},
        {"target": "b"},
    ],
    ids=["both-gates", "no-gate"],
)
def test_a_dict_edge_has_exactly_one_gate(edge):
    from AINDY.runtime.flow_engine.node_executor import resolve_next_node

    with pytest.raises(ValueError, match="'a'"):
        resolve_next_node("a", {}, {"start": "a", "edges": {"a": [edge]}, "end": ["b"]})


# ── the signature: what must NOT move ────────────────────────────────────────


def test_a_callable_gated_flow_keeps_its_recorded_digest():
    """★★ Every callable-gated flow in flight is hashed under the pre-change encoding."""
    assert _sig(_callable_gated_flow()) == _CALLABLE_GATED_DIGEST_BEFORE, (
        "the encoding of a callable-gated edge moved; every suspended run on such a flow "
        "would quarantine on upgrade"
    )


def test_the_callable_behind_a_name_does_not_move_the_signature(predicates):
    """Consistent with the callable rule: the NAME is the identity, the implementation is not.
    (Rebinding is refused in one process; this is two deploys with the same name.)"""
    predicates("np_impl", lambda s: True)
    before = _sig(_named_flow("np_impl"))
    from AINDY.runtime.flow_engine import registry as reg

    reg.PREDICATE_REGISTRY["np_impl"] = lambda s: False  # what a redeploy with a changed body is
    assert _sig(_named_flow("np_impl")) == before


# ── the signature: what MUST move ────────────────────────────────────────────


def test_naming_an_edge_moves_the_signature():
    """Migrating an edge from `condition` to `when` is a one-time move — the cost the design
    states, and the reason the runtime's own flows have not migrated."""
    callable_flow = {"start": "a", "edges": {"a": [{"condition": lambda s: True, "target": "b"}]}, "end": ["b"]}
    named_flow = {"start": "a", "edges": {"a": [{"when": "default", "target": "b"}]}, "end": ["b"]}
    assert _sig(callable_flow) != _sig(named_flow)


def test_renaming_a_named_predicate_moves_the_signature():
    """★★ THE BLIND SPOT, CLOSED — the inverse of
    `test_a_different_predicate_does_not_move_the_signature`, which stays true for callables."""
    assert _sig(_named_flow("np_one")) != _sig(_named_flow("np_two"))


def test_rerouting_a_named_edge_moves_the_signature():
    a = {"start": "a", "edges": {"a": [{"when": "np_x", "target": "b"}]}, "end": ["b", "c"]}
    b = {"start": "a", "edges": {"a": [{"when": "np_x", "target": "c"}]}, "end": ["b", "c"]}
    assert _sig(a) != _sig(b)


def test_reordering_named_edges_moves_the_signature():
    """First match wins, so order is meaning — as it already was for callable edges."""
    assert _sig(_named_flow("np_one", "np_two")) != _sig(_named_flow("np_two", "np_one"))


# ── the run ──────────────────────────────────────────────────────────────────


@pytest.fixture
def scheduler_spy():
    from unittest.mock import MagicMock, patch

    engine = MagicMock()
    with patch("AINDY.kernel.scheduler_engine.get_scheduler_engine", return_value=engine):
        yield engine


def _register_nodes(reg, names, ran):
    for n in names:
        def _mk(name):
            def _node(state, context):  # noqa: ANN001
                ran.append(name)
                return {"status": "SUCCESS", "output_patch": {}}
            return _node
        reg.register_node(n)(_mk(n))


def test_a_real_run_branches_by_name(db_session, scheduler_spy, predicates):
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    predicates("np_run_left", lambda s: s.get("side") == "left")
    nodes = ["np_a", "np_b", "np_c"]
    ran: list[str] = []
    _register_nodes(reg, nodes, ran)
    flow = {
        "start": "np_a",
        "edges": {"np_a": [{"when": "np_run_left", "target": "np_b"}, {"when": "default", "target": "np_c"}]},
        "end": ["np_b", "np_c"],
    }
    reg.register_flow("np_branch_flow", flow)
    try:
        for side, expected in (("left", "np_b"), ("right", "np_c")):
            ran.clear()
            result = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
                {"side": side}, flow_name="np_branch_flow"
            )
            assert result["status"] in {"COMPLETED", "SUCCESS"}, result
            assert ran == ["np_a", expected], (side, ran)
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "np_branch_flow").first()
        assert run.graph_signature == _sig(flow)
    finally:
        reg.FLOW_REGISTRY.pop("np_branch_flow", None)
        for n in nodes:
            reg.NODE_REGISTRY.pop(n, None)


def test_a_missing_name_fails_the_run_with_the_name_in_the_reason(db_session, scheduler_spy):
    """Loud on restore: the run goes `failed` naming the predicate. It must not end quietly,
    and it must not escape as an exception that leaves the row `executing`."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    nodes = ["np_m_a", "np_m_b"]
    ran: list[str] = []
    _register_nodes(reg, nodes, ran)
    flow = {"start": "np_m_a", "edges": {"np_m_a": [{"when": "np_not_registered", "target": "np_m_b"}]}, "end": ["np_m_b"]}
    reg.register_flow("np_missing_flow", flow)
    try:
        result = PersistentFlowRunner(flow=flow, db=db_session, user_id=None, workflow_type=None).start(
            {}, flow_name="np_missing_flow"
        )
        assert result["status"] == "FAILED", result
        assert "np_not_registered" in str(result.get("data") or result)
        assert ran == ["np_m_a"], "the edge is resolved AFTER the node, so the node ran; the successor must not"
        db_session.expire_all()
        run = db_session.query(FlowRun).filter(FlowRun.flow_name == "np_missing_flow").one()
        assert run.status == "failed", run.status
    finally:
        reg.FLOW_REGISTRY.pop("np_missing_flow", None)
        for n in nodes:
            reg.NODE_REGISTRY.pop(n, None)


def test_a_run_suspended_under_one_named_decision_quarantines_under_a_renamed_one(db_session, predicates):
    """★★ The blind spot closed, end to end. The same scenario as
    `test_resuming_into_a_changed_graph_quarantines_instead_of_executing`, except that the
    ONLY thing that changed between suspend and resume is which decision gates the edge —
    exactly what the callable form could not see."""
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import registry as reg
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    predicates("np_before", lambda s: True)
    predicates("np_after", lambda s: True)
    node, nxt = "np_q_a", "np_q_b"
    ran: list[str] = []
    _register_nodes(reg, [node, nxt], ran)
    planned = {"start": node, "end": [nxt], "edges": {node: [{"when": "np_before", "target": nxt}]}}
    rerouted = {"start": node, "end": [nxt], "edges": {node: [{"when": "np_after", "target": nxt}]}}
    reg.register_flow("np_quarantine_flow", planned)
    try:
        run = FlowRun(
            flow_name="np_quarantine_flow", status="waiting", current_node=node, state={},
            graph_signature=_sig(planned),
        )
        db_session.add(run)
        db_session.flush()
        run_id = str(run.id)

        response = PersistentFlowRunner(flow=rerouted, db=db_session, user_id=None, workflow_type=None).resume(run_id)

        assert ran == [], "the node executed under a decision the run was never planned for"
        db_session.expire_all()
        reloaded = db_session.query(FlowRun).filter(FlowRun.id == run_id).one()
        assert reloaded.status == "dead_letter", reloaded.status
        assert response.get("status") == "FAILED" and response.get("data", {}).get("quarantined") is True
    finally:
        reg.FLOW_REGISTRY.pop("np_quarantine_flow", None)
        for n in (node, nxt):
            reg.NODE_REGISTRY.pop(n, None)
