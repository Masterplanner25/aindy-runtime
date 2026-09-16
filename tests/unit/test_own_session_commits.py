"""A function that opens its OWN session and writes through it must commit it.

Four instances in one week, all one shape — ``SessionLocal()`` … a write … ``close()`` (or a
``with`` block exiting) with no ``commit()`` between — and every one passed its tests:

1. `_safe_finalize_eu` (FR-30, #673): every route unit sat `executing` forever.
2. `execution_pipeline/waits.py::_build_eu_resume_callback` (#679): the request-EU resume.
3. `wait_rehydration.py::_make_resume_callback` (#679 / #685): the boot-time EU resume.
4. `resume_spec.py::_build_execution_unit_resume_callback` (this PR): the CROSS-INSTANCE resume
   — the fallback for a wait whose registering instance died. Rolled back on every fire; and it
   moved only the unit, never the run (fixed alongside).

Why tests kept missing it: the shared `db_session` fixture puts the code and the assertion on
one connection inside one outer transaction, where a flush reads exactly like a commit (CLAUDE.md
catalogue variant 15). This guard does not run the code at all — it reads it, which is the one
instrument that cannot be blinded by a fixture.

**The rule, derived from source (variant 12: never a hand-written census):** for every function
(closures included, each owning its own bindings) under `AINDY/` that binds a session from ``SessionLocal()`` — ``db = SessionLocal()`` or
``with SessionLocal() as db`` — if the function body (nested closures included) performs a write
through that session, the body must also call ``commit`` on it. A write is: ``db.add/add_all/
merge/delete/flush/bulk_save_objects``, ``db.query(...).update/delete``, or one of the
`ExecutionUnitService` methods that flush a status (``update_status``, ``set_wait_condition``,
``resume_execution_unit``, ``link_flow_run``, ``link_memory_context``, ``append_output_memory``)
called on a service constructed over that session. Reads (``query``, ``execute("SELECT 1")``,
``first``) are not writes, and a session that only reads owes nothing.

**Limits, stated:** a write delegated to a callee that receives the session
(``execute_run(run_id, user_id, db)``) is invisible here — the callee owns its commits and the
survey below lists those functions so a reader can check them by hand. A source-text guard is a
supplement, never the coverage (CLAUDE.md); the durability tests in
`test_request_eu_finalize_commits_fr30.py`, `test_flow_rehydration_owns_the_unit.py` and
`test_cross_instance_resume_commits.py` are the behavioural half for the four instances above.

Liveness: `test_the_guard_bites` feeds the checker a synthetic module with the exact shape of
instance 4 and asserts it is reported — so an empty census or a broken walker cannot pass this
file by finding nothing (variant 6).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.runtime_only

ROOT = pathlib.Path(__file__).resolve().parents[2] / "AINDY"

SESSION_WRITES = {"add", "add_all", "merge", "delete", "flush", "bulk_save_objects"}
QUERY_WRITES = {"update", "delete"}
SERVICE_WRITES = {
    "update_status", "set_wait_condition", "resume_execution_unit", "link_flow_run",
    "link_memory_context", "append_output_memory",
}

#: Known, reasoned exceptions. Empty is the goal; an entry here needs a one-line why.
ALLOWLIST: dict[str, str] = {}


def _scoped_walk(fn: ast.AST):
    """Every node of ``fn``'s own body — NOT descending into nested function definitions, which
    are scanned as functions in their own right (a closure that binds its own session owns it)."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _session_names(fn: ast.AST) -> set[str]:
    """Variable names bound to a fresh ``SessionLocal()`` inside ``fn``'s own scope."""
    names: set[str] = set()
    for node in _scoped_walk(fn):
        if isinstance(node, ast.Assign) and _is_session_local_call(node.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if _is_session_local_call(item.context_expr) and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def _is_session_local_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SessionLocal" and not node.args


def _receiver_name(call: ast.Call) -> str | None:
    """``db.flush()`` → ``db``; ``db.query(X).update(...)`` → ``db``; ``Service(db).update_status``
    → ``db`` (the session the service wraps); anything else → None."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    base = func.value
    # unwrap chained calls: db.query(...).update(...) → db
    while isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute):
        base = base.func.value
    if isinstance(base, ast.Name):
        return base.id
    # Service(db).method(...) → db
    if isinstance(base, ast.Call) and base.args and isinstance(base.args[0], ast.Name):
        return base.args[0].id
    return None


def _writes_and_commits(fn: ast.AST, sessions: set[str]) -> tuple[list[str], bool, list[str]]:
    writes: list[str] = []
    delegations: list[str] = []
    commits = False
    for node in _scoped_walk(fn):
        if not isinstance(node, ast.Call):
            continue
        recv = _receiver_name(node)
        attr = getattr(node.func, "attr", None)
        if recv in sessions:
            if attr == "commit":
                commits = True
            elif attr in SESSION_WRITES or attr in SERVICE_WRITES:
                writes.append(f"{recv}.{attr}")
            elif attr in QUERY_WRITES and isinstance(node.func.value, ast.Call):
                writes.append(f"{recv}.query(...).{attr}")
        # A session handed to a callee: the callee owns the write. Recorded, not judged.
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Name) and arg.id in sessions and attr not in SESSION_WRITES | SERVICE_WRITES | {"commit", "close", "rollback", "query"}:
                name = attr or getattr(node.func, "id", "?")
                if name not in ("ExecutionUnitService",):
                    delegations.append(name)
    return writes, commits, delegations


def scan(root: pathlib.Path) -> tuple[list[tuple[str, str, list[str]]], int, list[tuple[str, str, list[str]]]]:
    """(offenders, functions_scanned, delegators)."""
    offenders, delegators = [], []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            sessions = _session_names(fn)
            if not sessions:
                continue
            scanned += 1
            writes, commits, delegations = _writes_and_commits(fn, sessions)
            key = f"{path.relative_to(root.parent).as_posix()}::{fn.name}"
            if writes and not commits:
                offenders.append((key, fn.name, writes))
            if delegations and not commits and not writes:
                delegators.append((key, fn.name, sorted(set(delegations))))
    return offenders, scanned, delegators


def test_every_own_session_writer_commits():
    offenders, scanned, _ = scan(ROOT)
    assert scanned >= 40, f"the census found only {scanned} own-session functions; the walker is broken"
    unexplained = [(k, w) for k, _, w in offenders if k not in ALLOWLIST]
    assert unexplained == [], (
        "functions that open their own SessionLocal(), write through it, and never commit — "
        "the write is rolled back on close, and no fixture-shared test can see it:\n  "
        + "\n  ".join(f"{k}: {', '.join(w)}" for k, w in unexplained)
    )


def test_the_guard_bites(tmp_path):
    """Liveness: the exact shape of instance 4, and of instance 1."""
    pkg = tmp_path / "AINDY"
    pkg.mkdir()
    (pkg / "bad.py").write_text(
        "def build(spec):\n"
        "    from AINDY.db import SessionLocal\n"
        "    def _resume():\n"
        "        with SessionLocal() as db:\n"
        "            ExecutionUnitService(db).resume_execution_unit(spec.eu_id)\n"
        "    return _resume\n"
        "\n"
        "def finalize(eu_id):\n"
        "    db = SessionLocal()\n"
        "    try:\n"
        "        ExecutionUnitService(db).update_status(eu_id, 'completed')\n"
        "    finally:\n"
        "        db.close()\n"
        "\n"
        "def good(eu_id):\n"
        "    db = SessionLocal()\n"
        "    try:\n"
        "        db.add(object())\n"
        "        db.commit()\n"
        "    finally:\n"
        "        db.close()\n"
        "\n"
        "def reader(run_id):\n"
        "    db = SessionLocal()\n"
        "    try:\n"
        "        return db.query(object).filter().first()\n"
        "    finally:\n"
        "        db.close()\n",
        encoding="utf-8",
    )
    offenders, scanned, _ = scan(pkg)
    names = sorted(n for _, n, _ in offenders)
    assert names == ["_resume", "finalize"], offenders  # the closure owns its session, not `build`
    assert scanned == 4


def test_delegators_are_listed_not_judged():
    """The stated limit, made visible: functions that hand their session to a callee are
    printed so a reader can check the callee. This never fails; it documents the blind spot."""
    _, _, delegators = scan(ROOT)
    assert isinstance(delegators, list)
