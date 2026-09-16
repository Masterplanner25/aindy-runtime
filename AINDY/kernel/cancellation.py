"""Can a cancelled run be observed *before* the next effect, rather than after it?

`CANCEL-REACH-1`. `sys.v1.agent.cancel` flips a non-terminal run to `cancelled` via an atomic
CAS in a separate session, and the Nodus chain observes that **between segments** — its own
comment says *"before this segment's tools run … halts the chain between steps"*. A tool already
inside `entry["fn"](…)` — an HTTP call, a long query, a subprocess — runs to completion, and so
does every remaining tool in that segment.

This narrows the observation window from **segment** granularity to **effect** granularity.

★ IT IS COOPERATIVE, AND SAYING SO IS PART OF THE CONTRACT
-----------------------------------------------------------
Nothing here preempts anything. A tool already executing is not interrupted; the *next* effect
is refused. The runtime can hard-kill a Nodus worker (`subprocess.run(timeout=…)`), a sandboxed
plugin (`terminate()` → `kill()`) and — since 2026-09-15 — an isolated TOOL worker, whose parent
polls this module while the worker runs and kills it on a cancel (`aindy_run_cancel_observed_total
{surface="tool_worker"}`). It cannot hard-kill a tool it invoked in-process — that asymmetry is
`TOOL-SEAM-ISOLATION-1`'s half of this design, where terminate strength is a function of the
isolation class. In-process degrades honestly: it refuses the next effect and says so.

Two chokepoints observe it, the same two lines the effect ledger brackets: `execute_tool` before
`entry["fn"]` (surface `tool`, and `tool_worker` for the isolated path) and the syscall
dispatcher before `entry.handler` (surface `syscall`, the run read from the execution span —
see `current_run_id`).

★★ WHY IT FAILS OPEN, WHICH IS THE OPPOSITE OF MOST GUARDS HERE
-----------------------------------------------------------------
An error reading cancellation state returns "not cancelled". Every other guard in this runtime
fails closed, and this one must not: refusing an effect because a database blip made the answer
unreadable would abort live work that nobody cancelled, and an aborted effect is not recoverable
by retrying the check. **A missed cancel costs one more effect; a false cancel costs the run.**

★★ AND WHY IT MUST NOT QUERY PER EFFECT — A CONSTRAINT THIS REPOSITORY HAS PAID FOR TWICE
--------------------------------------------------------------------------------------------
`RT-MEMTXN-LEAK-1` exhausted the connection pool by holding a transaction across a slow call on
a **request-shared** session. `MEM-RECALL-N1-1` was an N+1 in the same family — three queries per
candidate to re-read four columns the originating SELECT already had.

So this never touches the caller's session and never queries per effect. It uses **its own
short-lived session**, at most once per run per TTL window, and a `cancelled` answer is cached
**forever** because cancellation is terminal — a run cannot un-cancel, so re-asking is pure cost.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: How long a *negative* answer is trusted. Short enough that a cancel lands within a couple of
#: effects, long enough that a tool loop cannot turn this into a query storm. A positive answer
#: is never re-checked — see `_CANCELLED`.
DEFAULT_TTL_SECONDS = 2.0

_LOCK = threading.Lock()
#: run_id -> (answer, expires_at). Only ever holds negatives; positives go to `_CANCELLED`.
_NEGATIVE: dict[str, float] = {}
#: run_ids observed cancelled. Terminal, so no expiry — and bounded by the number of runs a
#: process actually cancels, which is small.
_CANCELLED: set[str] = set()

try:
    from AINDY.platform_layer.metrics import run_cancel_observed_total

    _METRICS_AVAILABLE = True
except Exception:  # pragma: no cover - metrics optional at import time
    _METRICS_AVAILABLE = False


def reset_cancellation_cache() -> None:
    """Clear cached state. For tests, and for a worker recycling its process state."""
    with _LOCK:
        _NEGATIVE.clear()
        _CANCELLED.clear()


def _read_status(run_id: str) -> Optional[str]:
    """Read the run's status on a session of this module's own. Never the caller's."""
    from AINDY.db.database import SessionLocal
    from AINDY.db.models import AgentRun
    from AINDY.runtime.nodus_adapter import _db_run_id

    db = SessionLocal()
    try:
        row = db.query(AgentRun.status).filter(AgentRun.id == _db_run_id(run_id)).first()
        return None if row is None else str(row[0])
    finally:
        db.close()


def is_run_cancelled(run_id: Optional[str], *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bool:
    """Whether ``run_id`` has been cancelled. Cheap to call in a loop; never raises.

    ``None``/empty returns ``False``: an effect with no run to belong to cannot be cancelled by
    one.

    ★ The out-of-process tool WORKER still gets no ``run_id`` — and that is now correct rather
    than a gap (`CANCEL-REACH-1` residual 2, closed 2026-09-15): the check runs in the PARENT,
    which polls this while the worker runs and kills it on a cancel. Running the check inside
    the worker would put it in the one process that cannot act on the answer.
    """
    if not run_id:
        return False
    key = str(run_id)

    with _LOCK:
        if key in _CANCELLED:
            return True
        expires = _NEGATIVE.get(key)
        if expires is not None and expires > time.monotonic():
            return False

    try:
        status = _read_status(key)
    except Exception as exc:  # noqa: BLE001 — see the module docstring: fail OPEN
        logger.debug("[cancellation] status unreadable for run %s: %s", run_id, exc)
        return False

    cancelled = status == "cancelled"
    with _LOCK:
        if cancelled:
            _CANCELLED.add(key)
            _NEGATIVE.pop(key, None)
        else:
            _NEGATIVE[key] = time.monotonic() + max(0.0, ttl_seconds)
    return cancelled


def current_run_id() -> Optional[str]:
    """The run the current execution span belongs to, or ``None`` outside one.

    `CANCEL-REACH-1` residual 1 asked for a run identity on `SyscallContext` and said "pick the
    field, not the lookup — and settle it once". It was settled once, elsewhere, after that was
    written: `COST-GOVERNOR-1` phase 3 made `llm_attribution_scope(tenant, run)` the identity of
    an execution span, set by `execute_run` for the whole run. This reads the run from there.
    A second ContextVar or a seventh context field carrying the same fact would be the second
    vocabulary every entry here warns about.

    ★ In-process only. A Nodus worker subprocess does not inherit a ContextVar; a guest `sys()`
    dispatched there carries no run for this check to see — the guest's tools go through
    `execute_tool` in the parent, which is checked with an explicit run id.
    """
    try:
        from AINDY.platform_layer.token_meter import current_llm_attribution

        _tenant, run_id = current_llm_attribution()
        return str(run_id) if run_id else None
    except Exception:  # pragma: no cover - fail OPEN, like everything else here
        return None


def note_effect_refused(*, surface: str) -> None:
    """Count an effect refused because its run was cancelled.

    ★ Without this the mechanism is invisible: a cancelled run that stops early and a cancelled
    run that ran three more tools look identical from the outside, which is exactly the
    ambiguity that makes an operator distrust a guard. It also makes the *narrowing* measurable
    — the whole claim of this change is that fewer effects run after a cancel than before.
    """
    if not _METRICS_AVAILABLE:
        return
    try:
        run_cancel_observed_total.labels(surface=surface).inc()
    except Exception:  # pragma: no cover
        pass
