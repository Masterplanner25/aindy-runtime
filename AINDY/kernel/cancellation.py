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

★ AUTHORITY-LIFETIME-1 (DEC-056..059) — THE SAME READ, WIDENED TO EVERY TERMINAL STATUS
------------------------------------------------------------------------------------------
A capability token was bound to the clock (`TOKEN_TTL_HOURS = 24`), not to the run it
authorises: a run that finished in 90 s could present its token all day. The three questions the
entry asked — where the check lives, whether to negative-cache, fail-open or closed — were
answered HERE, with evidence, for the `cancelled` value of the same column. So `run_terminal_status`
is that read returning the status instead of a bool; `is_run_cancelled` is `== "cancelled"` over
it (unchanged behaviour, pinned); the two sites that read it are the two that read cancel —
`check_tool_capability` (before the HMAC check) and the dispatcher's agent gate — and no third.
Every terminal answer is sticky for the process lifetime (a run never leaves a terminal state),
so the negative cache is the cache. It fails OPEN for the same reason cancel does, and the token's
expiry stays the outer bound. ★ A `waiting` run KEEPS its authority (DEC-059): a parked run
resumes with the same token, and revoking on wait would make every resume a re-mint — a grant
path `DEC-016` deliberately denied.
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
#: run_id -> expires_at. Only ever holds NON-terminal answers; terminal ones go to `_TERMINAL`.
_NEGATIVE: dict[str, float] = {}
#: run_id -> the terminal status observed. No expiry — a run never leaves a terminal state — and
#: bounded by the number of runs a process actually sees end, which is small.
_TERMINAL: dict[str, str] = {}

try:
    from AINDY.kernel.condition_codes import AGENT_TERMINAL_STATUSES as _AGENT_TERMINAL
except Exception:  # pragma: no cover - the kernel vocabulary is always importable in practice
    _AGENT_TERMINAL = frozenset({"completed", "failed", "cancelled", "verify_failed"})

#: The statuses after which a run's authority has ended. Derived from the kernel's own agent
#: vocabulary (variant 12: never a hand-written census), plus `refused` — the value
#: `finalize_for_run_status` maps and an admission gate may write before a run ever executes.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(_AGENT_TERMINAL) | {"refused"}

try:
    from AINDY.platform_layer.metrics import authority_lifetime_refusals_total, run_cancel_observed_total

    _METRICS_AVAILABLE = True
except Exception:  # pragma: no cover - metrics optional at import time
    _METRICS_AVAILABLE = False


def reset_cancellation_cache() -> None:
    """Clear cached state. For tests, and for a worker recycling its process state."""
    with _LOCK:
        _NEGATIVE.clear()
        _TERMINAL.clear()


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


def run_terminal_status(
    run_id: Optional[str], *, ttl_seconds: float = DEFAULT_TTL_SECONDS
) -> Optional[str]:
    """The run's TERMINAL status, or ``None`` while it is live (or unknown). Never raises.

    AUTHORITY-LIFETIME-1's read — the one `is_run_cancelled` always did, returning the status
    instead of a bool. ``None``/empty run returns ``None``: an effect with no run to belong to
    has no run to end. A missing row is ``None`` too (a run that does not exist has not ended;
    the token's own run binding is what refuses it). An unreadable status is ``None`` — fail
    OPEN, see the module docstring.

    A terminal answer is cached for the process lifetime; a live answer for ``ttl_seconds``.
    """
    if not run_id:
        return None
    key = str(run_id)

    with _LOCK:
        terminal = _TERMINAL.get(key)
        if terminal is not None:
            return terminal
        expires = _NEGATIVE.get(key)
        if expires is not None and expires > time.monotonic():
            return None

    try:
        status = _read_status(key)
    except Exception as exc:  # noqa: BLE001 — see the module docstring: fail OPEN
        logger.debug("[cancellation] status unreadable for run %s: %s", run_id, exc)
        return None

    with _LOCK:
        if status in TERMINAL_RUN_STATUSES:
            _TERMINAL[key] = str(status)
            _NEGATIVE.pop(key, None)
            return str(status)
        _NEGATIVE[key] = time.monotonic() + max(0.0, ttl_seconds)
        return None


def is_run_cancelled(run_id: Optional[str], *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bool:
    """Whether ``run_id`` has been cancelled. Cheap to call in a loop; never raises.

    ``None``/empty returns ``False``: an effect with no run to belong to cannot be cancelled by
    one. Since AUTHORITY-LIFETIME-1 this is `run_terminal_status(...) == "cancelled"` — the same
    read and the same cache, pinned identical in behaviour.

    ★ The out-of-process tool WORKER still gets no ``run_id`` — and that is now correct rather
    than a gap (`CANCEL-REACH-1` residual 2, closed 2026-09-15): the check runs in the PARENT,
    which polls this while the worker runs and kills it on a cancel. Running the check inside
    the worker would put it in the one process that cannot act on the answer.
    """
    return run_terminal_status(run_id, ttl_seconds=ttl_seconds) == "cancelled"


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


def note_authority_ended(*, status: str, surface: str) -> None:
    """Count an effect refused because its run had already reached ``status`` (terminal).

    A cancelled run is ALSO counted on `note_effect_refused` by its callers — that counter is
    CANCEL-REACH-1's narrowing signal and must keep moving when a cancel is observed early.
    """
    if not _METRICS_AVAILABLE:
        return
    try:
        authority_lifetime_refusals_total.labels(status=str(status), surface=surface).inc()
    except Exception:  # pragma: no cover
        pass


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
