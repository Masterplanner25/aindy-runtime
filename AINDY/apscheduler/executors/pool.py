"""Minimal ThreadPoolExecutor shim matching APScheduler's constructor surface.

Added for FR-15 (b). Without it, `from apscheduler.executors.pool import ThreadPoolExecutor`
raised under pytest — `pytest.ini` sets `pythonpath = . AINDY`, so `import apscheduler`
resolves to this shim rather than the installed package. The scheduler service catches that
ImportError and falls back to `executors=None`, which meant **the dedicated-executor code
path was never exercised by any test** while still shipping to production.

That is the "collected, but the branch under test is skipped" shape this repo tracks, so the
shim grows the surface rather than the guard being weakened to match it.
"""


class _Pool:
    def __init__(self, max_workers: int) -> None:
        self._max_workers = max_workers


class ThreadPoolExecutor:
    def __init__(self, max_workers: int = 10, pool_kwargs=None) -> None:
        self.max_workers = max_workers
        self.pool_kwargs = dict(pool_kwargs or {})
        # Mirrors the real executor's attribute path so a test can assert pool sizing
        # identically against the shim and the installed package.
        self._pool = _Pool(max_workers)
