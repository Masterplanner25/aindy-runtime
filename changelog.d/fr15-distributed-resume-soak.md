### Changed — what CI proves about a distributed resume (`FR-15`)

- `tests/integration/test_soak_distributed_resume.py` drives the whole path on **live Redis and
  live PostgreSQL**: the real dispatcher enqueues a resume, the real `process_one_job` dequeues
  and rebuilds it, and the assertions are on what the far end received. It pins that an
  unreconstructible resume is **dead-lettered rather than acknowledged**, and that duplicate
  delivery of the same resume executes the work exactly once.
- **This is evidence, not a behaviour change.** `AINDY_ASYNC_SCHEDULER_DISPATCH` still refuses
  `EXECUTION_MODE=distributed`, and a test pins that too, so a green run here cannot be
  mistaken for production being fixed.

### Fixed — a soak that could not see the thing it tested

- **`get_queue()` returns an in-memory backend whenever `TESTING` or `TEST_MODE` is set, and
  checks that *before* `REDIS_URL`.** `pytest.integration.ini` sets both, so **no test in this
  repository could reach the Redis queue backend.** The first version of this soak passed 6/6
  while enqueueing and dequeueing inside one process, proving nothing about the transport it
  existed to exercise.
- The soak now constructs the Redis backend directly, on an isolated key namespace, and
  **asserts the backend is Redis and not degraded** before running anything — so the vacuum
  cannot recur silently. `QUEUE-DURABILITY-CLASS-1`'s in-memory fallback is the other way this
  same vacuum can appear, and the assertion covers both.
- Worth recording for the next person: this is the **second** instance of the shape in
  `FR-15`'s own path. `async_heavy_execution_enabled()` also returns False under those two
  variables before reading its flag. A test-mode short-circuit placed above the real decision
  makes the real path untestable while every test passes.
