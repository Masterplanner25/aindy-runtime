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
"""
from __future__ import annotations

from pathlib import Path

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
