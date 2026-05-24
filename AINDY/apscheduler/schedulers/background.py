"""Minimal BackgroundScheduler used by tests and fallback runtime paths."""


class ConflictingIdError(Exception):
    """Raised when add_job() is called with a duplicate job id and replace_existing=False."""


class _Job:
    def __init__(self, *, func, trigger=None, id=None, name=None, replace_existing=False):
        self.func = func
        self.trigger = trigger
        self.id = id
        self.name = name
        self.replace_existing = replace_existing


class BackgroundScheduler:
    def __init__(self, job_defaults=None):
        self.job_defaults = job_defaults or {}
        self.running = False
        self._jobs = []

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
            )
        )

    def get_jobs(self):
        return list(self._jobs)

    def start(self):
        self.running = True

    def shutdown(self, wait=True):
        self.running = False
