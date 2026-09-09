"""`FLOW-PARALLEL-1` — the declared per-cell conflict policy, landed before fan-out exists.

The flow engine merged node output with ``state.update(patch)``: last-write-wins, harmless only
because plan steps are strictly sequential and there is never a second writer. The moment
fan-out arrives that becomes a **completion-order** race — two branches writing one cell yield
whichever finished last, which varies between runs and cannot be reproduced from the record.

★★ THE PROPERTY EVERY TEST HERE DEFENDS IS DETERMINISM, NOT MERGING
---------------------------------------------------------------------
A parallel flow that returns a different answer on replay is worse than a slow sequential one,
and it fails invisibly in exactly the cases people fan out for. So the assertions that matter
are the ones about *order independence* and about *refusing to guess* — not the ones showing a
merge produces a value.

★ AND THE SAFETY PROPERTY THAT MADE THIS LANDABLE
---------------------------------------------------
With a single patch, `merge_state` must be **exactly** `state.update(patch)`. That is today's
only path; if it differed at all, this would change live flow behaviour to prepare for a feature
that does not exist yet. The single-writer tests are therefore not filler — they are the reason
this could be wired onto the live path instead of sitting beside it unused.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


# ── the path that exists today ───────────────────────────────────────────────


def test_one_writer_is_exactly_state_update():
    """★★ THE SAFETY PROPERTY. Today's behaviour must be byte-for-byte unchanged."""
    from AINDY.runtime.flow_engine.state_merge import merge_state

    state = {"a": 1, "b": 2}
    merge_state(state, [("node_x", {"b": 99, "c": 3})])
    assert state == {"a": 1, "b": 99, "c": 3}


def test_one_writer_needs_no_policy():
    """A sequential flow must never have to declare anything to keep working."""
    from AINDY.runtime.flow_engine.state_merge import merge_state

    state: dict = {}
    merge_state(state, [("only", {"x": 1})], policies=None)
    assert state == {"x": 1}


def test_no_writers_is_a_no_op():
    from AINDY.runtime.flow_engine.state_merge import merge_state

    state = {"a": 1}
    assert merge_state(state, []) == {"a": 1}


# ── the refusal, which is the whole point ────────────────────────────────────


def test_an_undeclared_double_write_raises():
    """★★ THE BEHAVIOUR THIS MODULE EXISTS FOR.

    Not a warning, not a default. A silently-resolved conflict produces a plausible value that
    survives review and reproduces intermittently — the worst possible failure shape for a
    number someone acts on.
    """
    from AINDY.runtime.flow_engine.state_merge import StateMergeConflict, merge_state

    with pytest.raises(StateMergeConflict) as caught:
        merge_state({}, [("branch_a", {"total": 1}), ("branch_b", {"total": 2})])

    message = str(caught.value)
    assert "total" in message
    assert "branch_a" in message and "branch_b" in message, (
        "the error does not name the conflicting writers, so it cannot be acted on"
    )


def test_disjoint_writes_need_no_policy():
    """★ The liveness control for the refusal, and the common fan-out shape.

    Without this, a mechanism that refused *every* multi-writer superstep would pass the test
    above while making fan-out unusable — refusing branches that never contended at all.
    """
    from AINDY.runtime.flow_engine.state_merge import merge_state

    state: dict = {}
    merge_state(state, [("a", {"left": 1}), ("b", {"right": 2})])
    assert state == {"left": 1, "right": 2}


def test_an_unknown_policy_name_is_refused():
    from AINDY.runtime.flow_engine.state_merge import StateMergeConflict, merge_state

    with pytest.raises(StateMergeConflict):
        merge_state(
            {}, [("a", {"c": 1}), ("b", {"c": 2})],
            policies={"c": {"policy": "whatever_seems_right"}},
        )


# ── the three policies, each checked for the property that makes it safe ─────


def test_last_write_wins_resolves_in_declaration_order_not_completion_order():
    """★★ THE DISTINCTION THE WHOLE MODULE TURNS ON.

    `merge_state` is handed patches in declaration order. It must take the last of *those*,
    which is reproducible from the flow definition — not the last to arrive, which is not
    recorded anywhere and differs between runs.
    """
    from AINDY.runtime.flow_engine.state_merge import merge_state

    policies = {"reading": {"policy": "last_write_wins"}}
    state: dict = {}
    merge_state(state, [("a", {"reading": "first"}), ("b", {"reading": "second"})],
                policies=policies)
    assert state["reading"] == "second"

    # The same two branches supplied in the other declaration order resolve the other way —
    # which is what "declaration order decides" means, and is deterministic per definition.
    state = {}
    merge_state(state, [("b", {"reading": "second"}), ("a", {"reading": "first"})],
                policies=policies)
    assert state["reading"] == "first"


@pytest.mark.parametrize(
    "op,values,expected",
    [
        ("sum", [1, 2, 4], 7),
        ("max", [3, 9, 1], 9),
        ("min", [3, 9, 1], 1),
        ("or", [False, True], True),
        ("and", [True, False], False),
        ("union", [["a"], ["b", "a"]], ["a", "b"]),
    ],
)
def test_reduce_applies_the_declared_operator(op, values, expected):
    from AINDY.runtime.flow_engine.state_merge import merge_state

    patches = [(f"w{i}", {"cell": v}) for i, v in enumerate(values)]
    state: dict = {}
    merge_state(state, patches, policies={"cell": {"policy": "reduce", "op": op}})
    assert state["cell"] == expected


def test_reduce_is_order_independent():
    """★★ WHY `reduce` IS THE POLICY TO PREFER.

    Its operators are commutative and associative, so the answer does not depend on order at
    all — it is deterministic without anyone having to reason about branch ordering. That is a
    stronger guarantee than last-write-wins, which is merely *defined*.
    """
    from AINDY.runtime.flow_engine.state_merge import merge_state

    policy = {"cell": {"policy": "reduce", "op": "sum"}}
    forward: dict = {}
    merge_state(forward, [("a", {"cell": 1}), ("b", {"cell": 2}), ("c", {"cell": 4})],
                policies=policy)
    backward: dict = {}
    merge_state(backward, [("c", {"cell": 4}), ("b", {"cell": 2}), ("a", {"cell": 1})],
                policies=policy)
    assert forward == backward


def test_a_non_commutative_reduce_op_is_refused():
    """★ The guard that keeps `reduce`'s guarantee true.

    Accepting subtraction or ordered concatenation would reintroduce completion-order dependence
    through the one policy that is supposed to be immune to it — and it would do so under a name
    that tells the reader order does not matter.
    """
    from AINDY.runtime.flow_engine.state_merge import StateMergeConflict, merge_state

    with pytest.raises(StateMergeConflict) as caught:
        merge_state({}, [("a", {"c": 1}), ("b", {"c": 2})],
                    policies={"c": {"policy": "reduce", "op": "subtract"}})
    assert "commutative" in str(caught.value)


def test_a_barrier_refuses_an_incomplete_superstep():
    """★ A barrier decides nothing from partial input — it says which writer is missing."""
    from AINDY.runtime.flow_engine.state_merge import StateMergeConflict, merge_state

    policies = {"joined": {"policy": "barrier", "writers": ["a", "b", "c"]}}
    with pytest.raises(StateMergeConflict) as caught:
        merge_state({}, [("a", {"joined": 1}), ("b", {"joined": 2})], policies=policies)
    assert "c" in str(caught.value)


def test_a_complete_barrier_resolves():
    """Liveness control — without it, a barrier that always raised would pass the test above."""
    from AINDY.runtime.flow_engine.state_merge import merge_state

    state: dict = {}
    merge_state(
        state,
        [("a", {"joined": 1}), ("b", {"joined": 2})],
        policies={"joined": {"policy": "barrier", "writers": ["a", "b"]}},
    )
    assert state["joined"] == 2


def test_a_barrier_naming_no_writers_is_refused():
    """A barrier that names nobody cannot tell a complete superstep from an incomplete one."""
    from AINDY.runtime.flow_engine.state_merge import StateMergeConflict, merge_state

    with pytest.raises(StateMergeConflict):
        merge_state({}, [("a", {"c": 1}), ("b", {"c": 2})],
                    policies={"c": {"policy": "barrier", "writers": []}})


# ── the declaration, and the seam being live ─────────────────────────────────


def test_policies_are_read_from_the_flow_definition():
    from AINDY.runtime.flow_engine.state_merge import declared_policies

    assert declared_policies({"state_policies": {"c": {"policy": "last_write_wins"}}}) == {
        "c": {"policy": "last_write_wins"}
    }
    assert declared_policies({}) == {}
    assert declared_policies({"state_policies": "not-a-mapping"}) == {}


def test_the_engine_merges_through_the_seam():
    """★★ `ROUTE-AST-UNWIRED-1` — a policy that exists and is never consulted is not a policy.

    ★ **This test MOVED with the code it guards (FLOW-PARALLEL-1 phase 0), it was not weakened.**
    The merge used to live in `_handle_node_status`'s SUCCESS branch and this drove that
    function. The merge now belongs to the SUPERSTEP (`_merge_superstep`), because called per
    node it can only ever see one patch — and `last_write_wins` resolving over one patch at a
    time is completion order, which is the exact nondeterminism `state_merge` exists to prevent.
    So the assertion follows the seam, and is *stronger* than before: it now also pins that
    every successful branch reaches the merge together, in declaration order.

    Driven through the real function rather than asserted over source text: a string match would
    be satisfied by a commented-out call, which this repository has already been caught by.
    """
    from unittest.mock import patch

    import AINDY.runtime.flow_engine.runner_steps as rs

    calls: list = []
    runner = type("R", (), {"flow": {"state_policies": {"c": {"policy": "reduce", "op": "sum"}}}})()

    with patch.object(rs, "merge_state", side_effect=lambda s, p, **kw: calls.append((s, p, kw))):
        rs._merge_superstep(
            runner,
            {},
            [{"node": "n1", "patch": {"c": 1}, "status": "SUCCESS"}],
        )

    assert calls, "the flow engine does not merge through the policy seam"
    _, patches, kwargs = calls[0]
    assert patches == [("n1", {"c": 1})]
    assert kwargs["policies"] == {"c": {"policy": "reduce", "op": "sum"}}, (
        "the engine did not pass the flow's declared policies, so a declaration would be "
        "silently ignored at the only place it matters"
    )


def test_the_seam_passes_every_successful_branch_together_in_declaration_order():
    """★★ The property the move exists to make possible, and the reason it could not stay put.

    `merge_state` guarantees determinism only if it sees the whole superstep at once. One call
    per branch would apply patches in completion order no matter what the policy says.
    """
    from unittest.mock import patch

    import AINDY.runtime.flow_engine.runner_steps as rs

    calls: list = []
    runner = type("R", (), {"flow": {"state_policies": {"c": {"policy": "last_write_wins"}}}})()

    with patch.object(rs, "merge_state", side_effect=lambda s, p, **kw: calls.append(p)):
        rs._merge_superstep(
            runner,
            {},
            [
                {"node": "a", "patch": {"c": 1}, "status": "SUCCESS"},
                {"node": "b", "patch": {"c": 2}, "status": "SUCCESS"},
            ],
        )

    assert len(calls) == 1, (
        f"the superstep merged in {len(calls)} calls; merging per branch applies patches in "
        f"completion order and defeats every declared policy"
    )
    assert calls[0] == [("a", {"c": 1}), ("b", {"c": 2})], "declaration order was not preserved"


def test_only_successful_branches_contribute_a_patch():
    """★ Preserved behaviour, pinned because a refactor is exactly where it could be lost.

    When the merge lived in the SUCCESS branch, a WAIT or FAILED node's patch was never merged.
    That is unchanged — and it had to be checked rather than assumed, because widening a merge is
    a natural place to accidentally start including patches that were previously dropped.
    """
    from unittest.mock import patch

    import AINDY.runtime.flow_engine.runner_steps as rs

    calls: list = []
    runner = type("R", (), {"flow": {}})()

    with patch.object(rs, "merge_state", side_effect=lambda s, p, **kw: calls.append(p)):
        rs._merge_superstep(
            runner,
            {},
            [
                {"node": "ok", "patch": {"a": 1}, "status": "SUCCESS"},
                {"node": "waiting", "patch": {"b": 2}, "status": "WAIT"},
                {"node": "broken", "patch": {"c": 3}, "status": "FAILURE"},
            ],
        )

    assert calls[0] == [("ok", {"a": 1})], (
        "a non-SUCCESS branch's patch reached the merge; that is a behaviour change smuggled "
        "inside a refactor"
    )


def test_a_superstep_with_no_successful_branch_does_not_merge_at_all():
    """No writers means no merge call — not an empty merge, which would still consult policies."""
    from unittest.mock import patch

    import AINDY.runtime.flow_engine.runner_steps as rs

    calls: list = []
    runner = type("R", (), {"flow": {}})()

    with patch.object(rs, "merge_state", side_effect=lambda s, p, **kw: calls.append(p)):
        result = rs._merge_superstep(runner, {"untouched": True}, [
            {"node": "broken", "patch": {"c": 3}, "status": "FAILURE"},
        ])

    assert calls == []
    assert result == {"untouched": True}


def test_the_runner_actually_calls_the_merge_seam():
    """★★ `ROUTE-AST-UNWIRED-1` — restored after the phase 0 move nearly lost it.

    The original version of `test_the_engine_merges_through_the_seam` drove
    `_handle_node_status`, which **was** the function the runner calls, so it proved wiring for
    free. Relocating it to `_merge_superstep` made it drive the seam *directly* — one step
    removed from the live path — and a runner that silently stopped calling the seam would have
    kept every merge test green. That is precisely the failure this repository catalogues.

    Checked over the AST rather than by string match: a commented-out call must not satisfy it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("AINDY/runtime/flow_engine/runner.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_merge_superstep"
    ]

    assert calls, (
        "runner.py never calls _merge_superstep — declared state policies would be silently "
        "ignored on every run while the merge suite stayed green"
    )
