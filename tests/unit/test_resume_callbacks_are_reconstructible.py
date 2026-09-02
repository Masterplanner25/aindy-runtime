"""Every registered resume callback must be built from identifiers, not captured state.

`FR-15` stage B. A resume callback is handed to the `SchedulerEngine` and fires later — on a
scheduler thread, after the request that registered it has returned, possibly in a different
process. Anything it closes over has to still be meaningful at that point.

Two sites did not satisfy that:

- `runner_steps.py` registered `lambda: self.resume(run_id)`, capturing the `FlowRunner` and
  therefore `self.db`.
- `execution_pipeline/waits.py` registered
  `lambda: ExecutionUnitService(db).resume_execution_unit(eu_id)`, capturing the
  **request-scoped** session directly.

Both are `AGENT_WORKING_RULES` §5 — never share a SQLAlchemy session across threads or requests
— and both survived because a closed SQLAlchemy session is not a dead one: it transparently
checks out a new connection on next use, so the violation was latent rather than visible. That
is exactly the kind that stops being latent under concurrency, and `FR-15 (a)` made scheduler
resumes concurrent.

★ THE GUARD IS OVER THE AST, DELIBERATELY
------------------------------------------
A string search for `lambda: self.resume` would be satisfied by a comment, and would miss any
other capturing form. Four source-text assertions in this work have already proved weaker than
they looked, which is now a standing rule in `CLAUDE.md`. A parsed argument node cannot be
satisfied by prose, and it catches *any* lambda rather than the two spellings someone happened
to think of.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

RUNTIME = Path("AINDY")


def _resume_callback_arguments() -> list[tuple[str, int, ast.AST]]:
    """Every `resume_callback=<expr>` passed anywhere under `AINDY/`, as parsed nodes."""
    found: list[tuple[str, int, ast.AST]] = []
    for path in RUNTIME.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - not our problem here
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "resume_callback":
                    found.append((str(path), kw.value.lineno, kw.value))
    return found


def test_the_scan_finds_the_registration_sites():
    """★ Liveness control for the guard below.

    Every assertion here is of the form "no site does X". A scan that found *nothing* — a moved
    file, a renamed keyword, a parse failure swallowed above — would satisfy that vacuously and
    keep doing so forever. This is the check that the instrument is pointed at something.
    """
    sites = _resume_callback_arguments()
    assert len(sites) >= 6, (
        f"only {len(sites)} resume_callback registrations found under AINDY/; the scan is not "
        f"seeing the wait-registration sites and every assertion in this file is vacuous"
    )


def test_no_resume_callback_is_a_capturing_lambda():
    """★★ THE PROPERTY. A lambda argument here is, in practice, a captured closure.

    Not a style rule. The two that existed captured a DB session and handed it to a scheduler
    thread; a resume registered in one process must remain meaningful in another, and a lambda
    written inline at a registration site cannot be — it closes over whatever is in scope, which
    is precisely the request-bound state that will not survive.

    The builders it replaced take identifiers and open their own session, which is what makes a
    resume portable at all (`resume_reconstruction`).
    """
    offenders = [
        f"{path}:{lineno}"
        for path, lineno, value in _resume_callback_arguments()
        if isinstance(value, ast.Lambda)
    ]
    assert not offenders, (
        "resume_callback is being passed an inline lambda at: "
        + ", ".join(offenders)
        + ". A lambda at a registration site closes over request-bound state — a DB session, a "
        "runner, a request — and the callback fires later on a scheduler thread, possibly in "
        "another process. Build it from identifiers instead (see "
        "AINDY/core/resume_reconstruction.py)."
    )


def test_the_execution_unit_resume_captures_only_an_identifier():
    """The replacement for the worst of the two, checked on the closure it actually produces.

    `lambda: ExecutionUnitService(db).resume_execution_unit(eu_id)` captured the request's own
    session. This one captures a string and opens its own.
    """
    from AINDY.core.execution_pipeline.waits import _build_eu_resume_callback

    callback = _build_eu_resume_callback("eu-123")
    captured = dict(
        zip(
            callback.__code__.co_freevars,
            (cell.cell_contents for cell in (callback.__closure__ or ())),
        )
    )

    assert captured == {"eu_id": "eu-123"}, (
        f"the EU resume closure captured {captured!r}. Only plain identifiers may be carried — "
        f"anything else ties the callback to the request or process that registered it."
    )


def test_the_flow_runner_registers_a_reconstructed_callback():
    """The other two sites, checked structurally rather than by driving a whole flow to a wait.

    Driving a real flow into `resource_available` and then into an event wait would be a large
    integration fixture for a property that is decided entirely at the call site. What matters
    is that the argument is the shared builder, so the live path and the rehydration sweep
    produce the *same* callback — one implementation, which is the point of stage 1.
    """
    tree = ast.parse(
        Path("AINDY/runtime/flow_engine/runner_steps.py").read_text(encoding="utf-8")
    )
    builders = [
        value.func.id
        for _, _, value in [
            (None, None, kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "resume_callback"
        ]
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
    ]
    assert builders == ["build_flow_resume_callback"] * 2, (
        f"runner_steps.py registers {builders!r}. Both wait registrations must use the shared "
        f"builder, so the live path and the rehydration sweep cannot drift into two different "
        f"resumes for the same run."
    )
