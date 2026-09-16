"""DEC-022 — ``sys.v1.agent.list_recent_durations`` is removed, and stays removed.

Its only caller was the runtime's own system-state snapshot, which dispatched it with no
tenant and never received an answer (``SYSTEM-STATE-TENANT-1``, #692). A re-registration is
a decision — this pins the absence so it cannot come back as a side effect. The floor is
pinned at its new value so a lowered floor with no removal beside it (the lost-registration
case the constant guards) is still caught by ``>=`` elsewhere.
"""

from __future__ import annotations

import pytest

from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, SYSCALL_REGISTRY_MIN_COUNT

pytestmark = pytest.mark.runtime_only


def test_list_recent_durations_is_not_registered():
    assert "sys.v1.agent.list_recent_durations" not in SYSCALL_REGISTRY
    assert not any(name.endswith("list_recent_durations") for name in SYSCALL_REGISTRY)


def test_count_runs_survives_the_removal():
    """The sibling has a consumer (the app's identity boot) and was deliberately kept."""
    assert "sys.v1.agent.count_runs" in SYSCALL_REGISTRY


def test_floor_matches_the_static_registry():
    assert SYSCALL_REGISTRY_MIN_COUNT == 23
    assert len(SYSCALL_REGISTRY) >= SYSCALL_REGISTRY_MIN_COUNT
