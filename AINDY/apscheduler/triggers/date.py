"""Minimal DateTrigger implementation (vendored stub for the test harness).

Mirrors the real APScheduler ``DateTrigger`` surface used by the runtime — a one-off
``run_date`` fire. The real trigger is used in production; this stub exists so the
top-level ``apscheduler`` name resolves under the test pythonpath shadow.
"""


class DateTrigger:
    def __init__(self, run_date=None, **kwargs):
        self.run_date = run_date
        self.kwargs = kwargs
