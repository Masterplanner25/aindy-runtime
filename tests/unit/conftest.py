"""
tests/unit/conftest.py
──────────────────────
Makes the *default* safe for unit tests (CI-MARKER-1).

`Runtime Contracts` — the only CI job that runs unit tests — invokes
``pytest tests -m runtime_only``. The marker is applied per-file as
``pytestmark = pytest.mark.runtime_only`` and nothing applied it automatically,
so a new file under ``tests/unit/`` defaulted to **not running in any CI job**
and the pull request looked no different for it. That is how 268 tests across
24 files — including the FR-8/9/10 regressions that forced the 2.0.1 release —
ended up unrunnable while CI stayed green.

The hook below closes the default: every item collected from this directory
gets ``runtime_only`` unless it already carries it, or carries a marker that
hands it to a different job. Explicit ``pytestmark`` lines stay in the files
(they are the greppable convention, and they keep working if this hook is ever
removed); this is the belt to their braces.

Opting a unit test *out* of the runtime job is still possible — mark it
``integration``/``redis``/``mongo``/``multi_instance``/``sandbox_escape`` — but
now it takes a deliberate marker, not an omission.

Coverage of the hook itself is in ``tests/unit/test_ci_marker_default.py``,
which spawns a real pytest subprocess against a generated unmarked file. A test
that only inspected this module would pass even if pytest never loaded the hook.

ContextVar isolation (TEST-ORDER-CONTEXTVAR-1)
──────────────────────────────────────────────
pytest runs every test in ONE ``contextvars`` context, so a test that ``.set()``s
a ContextVar and never resets it changes the ambient state of every test that
runs after it. Measured 2026-09-13: one file left ``pipeline_active=True``, a
trace id and a syscall ``_EU_ID_CTX`` set, so ~180 of 221 unit files ran with the
ExecutionContract gate vacuously satisfied — and the first symptom was a test
in a different file that failed only in CI's full order.

``_contextvar_isolation`` below snapshots every ContextVar the runtime has
imported, and after each test **resets what changed and fails that test**. The
reset keeps victims clean; the failure lands on the leaker, not on whichever
test 100 files later happened to notice. The census is DERIVED from loaded
``AINDY.*`` modules, never typed out (variant 12), and asserted non-empty.
Its liveness control is ``tests/unit/test_contextvar_isolation_guard.py``.
"""
from __future__ import annotations

import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import pytest

UNIT_DIR = Path(__file__).parent.resolve()

#: Markers that route a test to a CI job other than ``Runtime Contracts``.
#: Carrying one of these is a deliberate opt-out from the runtime unit job.
FOREIGN_JOB_MARKERS = (
    "integration",
    "sandbox_escape",
    "redis",
    "mongo",
    "multi_instance",
)


def pytest_collection_modifyitems(config, items):
    """Default every ``tests/unit`` item into the ``runtime_only`` job.

    This hook receives the whole session's items, not just this directory's,
    so it filters by path before adding anything.
    """
    for item in items:
        try:
            item_path = Path(str(item.path)).resolve()
        except (AttributeError, OSError):
            continue
        if UNIT_DIR not in item_path.parents:
            continue
        if item.get_closest_marker("runtime_only"):
            continue
        if any(item.get_closest_marker(name) for name in FOREIGN_JOB_MARKERS):
            continue
        item.add_marker(pytest.mark.runtime_only)


# ── ContextVar isolation (TEST-ORDER-CONTEXTVAR-1) ───────────────────────────

_UNSET = object()
_census_cache: dict[str, Any] = {"module_count": -1, "vars": ()}


def _runtime_contextvars() -> tuple[tuple[str, ContextVar], ...]:
    """Every ContextVar defined by a currently-imported ``AINDY.*`` module.

    Recomputed only when the set of imported AINDY modules grows, so the per-test
    cost is a dict-size comparison. A ContextVar first imported DURING a test is
    not in that test's snapshot and is compared from the next test on.
    """
    modules = [(name, mod) for name, mod in list(sys.modules.items())
               if name == "AINDY" or name.startswith("AINDY.")]
    if len(modules) == _census_cache["module_count"]:
        return _census_cache["vars"]
    found: dict[int, tuple[str, ContextVar]] = {}
    for name, mod in modules:
        for attr, value in list(vars(mod).items()):
            if isinstance(value, ContextVar) and id(value) not in found:
                found[id(value)] = (f"{name}.{attr}", value)
    census = tuple(sorted(found.values(), key=lambda pair: pair[0]))
    _census_cache["module_count"] = len(modules)
    _census_cache["vars"] = census
    return census


def _read(var: ContextVar) -> Any:
    try:
        return var.get()
    except LookupError:
        return _UNSET


@pytest.fixture(autouse=True)
def _contextvar_isolation(request):
    """Fail a test that leaves a runtime ContextVar changed, after restoring it."""
    census = _runtime_contextvars()
    before = {label: _read(var) for label, var in census}
    yield
    census = _runtime_contextvars()
    leaked = []
    for label, var in census:
        if label not in before:
            continue
        prior, now = before[label], _read(var)
        if now is prior or now == prior:
            continue
        # Restore so the NEXT test starts clean; the failure below is attributed here.
        # A var with no default that was unset cannot be un-set without the leaker's
        # token — say so rather than pretend (every runtime ContextVar has a default today).
        if prior is _UNSET:
            leaked.append(f"{label}: <unset> -> {now!r} (NOT restorable without the token)")
        else:
            var.set(prior)
            leaked.append(f"{label}: {prior!r} -> {now!r}")
    if leaked:
        pytest.fail(
            "TEST-ORDER-CONTEXTVAR-1: this test changed a runtime ContextVar and did not "
            "reset it, so every later test would have inherited the value. Keep the token "
            "from .set() and .reset() it (or use the paired set_*/reset_* helpers).\n  "
            + "\n  ".join(leaked)
        )
