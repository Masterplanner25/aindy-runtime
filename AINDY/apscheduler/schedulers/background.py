"""Minimal BackgroundScheduler used by tests and fallback runtime paths."""


class ConflictingIdError(Exception):
    """Raised when add_job() is called with a duplicate job id and replace_existing=False."""


class _Job:
    def __init__(
        self,
        *,
        func,
        trigger=None,
        id=None,
        name=None,
        replace_existing=False,
        executor="default",
        max_instances=None,
        coalesce=None,
        **kwargs,
    ):
        self.func = func
        self.trigger = trigger
        self.id = id
        self.name = name
        self.replace_existing = replace_existing
        # FR-15 (b) — the shim used to swallow **kwargs, so NO test could assert a job's
        # executor, max_instances or coalesce. Those are load-bearing here: `max_instances=1`
        # is what makes a blocked tick skip the next one, and the wait tick's dedicated
        # `executor` is what stops it being starved. Recording them makes the scheduler's
        # job configuration testable at all.
        self.executor = executor
        self.max_instances = max_instances
        self.coalesce = coalesce
        self.kwargs = dict(kwargs)


class BackgroundScheduler:
    def __init__(self, job_defaults=None, executors=None):
        self.job_defaults = job_defaults or {}
        self._executors = dict(executors or {})
        self.running = False
        self._jobs = []
        self._listeners = []

    def add_job(self, func, trigger=None, id=None, name=None, replace_existing=False, **kwargs):
        if id is not None:
            existing_ids = {job.id for job in self._jobs if job.id is not None}
            if id in existing_ids:
                if replace_existing:
                    self._jobs = [job for job in self._jobs if job.id != id]
                else:
                    raise ConflictingIdError(
                        f"Job with id {id!r} already exists. "
                        "Use replace_existing=True to overwrite it."
                    )
        self._jobs.append(
            _Job(
                func=func,
                trigger=trigger,
                id=id,
                name=name,
                replace_existing=replace_existing,
                **kwargs,
            )
        )

    def add_listener(self, callback, mask=None):
        """SYSMAX-5 — record listeners so a test can fire one.

        The real scheduler dispatches these itself; the shim only needs to prove the
        runtime registered a callback and that the callback does the right thing.
        """
        self._listeners.append((callback, mask))

    def get_jobs(self):
        return list(self._jobs)

    def start(self):
        self.running = True

    def shutdown(self, wait=True):
        self.running = False
