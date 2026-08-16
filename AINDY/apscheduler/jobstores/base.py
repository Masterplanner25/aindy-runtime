"""Minimal APScheduler jobstore exceptions shim.

`JobLookupError` is what the real scheduler raises when `remove_job`/`get_job` is given an id
that is not registered. The runtime catches it to mean *"already gone, fine"* — so the shim must
raise the same type, or a test would exercise a different control path than production.
"""


class JobLookupError(KeyError):
    """Raised when a job id is not found in the store."""

    def __init__(self, job_id):
        super().__init__(f"No job by the id of {job_id} was found")
        self.job_id = job_id
