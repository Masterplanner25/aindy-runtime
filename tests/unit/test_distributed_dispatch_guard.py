"""`dispatch()` must refuse distributed work the worker could not rebuild.

Found while scoping `FR-15 (a)`, and filed separately because it is not a scheduler problem —
it is a property of `dispatch()` that happens to bite the scheduler first.

**The asymmetry.** `dispatch(eu, handler_fn, context)` takes a closure. In thread mode that
closure *is* the work. In distributed mode it cannot be, so `_enqueue_distributed()` never
receives `handler_fn` at all — it builds a `QueueJobPayload` from `context` and the worker
rebuilds the work from the database by resolving `job_id` against `JobLog`.

**The silent failure.** Before this guard, a context with no `log_id` still produced a payload:
`job_id = context.get("log_id") or eu_id or uuid4()`. That fallback manufactured an id exactly
when there was none to use, and `worker_loop` treats an unresolvable job as *finished*::

    job_data = _fetch_job_data(job.job_id)   # None
    if job_data is None:
        logger.warning("[Worker] JobLog not found job_id=%s", job.job_id)
        q.ack(job.job_id)                    # ACKed, not DLQ'd
        return True                          # reported as SUCCESS

So the work disappears and every observable signal says it completed: no dead-letter entry, no
retry, no failed status, nothing for an operator to find. A warning in a worker log is not a
failure signal — nothing alerts on it and nothing counts it.

**Why it was never hit.** All three live `async_job_service` call sites pass `log_id`. That is
the entire guarantee, and it is an accident — nothing asked for it and nothing checked it. The
first caller to omit it would have paid the full price, which is what `FR-15 (a)` nearly did.

★ This is `EVENTBUS-PUBLISH-LATCH-1`'s lesson in a different subsystem: a degradation nobody
counted, indistinguishable from success. The repair is the same — make it loud.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.runtime_only


class _EU:
    type = "job"
    priority = "normal"
    id = "eu-guard-test"
    extra = {"async_hint": True}


def _distributed(monkeypatch):
    """Force the distributed transport, which is what `docker-compose.prod.yml` sets."""
    monkeypatch.setenv("EXECUTION_MODE", "distributed")


# ── The liveness control ─────────────────────────────────────────────────────


def test_work_carrying_a_log_id_still_enqueues(monkeypatch):
    """★ Without this, every assertion below passes against a `dispatch()` that refuses
    *everything* — which would break all three live `async_job_service` call sites while the
    guard's own tests stayed green."""
    from AINDY.core.execution_dispatcher import ExecutionMode, dispatch

    _distributed(monkeypatch)
    queue = MagicMock()

    with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
        result = dispatch(_EU(), handler_fn=lambda: None, context={"log_id": "job-1"})

    assert result.mode is ExecutionMode.ASYNC
    assert queue.enqueue.call_count == 1, "work with a resolvable id must still be distributed"
    assert queue.enqueue.call_args[0][0].job_id == "job-1"


# ── The guard ────────────────────────────────────────────────────────────────


def test_work_without_a_log_id_is_refused(monkeypatch):
    """The scheduler's shape exactly: `{eu_id, run_id, source}` and a closure."""
    from AINDY.core.execution_dispatcher import UndistributableWorkError, dispatch

    _distributed(monkeypatch)
    queue = MagicMock()

    with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
        with pytest.raises(UndistributableWorkError):
            dispatch(
                _EU(),
                handler_fn=lambda: None,
                context={"eu_id": "eu-1", "run_id": "run-1", "source": "scheduler.resume"},
            )


def test_refusal_happens_before_anything_is_enqueued(monkeypatch):
    """★ The assertion that matters operationally.

    Raising *after* the push would leave a poison message on the queue for a worker to ack as
    success — the exact outcome the guard exists to prevent, with an exception added rather
    than instead.
    """
    from AINDY.core.execution_dispatcher import UndistributableWorkError, dispatch

    _distributed(monkeypatch)
    queue = MagicMock()

    with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
        with pytest.raises(UndistributableWorkError):
            dispatch(_EU(), handler_fn=lambda: None, context={"eu_id": "eu-1"})

    assert queue.enqueue.call_count == 0, "a payload was pushed before the refusal"
    assert queue.enqueue_delayed.call_count == 0, "a delayed payload was pushed before the refusal"


def test_the_error_names_what_the_caller_must_do(monkeypatch):
    """An exception a reader cannot act on just relocates the confusion.

    Whoever trips this is adding a `dispatch()` caller and does not yet know that distributed
    mode drops the closure — so the message has to carry the cause and the two ways out, not
    just the missing key.
    """
    from AINDY.core.execution_dispatcher import UndistributableWorkError, dispatch

    _distributed(monkeypatch)
    queue = MagicMock()

    with patch("AINDY.core.distributed_queue.get_queue", return_value=queue):
        with pytest.raises(UndistributableWorkError) as caught:
            dispatch(_EU(), handler_fn=lambda: None, context={"eu_id": "eu-1", "run_id": "r"})

    message = str(caught.value)
    assert "log_id" in message
    assert "silent" in message, "the message must say the loss would be silent"
    assert "submit_async_job" in message, "the message must point at the way to get a JobLog"
    assert "eu_id" in message and "run_id" in message, (
        "the message must echo the keys actually seen, or a caller cannot tell which of their "
        "several dispatch sites tripped it"
    )


# ── The blast radius: thread mode is untouched ───────────────────────────────


def test_thread_mode_does_not_require_a_log_id(monkeypatch):
    """★ The guard must constrain the distributed transport ONLY.

    In thread mode the closure is carried directly, so there is nothing to reconstruct and
    nothing to refuse. A guard that fired here would break the scheduler's own dispatch — the
    path `FR-15 (a)` just enabled — and it would do so only under the flag, which is the
    hardest kind of regression to attribute.
    """
    from AINDY.core.execution_dispatcher import ExecutionMode, dispatch

    monkeypatch.setenv("EXECUTION_MODE", "thread")
    ran: list[int] = []

    result = dispatch(
        _EU(),
        handler_fn=lambda: ran.append(1),
        context={"eu_id": "eu-1", "run_id": "run-1", "source": "scheduler.resume"},
    )

    assert result.future is not None
    result.future.result(timeout=30)
    assert ran == [1], "thread-mode dispatch must run the closure regardless of log_id"


def test_every_live_async_job_dispatch_site_passes_a_log_id():
    """Supplement, not coverage — `ROUTE-GUARD-1`'s rule applies here too.

    The behavioural tests above prove the guard; this catches the specific regression of
    someone dropping `log_id` from one of the three `async_job_service` call sites, which
    would turn every distributed job submission into a hard failure. Cheap, and it fails with
    a message that says why rather than as a mysterious refusal at runtime.
    """
    import re
    from pathlib import Path

    src = Path("AINDY/platform_layer/async_job_service.py").read_text(encoding="utf-8")
    calls = re.findall(r"_dispatch\(\s*JOB_DISPATCH_STUB.*?\n\s*\)", src, re.S)

    assert len(calls) == 3, f"expected 3 job dispatch sites, found {len(calls)}"
    for call in calls:
        assert '"log_id"' in call, (
            "an async_job_service dispatch site no longer passes log_id. Under "
            "EXECUTION_MODE=distributed that now raises UndistributableWorkError instead of "
            "silently losing the job — correct, but it means this submission path is broken."
        )
