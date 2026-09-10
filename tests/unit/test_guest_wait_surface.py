"""GUEST-BUILTINS-DEAD-1 — what a guest can and cannot do to request a WAIT.

This pins a **finding**, not a design. `AINDY/runtime/nodus_builtins.py` (530 lines) has zero
importers under ``AINDY/``, so the guest ``event`` and ``memory`` namespaces it defines — and
``NodusWaitSignal`` — are unreachable from every execution path. The wait capability itself is
live, but the only way to reach it is an undocumented state key.

★ **Why pin something that is arguably broken?** Because the gap is invisible: nothing is failing,
waits work (they are segmented at the host level by ``agent_plan_compiler``), and the dead module
looks maintained — `MEM-NODETYPE-1` "fixed a bug" in it three months ago. If someone later wires
``event.wait()`` into the worker, or removes the magic-key path, these tests say so out loud
instead of letting the surface drift again.

★ **These assertions are deliberately two-sided.** Asserting only that ``event`` is missing would
pass just as well on a worker that cannot run anything at all, and asserting only that the state
key works would not notice ``event.wait()`` quietly starting to work. Both directions are checked,
with a liveness control first.

The decision about which of these *should* be true belongs to `WAIT-TYPED-CONTRACT-1`. When it is
taken, this file is the thing to update.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")

from AINDY.runtime import nodus_worker  # noqa: E402


def _run(script: str) -> dict:
    return nodus_worker.run_one(
        {"script": script, "state": {}, "context": {"user_id": "wait-surface-test"}}
    )


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_the_worker_runs_a_benign_script():
    """★ Without this, 'event is undefined' passes on a totally broken worker."""
    result = _run('set_state("x", 41 + 1)\n')
    assert result["status"] == "success", result
    assert result["output_state"].get("x") == 42


# ── The documented API does not exist on the execution path ──────────────────


def test_the_documented_event_wait_api_is_unreachable():
    """``NodusEventBuiltins.wait``'s own docstring carries this as its worked example.

    It fails, because the worker never imports ``nodus_builtins`` and injects no ``event``.
    """
    result = _run('let p = event.wait("approval.received")\n')

    assert result["status"] == "failure"
    assert "Undefined variable: event" in str(result.get("error")), result.get("error")


def test_nodus_builtins_has_no_importer_in_the_runtime():
    """The root cause, asserted directly rather than via its symptom.

    Walks the AST of every runtime module rather than grepping: a comment or a docstring
    mentioning ``nodus_builtins`` must not satisfy this, but a real import must fail it.
    """
    import ast
    from pathlib import Path

    aindy_root = Path(nodus_worker.__file__).resolve().parents[1]
    importers = []
    for path in aindy_root.rglob("*.py"):
        if path.name == "nodus_builtins.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any("nodus_builtins" in a.name for a in node.names):
                    importers.append(str(path))
            elif isinstance(node, ast.ImportFrom):
                # ★ BOTH halves. `from AINDY.runtime import nodus_builtins` puts the module
                #   name in `node.names`, and `node.module` is only "AINDY.runtime" — checking
                #   `node.module` alone missed the single most likely import form. Found by
                #   mutation testing, which is the whole reason to run it on an AST check.
                if node.module and "nodus_builtins" in node.module:
                    importers.append(str(path))
                elif any("nodus_builtins" in a.name for a in node.names):
                    importers.append(str(path))

    # A liveness control on the walk itself: an empty sweep would satisfy this vacuously.
    assert len(list(aindy_root.rglob("*.py"))) > 100, "the module sweep found almost nothing"

    assert importers == [], (
        "nodus_builtins now HAS an importer, so GUEST-BUILTINS-DEAD-1 has changed: "
        + ", ".join(importers)
    )


# ── The capability is live, by an undocumented route ─────────────────────────


def test_the_wait_is_reachable_only_by_an_undocumented_state_key():
    """★ The uncomfortable half: the unsupported-looking route is the one that works."""
    result = _run(
        'set_state("nodus_wait_requested", true)\n'
        'set_state("nodus_wait_event_type", "approval.received")\n'
    )

    assert result["status"] == "waiting", result
    assert result["wait_for"] == "approval.received"


def test_worker_wait_signal_is_defined_and_never_raised():
    """``WorkerWaitSignal`` is the vestigial twin of ``NodusWaitSignal``.

    Asserted through the AST so a comment or a docstring mentioning it cannot satisfy the check
    (``CLAUDE.md`` → *a source-text assertion is a supplement, never the coverage*).
    """
    import ast
    from pathlib import Path

    aindy_root = Path(nodus_worker.__file__).resolve().parents[1]
    raisers = []
    for path in aindy_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            exc = node.exc
            name = None
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                name = exc.func.id
            elif isinstance(exc, ast.Name):
                name = exc.id
            if name == "WorkerWaitSignal":
                raisers.append(f"{path}:{node.lineno}")

    assert raisers == [], (
        "WorkerWaitSignal is now raised somewhere, so its catch branch is no longer dead: "
        + ", ".join(raisers)
    )

    # And the handler it feeds still exists — otherwise the assertion above is vacuous.
    source = Path(nodus_worker.__file__).read_text(encoding="utf-8")
    assert "except WorkerWaitSignal" in source, "the catch branch is gone; update this entry"
