"""FLOW-PARALLEL-1 phase 2 — join policies, resolved at the barrier.

Phase 1 shipped the degenerate join: every branch of a fan-out group must succeed and converge.
The design said that was half a primitive and not to close on it. Phase 2 declares the join on
the group — `all` (phase 1 exactly), `any`, `quorum(k)` — and gives a superstep that proceeds
past a failed branch the vocabulary it lacked: **`partial`** (`EFFECT-PARTIAL-1`), naming the
branches that did not land, on the run's state, on the completion event and on the
`sys.v1.flow.run` envelope. This is the first thing in the runtime that emits a `partial`
outcome; `test_syscall_outcome.py`'s census records it.

What is pinned, and why each matters:

* `all` is byte-for-byte phase 1 — including its graph signature, so a run suspended on a
  phase-1 group does not quarantine on upgrade;
* a lenient join that proceeds is `partial`, never silent, never `success`;
* a lenient join that cannot be satisfied fails whole, naming the join and the count;
* convergence is required of the SUCCEEDED branches only;
* the merge is unchanged — a failed branch's patch does not land;
* the `flow.run` envelope says `partial` end to end, through the real dispatcher.

Mutation-checked: make `resolve_join` count failures as successes and five tests fail; drop the
`_superstep_partials` write and the partial tests fail; encode `all` in the signature and the
signature test fails; converge over all branches instead of succeeded and the convergence test
fails; drop the OUTCOME_KEY lift in `_handle_flow_run` and the envelope test fails.
"""
from __future__ import annotations

import pytest

from AINDY.runtime.flow_engine.fan_out import FanOutEdgeGroup, resolve_join
from AINDY.runtime.flow_engine.graph_signature import flow_topology_signature

pytestmark = pytest.mark.runtime_only


# ── the declaration ───────────────────────────────────────────────────────────


def test_join_defaults_to_all_and_validates():
    g = FanOutEdgeGroup(["a", "b", "c"])
    assert g.join == "all" and g.required_successes == 3 and g.join_label == "all"
    assert FanOutEdgeGroup(["a", "b"], join="any").required_successes == 1
    q = FanOutEdgeGroup(["a", "b", "c"], join="quorum", quorum=2)
    assert q.required_successes == 2 and q.join_label == "quorum:2"
    with pytest.raises(ValueError, match="join must be one of"):
        FanOutEdgeGroup(["a", "b"], join="most")
    with pytest.raises(ValueError, match="needs quorum=k"):
        FanOutEdgeGroup(["a", "b"], join="quorum")
    with pytest.raises(ValueError, match="needs quorum=k"):
        FanOutEdgeGroup(["a", "b"], join="quorum", quorum=3)
    with pytest.raises(ValueError, match="only meaningful with join='quorum'"):
        FanOutEdgeGroup(["a", "b"], join="any", quorum=1)


def test_resolve_join_is_declaration_ordered_and_names_the_failed():
    g = FanOutEdgeGroup(["a", "b", "c"], join="any")
    out = resolve_join(g, {"c": "SUCCESS", "a": "FAILURE", "b": "SUCCESS"})
    assert out.succeeded == ("b", "c") and out.failed == ("a",)
    assert out.satisfied and out.partial
    out = resolve_join(FanOutEdgeGroup(["a", "b"]), {"a": "SUCCESS", "b": "FAILURE"})
    assert not out.satisfied and not out.partial, "`all` with a failure is not satisfied and not partial"
    out = resolve_join(FanOutEdgeGroup(["a", "b"], join="any"), {"a": "SUCCESS", "b": "SUCCESS"})
    assert out.satisfied and not out.partial, "nothing failed: a plain success, not a partial"


def test_a_default_join_keeps_the_phase_1_signature():
    """★ Every run suspended on a phase-1 group hashed `{"fan_out": [...]}`. `all` must keep
    that digest exactly, or upgrading quarantines them (FLOW-GRAPH-SIGNATURE-1's rule)."""
    phase1 = {"start": "a", "end": ["b", "c"], "edges": {"a": [FanOutEdgeGroup(["b", "c"])], "b": [], "c": []}}
    # ★ Recorded under the PHASE-1 canonicaliser (main @ 106cf68, before the join existed). A
    #   comparison between two encodings that move together cannot pin this; only a constant can
    #   — the same reason `test_existing_flow_signatures_are_unchanged` pins digests.
    assert flow_topology_signature(phase1) == "46166a313ea2fa60719d3b7a2ae6411fc919ef9e7464555df69cee7c5eaf259f"
    explicit_all = {"start": "a", "end": ["b", "c"], "edges": {"a": [FanOutEdgeGroup(["b", "c"], join="all")], "b": [], "c": []}}
    assert flow_topology_signature(phase1) == flow_topology_signature(explicit_all)
    lenient = {"start": "a", "edges": {"a": [FanOutEdgeGroup(["b", "c"], join="any")], "b": [], "c": []}}
    assert flow_topology_signature(phase1) != flow_topology_signature(lenient), (
        "the join decides which successors are reachable — it is shape, and a run planned under "
        "`all` must not resume under `any`"
    )
    q2 = {"start": "a", "edges": {"a": [FanOutEdgeGroup(["b", "c"], join="quorum", quorum=2)], "b": [], "c": []}}
    q1 = {"start": "a", "edges": {"a": [FanOutEdgeGroup(["b", "c"], join="quorum", quorum=1)], "b": [], "c": []}}
    assert flow_topology_signature(q2) != flow_topology_signature(q1)


# ── the barrier ───────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *a, **kw):
        return self

    def scalar(self):
        return self._value


class _FakeRunnerDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    def query(self, *a, **kw):
        return _FakeQuery(None)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


class _Run:
    id = "22222222-2222-2222-2222-222222222222"
    current_node = "fan"


def _runner(flow):
    from AINDY.runtime.flow_engine.runner import PersistentFlowRunner

    class _R:
        db = _FakeRunnerDB()
        _execute_superstep = PersistentFlowRunner._execute_superstep
        _merge_superstep = PersistentFlowRunner._merge_superstep
        _allocate_sequence_numbers = PersistentFlowRunner._allocate_sequence_numbers

    r = _R()
    r.flow = flow
    return r


def _flow(join="all", quorum=None, targets=("b", "c", "d")):
    return {
        "edges": {
            "fan": [FanOutEdgeGroup(targets, join=join, quorum=quorum)],
            **{t: ["join"] for t in targets},
            "join": [],
        }
    }


def _nodes(monkeypatch, failing: set[str]):
    import AINDY.runtime.flow_engine as fe

    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    monkeypatch.setattr(
        fe, "execute_node",
        lambda node, state, ctx: (
            {"status": "FAILURE", "error": f"{node} broke", "output_patch": {node: "MUST NOT LAND"}}
            if node in failing else {"status": "SUCCESS", "output_patch": {node: True}}
        ),
        raising=False,
    )


def test_any_proceeds_past_a_failed_branch_as_partial(monkeypatch):
    _nodes(monkeypatch, failing={"c"})
    r = _runner(_flow("any"))
    state: dict = {}

    nxt = r._execute_superstep(_Run(), state, {"db": object()}, ["b", "c", "d"], None)

    assert nxt == "join"
    assert state.get("b") is True and state.get("d") is True
    assert "c" not in state, "a failed branch's patch does not land"
    assert state["_superstep_partials"] == [{
        "superstep": "fan", "join": "any", "succeeded": ["b", "d"],
        "failed": [{"branch": "c", "error": "c broke"}],
    }]
    assert [h.status for h in r.db.added] == ["SUCCESS", "FAILURE", "SUCCESS"], "history shows what was attempted"


def test_any_with_every_branch_failed_fails_whole(monkeypatch):
    _nodes(monkeypatch, failing={"b", "c", "d"})
    r = _runner(_flow("any"))
    with pytest.raises(ValueError, match=r"join=any needs 1 of 3 to succeed and 0 did"):
        r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c", "d"], None)


def test_quorum_is_satisfied_at_k_and_not_below(monkeypatch):
    _nodes(monkeypatch, failing={"d"})
    r = _runner(_flow("quorum", quorum=2))
    state: dict = {}
    assert r._execute_superstep(_Run(), state, {"db": object()}, ["b", "c", "d"], None) == "join"
    assert state["_superstep_partials"][0]["join"] == "quorum:2"

    _nodes(monkeypatch, failing={"c", "d"})
    r = _runner(_flow("quorum", quorum=2))
    with pytest.raises(ValueError, match=r"join=quorum:2 needs 2 of 3 to succeed and 1 did"):
        r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c", "d"], None)


def test_all_still_fails_on_one_failure_exactly_as_phase_1(monkeypatch):
    _nodes(monkeypatch, failing={"c"})
    r = _runner(_flow("all"))
    state: dict = {}
    with pytest.raises(ValueError, match=r"join=all needs 3 of 3 to succeed and 2 did"):
        r._execute_superstep(_Run(), state, {"db": object()}, ["b", "c", "d"], None)
    assert "_superstep_partials" not in state, "`all` never reports partial — it fails whole"


def test_a_clean_superstep_records_no_partial(monkeypatch):
    _nodes(monkeypatch, failing=set())
    r = _runner(_flow("any"))
    state: dict = {}
    r._execute_superstep(_Run(), state, {"db": object()}, ["b", "c", "d"], None)
    assert "_superstep_partials" not in state


def test_convergence_is_required_of_the_succeeded_branches_only(monkeypatch):
    """A failed branch's successor is not consulted — it produced no state to choose against."""
    _nodes(monkeypatch, failing={"c"})
    flow = _flow("any")
    flow["edges"]["c"] = ["elsewhere"]  # would break convergence if it were counted
    flow["edges"]["elsewhere"] = []
    r = _runner(flow)
    state: dict = {}
    assert r._execute_superstep(_Run(), state, {"db": object()}, ["b", "c", "d"], None) == "join"

    _nodes(monkeypatch, failing=set())
    r = _runner(flow)
    with pytest.raises(ValueError, match="did not converge"):
        r._execute_superstep(_Run(), {}, {"db": object()}, ["b", "c", "d"], None)


# ── end to end: the run completes as `partial` and the envelope says so ───────


def test_flow_run_envelope_is_partial_when_a_lenient_join_proceeded(db_session, monkeypatch):
    """Through the REAL runner and the REAL dispatcher: the first `partial` emitter, end to end."""
    import uuid

    from AINDY.kernel import syscall_dispatcher as sd
    from AINDY.kernel.syscall_registry import SyscallContext
    from AINDY.runtime.flow_engine import registry as reg

    monkeypatch.delenv("AINDY_FLOW_FAN_OUT", raising=False)
    name = "join_probe_flow"
    for node, outcome in (("start_n", "SUCCESS"), ("ok_n", "SUCCESS"), ("bad_n", "FAILURE"), ("end_n", "SUCCESS")):
        def _make(status):
            def _n(state, context):  # noqa: ANN001
                if status == "FAILURE":
                    return {"status": "FAILURE", "error": "bad_n broke"}
                return {"status": "SUCCESS", "output_patch": {}}
            return _n
        reg.register_node(node)(_make(outcome))
    flow = {
        "start": "start_n", "end": ["end_n"],
        "edges": {
            "start_n": [FanOutEdgeGroup(["ok_n", "bad_n"], join="any")],
            "ok_n": ["end_n"], "bad_n": ["end_n"], "end_n": [],
        },
    }
    reg.register_flow(name, flow)
    try:
        ctx = SyscallContext(
            execution_unit_id="", user_id=str(uuid.uuid4()), capabilities=["flow.run"], trace_id="",
            metadata={"_db": db_session},
        )
        envelope = sd.get_dispatcher().dispatch(
            "sys.v1.flow.run", {"flow_name": name, "initial_state": {}}, ctx
        )
    finally:
        reg._flows.pop(name, None) if hasattr(reg, "_flows") else None

    assert envelope["status"] == "partial", envelope
    units = envelope["outcome"]["units"]
    assert units == [{"superstep": "start_n", "join": "any", "branch": "bad_n", "error": "bad_n broke"}]
    assert "_outcome" not in envelope["data"], "the marker never reaches the consumer twice"
    assert envelope["data"]["flow_result"]["status"] == "SUCCESS", "the run itself completed"
