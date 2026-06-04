"""Pin the ResourceManager quota defaults so they cannot silently drift.

cpu_time_ms measures wall-clock elapsed time (monotonic clock), including
all network I/O wait.  The 300 000 ms default accommodates real agent steps
that include embedding API round-trips (~34 s observed; see trace 4cc32073).
"""
from __future__ import annotations

import importlib
import os

import pytest

pytestmark = pytest.mark.runtime_only


def _reload_resource_manager(env: dict[str, str] | None = None):
    """Re-import resource_manager with a clean env so module-level constants reset."""
    saved = {k: os.environ.pop(k, None) for k in ["AINDY_QUOTA_CPU_MS", "AINDY_QUOTA_MEMORY_BYTES",
                                                    "AINDY_QUOTA_MAX_SYSCALLS", "AINDY_QUOTA_MAX_CONCURRENT"]}
    if env:
        os.environ.update(env)
    try:
        import AINDY.kernel.resource_manager as rm_mod
        importlib.reload(rm_mod)
        return rm_mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if env:
            for k in env:
                os.environ.pop(k, None)


def test_default_cpu_time_ms_is_300s():
    """Default AINDY_QUOTA_CPU_MS must be 300 000 ms (5-minute wall-clock limit)."""
    rm = _reload_resource_manager()
    assert rm.MAX_CPU_TIME_MS == 300_000, (
        f"Default cpu_time_ms cap changed unexpectedly: got {rm.MAX_CPU_TIME_MS}. "
        "This measures wall-clock time (including I/O wait). "
        "See AGENT-RESLIMIT-001 before lowering this value."
    )


def test_cpu_time_ms_env_override():
    """AINDY_QUOTA_CPU_MS must override the default."""
    rm = _reload_resource_manager({"AINDY_QUOTA_CPU_MS": "60000"})
    assert rm.MAX_CPU_TIME_MS == 60_000


def test_other_quota_defaults_unchanged():
    """Non-cpu quota defaults — verify they haven't silently changed."""
    rm = _reload_resource_manager()
    assert rm.MAX_MEMORY_BYTES == 256 * 1024 * 1024
    assert rm.MAX_SYSCALLS_PER_EXECUTION == 100
    assert rm.MAX_CONCURRENT_PER_TENANT == 5
