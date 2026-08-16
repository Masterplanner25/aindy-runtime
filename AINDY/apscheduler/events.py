"""Minimal APScheduler events shim (see the package docstring).

Added for SYSMAX-5. `pytest.ini` sets `pythonpath = . AINDY`, so `import apscheduler` resolves
to this shim rather than the installed package — and without an `events` module the starvation
listener's registration silently took its `except ImportError` path, meaning **the branch would
have shipped unexercised by any test**. That is the "collected, but the branch under test is
skipped" shape this repo tracks, so the shim grows to match the guard.

Codes mirror the real package's bit flags so a test can build a realistic event.
"""

EVENT_JOB_ADDED = 512
EVENT_JOB_REMOVED = 1024
EVENT_JOB_MODIFIED = 2048
EVENT_JOB_EXECUTED = 4096
EVENT_JOB_ERROR = 8192
EVENT_JOB_MISSED = 16384
EVENT_JOB_SUBMITTED = 32768
EVENT_JOB_MAX_INSTANCES = 65536


class JobExecutionEvent:
    """Just enough surface for a listener: a code and a job id."""

    def __init__(self, code: int, job_id: str | None = None) -> None:
        self.code = code
        self.job_id = job_id
