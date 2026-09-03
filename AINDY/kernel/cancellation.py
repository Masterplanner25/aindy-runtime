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
is refused. The runtime can already hard-kill a Nodus worker (`subprocess.run(timeout=…)`) and a
sandboxed plugin (`terminate()` → `kill()`), and cannot hard-kill a tool it invoked in-process —
that asymmetry is `TOOL-SEAM-ISOLATION-1`'s half of this design, where terminate strength is a
function of the isolation class. This module is the in-process half and degrades honestly:
it refuses the next effect and says that is what it did.

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
    one. That is the out-of-process tool worker's case, which passes ``run_id=None`` — and it is
    the right answer there rather than an oversight, because that path is hard-killable by its
    isolation class instead.
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
