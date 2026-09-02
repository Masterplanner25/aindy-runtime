"""`FR-15`'s remaining half — a resume must be rebuildable from its identifier.

The scheduler's work is a closure. In thread mode that closure *is* the work; in distributed
mode it cannot be, so the resume could not be routed there at all — the payload would key on
an id no worker resolves, and a worker acknowledges an unresolvable job as **successfully
completed**. That is why `async_scheduler_dispatch_enabled()` refused distributed mode when
this file was written; it is opt-in there now, and a deployment that has not opted in still
runs the serialised dispatch `FR-15` describes.

★★ **The durable representation was never missing.** `rehydrate_waiting_flow_runs` and
`rehydrate_waiting_agent_runs` rebuild exactly this on every boot, because a restart destroys
the live closure. `resume_reconstruction` gives that an entry point for *one* run instead of
only a whole sweep.

★ WHAT THESE TESTS ARE ACTUALLY FOR
------------------------------------
Not "does the function return something" — the risk here is subtler and it bit twice while
writing the module. Both builders read values off a row, and **a wrong field name still
compiles**:

- `getattr(run, "execution_unit_id", "")` looks right and is not: there is no such column on
  `FlowRun`. It always yields `""`, so the rebuilt resume skips the EU transition and
  half-executes.
- `(run.plan or {}).get("segments")` looks right and is not: segments come from
  `split_agent_plan()`, and reading the plan directly produces a different decomposition than
  the one the run was suspended against.

Both failures are swallowed by the module's broad `except` and surface as `None` — which is
also the legitimate answer for "not resumable". So a test that only asserts `is not None` on
a happy path proves almost nothing, and a test that only asserts `is None` on a sad path
passes just as well when the builder is broken outright.

**The tests below are therefore built around comparing the rebuild against the sweep** —
the path that already works in production — rather than against a hand-written expectation.
A field the sweep reads and the rebuild does not is a divergence, and divergence is the
actual failure mode: two representations of one operation is how they drifted in the first
place.
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.runtime_only


# ── 1. The invariant that makes a resume portable at all ─────────────────────


def test_flow_resume_callback_captures_no_live_objects():
    """★ THE LOAD-BEARING PROPERTY.

    A closure over a session, a runner or a request cannot cross a process boundary, and
    cannot outlive the request that made it. `_build_agent_resume_callback`'s docstring
    already states this ("captures only plain values, never a live DB session"); this asserts
    it of the flow half, which is the one that had a nested copy and a
    `lambda: self.resume(id)` on the live path.

    Checked against the signature rather than by introspecting the closure, because the
    signature is the contract: every parameter is a plain value read off the `FlowRun` row,
    so anything holding it can be reconstructed from `run_id` alone. A parameter that is not
    row-derived silently makes a run un-resumable anywhere but the process that registered
    it — which is the defect, not a style question.
    """
    from AINDY.core.flow_run_rehydration import build_flow_resume_callback

    params = inspect.signature(build_flow_resume_callback).parameters
    assert set(params) == {"r_id", "flow_name", "user_id", "workflow_type", "eid"}, (
        "build_flow_resume_callback's parameters changed. Every one must be readable off the "
        "FlowRun row (or the EU reached through it), or resume_reconstruction cannot rebuild "
        "the call and FR-15's distributed half reopens."
    )
    assert all(p.kind is p.KEYWORD_ONLY for p in params.values()), (
        "parameters must be keyword-only — a positional call is how the wrong row field ends "
        "up in the wrong slot without anything failing"
    )


def test_the_rehydration_sweep_uses_the_same_builder():
    """★ ONE representation, not two.

    The whole point of extracting the factory was to stop the sweep and the by-id rebuild
    being separate implementations of one operation. If the sweep stops calling it, they can
    drift again — and drift is invisible until a resume behaves differently depending on
    which path reached it.
    """
    from pathlib import Path

    src = Path("AINDY/core/flow_run_rehydration.py").read_text(encoding="utf-8")
    assert "resume_callback=build_flow_resume_callback(" in src, (
        "the rehydration sweep no longer calls build_flow_resume_callback — it has grown a "
        "second implementation of the resume it shares with resume_reconstruction"
    )
    assert "def _make_resume_callback(" not in src, (
        "the nested factory is back; the sweep and the by-id rebuild are two implementations "
        "again"
    )


# ── 2. Divergence between the rebuild and the sweep ──────────────────────────


def _register(monkeypatch, *names):
    """Put `names` in this process's FLOW_REGISTRY for the duration of a test.

    Needed since the rebuild refuses a flow this process does not hold. That refusal is the
    point (see `test_a_flow_absent_from_this_processes_registry_is_refused`), and it means a
    test asserting a successful rebuild has to say which flow it is asserting about — the
    unregistered case is now a different, deliberately-failing path.
    """
    monkeypatch.setattr(
        "AINDY.runtime.flow_engine.FLOW_REGISTRY",
        {n: object() for n in names},
        raising=False,
    )


def _captured(callback) -> dict:
    """The values a resume closure carries, read back off the closure itself.

    Behavioural rather than textual. An earlier version of the test below asserted on the
    module's source and failed against the explanatory comment that *quotes* the bad
    pattern — a source-text assertion cannot tell code from prose, which is `ROUTE-GUARD-1`
    in miniature. What the closure actually captured is the thing that matters anyway.
    """
    names = callback.__code__.co_freevars
    return dict(zip(names, (c.cell_contents for c in (callback.__closure__ or ()))))


def test_rebuild_resolves_the_execution_unit_the_way_the_sweep_does(db_session, monkeypatch):
    """★ THE TEST THAT CATCHES A WRONG FIELD NAME, and it caught mine.

    `getattr(run, "execution_unit_id", "")` compiles, always returns `""`, and produces a
    resume that silently skips the EU transition — nothing about the call fails, and the
    module's broad `except` turns any read error into `None`, which is also the legitimate
    answer for "not resumable". So the only assertion with teeth is on the value the rebuilt
    closure actually carries.

    The sweep reaches the EU through `ExecutionUnit.flow_run_id`, because there is no
    `execution_unit_id` column on `FlowRun`. If the rebuild diverges, `eid` comes back empty
    here while everything else still looks correct.
    """
    from AINDY.core.resume_reconstruction import build_resume_callback
    from AINDY.db.models.execution_unit import ExecutionUnit
    from AINDY.db.models.flow_run import FlowRun

    _register(monkeypatch, "demo_flow")
    run = FlowRun(flow_name="demo_flow", status="waiting", workflow_type="flow")
    db_session.add(run)
    db_session.flush()

    eu = ExecutionUnit(type="flow", flow_run_id=str(run.id))
    db_session.add(eu)
    db_session.flush()

    callback = build_resume_callback(run_id=str(run.id), eu_type="flow", db=db_session)
    assert callback is not None, "a waiting flow run with an EU must be reconstructible"

    captured = _captured(callback)
    assert captured["eid"] == str(eu.id), (
        f"the rebuilt resume carries eid={captured['eid']!r}, expected {str(eu.id)!r}. An "
        f"empty value means the EU was resolved off a FlowRun column that does not exist — "
        f"the resume would run but skip its execution-unit transition."
    )
    assert captured["r_id"] == str(run.id)
    assert captured["flow_name"] == "demo_flow"


def test_a_flow_run_with_no_execution_unit_is_still_reconstructible(db_session, monkeypatch):
    """★ The liveness control for the test above.

    Without it, a rebuild that returned `None` whenever it could not find an EU would make
    the eid assertion unreachable rather than failing it. The sweep treats a missing EU as
    fine — "EU context is optional" — so an empty `eid` is a legitimate value here and only
    a *wrong* one when an EU exists.
    """
    from AINDY.core.resume_reconstruction import build_resume_callback
    from AINDY.db.models.flow_run import FlowRun

    _register(monkeypatch, "orphan_flow")
    run = FlowRun(flow_name="orphan_flow", status="waiting", workflow_type="flow")
    db_session.add(run)
    db_session.flush()

    callback = build_resume_callback(run_id=str(run.id), eu_type="flow", db=db_session)
    assert callback is not None, (
        "a waiting flow run with no ExecutionUnit must still be reconstructible — the sweep "
        "treats EU context as optional and the rebuild must agree"
    )
    assert _captured(callback)["eid"] == ""


def test_the_rebuilt_closure_carries_no_live_objects(db_session, monkeypatch):
    """★ THE PORTABILITY INVARIANT, asserted on the actual closure.

    This is what lets a resume cross a process boundary at all. A captured `Session`,
    `FlowRunner` or request would make the closure meaningless anywhere but here — and it
    would still *look* fine in-process, which is how `FR-15`'s distributed half stayed
    invisible.
    """
    from AINDY.core.resume_reconstruction import build_resume_callback
    from AINDY.db.models.flow_run import FlowRun

    _register(monkeypatch, "portable_flow")
    run = FlowRun(flow_name="portable_flow", status="waiting", workflow_type="flow")
    db_session.add(run)
    db_session.flush()

    callback = build_resume_callback(run_id=str(run.id), eu_type="flow", db=db_session)
    assert callback is not None

    for name, value in _captured(callback).items():
        assert value is None or isinstance(value, (str, int, float, bool)), (
            f"the resume closure captured {name}={type(value).__name__}. Only plain values "
            f"may be carried — anything else cannot be reconstructed elsewhere, which is the "
            f"defect FR-15's remaining half is about."
        )


def test_rebuild_splits_the_agent_plan_rather_than_reading_it():
    """The agent-side equivalent, and the second wrong-field bug from writing this module.

    `split_agent_plan()` is what defines a segment boundary. Reading `plan["segments"]`
    directly produces a different decomposition than the one the run was suspended against,
    so `resume_segment_index` would index into the wrong list — resuming at the wrong point
    or refusing a run that is perfectly resumable.
    """
    from pathlib import Path

    rebuild = Path("AINDY/core/resume_reconstruction.py").read_text(encoding="utf-8")
    assert "split_agent_plan(run.plan or {})" in rebuild, (
        "the agent rebuild is not using split_agent_plan; a raw plan read gives a different "
        "segmentation than the one resume_segment_index refers to"
    )
    assert '.get("segments")' not in rebuild, (
        "the agent rebuild reads plan['segments'] directly — see above"
    )


def test_a_flow_absent_from_this_processes_registry_is_refused(db_session, monkeypatch):
    """★★ THE FOURTH SILENT LOSS, and the one that blocked the distributed flip.

    The built callback checks `FLOW_REGISTRY` itself and, finding nothing, logs a warning and
    **returns normally**. That is right for the rehydration sweep — a flow that is not ours is
    legitimately skipped — and catastrophic for a caller that has already discarded the
    closure: it sees a clean return, ACKs the message, and reports the resume completed while
    the run sits `waiting` forever.

    It is not hypothetical across a process boundary. `register_flow()` only COLLECTS
    registrations; `register_flows()` invokes them, and only that fills `FLOW_REGISTRY`. The
    worker called the first and not the second, so its registry was empty — every flow resume
    would have been acknowledged and lost.

    Refusing here turns it into `ResumeNotReconstructible`, so the message is dead-lettered
    and visible.
    """
    from AINDY.core.resume_reconstruction import build_resume_callback
    from AINDY.db.models.flow_run import FlowRun
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    run = FlowRun(flow_name="a_flow_this_process_does_not_have", status="waiting")
    db_session.add(run)
    db_session.flush()

    monkeypatch.setattr(
        "AINDY.runtime.flow_engine.FLOW_REGISTRY", {}, raising=False
    )
    assert "a_flow_this_process_does_not_have" not in FLOW_REGISTRY

    assert build_resume_callback(run_id=str(run.id), eu_type="flow", db=db_session) is None, (
        "a flow absent from this process's registry must be refused, not handed back as a "
        "callable that returns silently — the caller would ACK it as completed work"
    )


def test_a_registered_flow_is_still_reconstructible(db_session, monkeypatch):
    """★ Liveness control for the refusal above.

    Without it, a rebuild that refused *everything* would satisfy the test above while
    breaking every resume — the refusal has to be specific to an absent flow.
    """
    from AINDY.core.resume_reconstruction import build_resume_callback
    from AINDY.db.models.flow_run import FlowRun

    run = FlowRun(flow_name="a_flow_this_process_has", status="waiting")
    db_session.add(run)
    db_session.flush()

    monkeypatch.setattr(
        "AINDY.runtime.flow_engine.FLOW_REGISTRY",
        {"a_flow_this_process_has": object()},
        raising=False,
    )

    assert build_resume_callback(run_id=str(run.id), eu_type="flow", db=db_session) is not None


def test_the_worker_entrypoint_invokes_flow_registration():
    """★ The other half, and the reason the refusal alone is not enough.

    Refusing an unregistered flow makes the loss visible; it does not make the resume work.
    The worker has to actually hold the flows, which means calling `register_flows()` — which
    INVOKES the collected registrations — and not only `load_plugins()`, which merely collects
    them.

    ★ Asserted over the AST, not the source text. The first version of this test matched the
    string `"register_flows()"` and a mutation that commented the call out left it green — the
    fourth time in this work that a source-text assertion proved weaker than it looked. A
    parsed call node cannot be satisfied by a comment.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("AINDY/worker/__main__.py").read_text(encoding="utf-8"))
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "register_flows" in called, (
        "the worker entrypoint does not CALL register_flows(). load_plugins() only collects "
        "flow registrations — without the invoke its FLOW_REGISTRY is empty, and every flow "
        "resume routed to a worker is dead-lettered as unreconstructible."
    )


# ── 3. The answer vs the failure ─────────────────────────────────────────────


def test_unknown_eu_type_returns_none_rather_than_raising(db_session):
    """`task` and unknown types have no resume semantics. That is an answer, not an error."""
    from AINDY.core.resume_reconstruction import build_resume_callback

    assert build_resume_callback(run_id="whatever", eu_type="task", db=db_session) is None
    assert build_resume_callback(run_id="whatever", eu_type="", db=db_session) is None


def test_missing_run_returns_none(db_session):
    """A run that does not exist is not resumable, and not a crash."""
    import uuid

    from AINDY.core.resume_reconstruction import build_resume_callback

    missing = str(uuid.uuid4())
    assert build_resume_callback(run_id=missing, eu_type="flow", db=db_session) is None
    assert build_resume_callback(run_id=missing, eu_type="agent", db=db_session) is None


def test_require_raises_where_none_cannot_be_handled(db_session):
    """★ The distinction the two entry points exist for.

    A transport that has already discarded the closure cannot do anything sensible with
    `None`: the work is neither carried nor rebuildable. Enqueueing it anyway is precisely
    the silent loss — the worker acks an unresolvable job as success. So the seam that has no
    fallback gets a raising variant, and the error says why rather than naming a missing key.
    """
    import uuid

    from AINDY.core.resume_reconstruction import (
        ResumeNotReconstructible,
        require_resume_callback,
    )

    with pytest.raises(ResumeNotReconstructible) as caught:
        require_resume_callback(run_id=str(uuid.uuid4()), eu_type="flow", db=db_session)

    message = str(caught.value)
    assert "unresolvable" in message, "the error must say what goes wrong downstream"
    assert "acknowledged as" in message or "successfully completed" in message, (
        "the error must name the silent-loss failure mode, not just report a missing run"
    )


def test_reconstructible_types_are_declared_not_inferred():
    """A transport checks this set *before* discarding the closure.

    It has to be readable without calling anything — by the time a builder returns `None` the
    caller may already have given up the work it was carrying.
    """
    from AINDY.core.resume_reconstruction import _BUILDERS, RECONSTRUCTIBLE_EU_TYPES

    assert RECONSTRUCTIBLE_EU_TYPES == frozenset(_BUILDERS), (
        "the declared set and the builder registry disagree — a transport consulting the "
        "declaration would route work that cannot in fact be rebuilt"
    )
