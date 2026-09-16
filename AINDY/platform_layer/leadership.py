"""
Background-task leadership election via a Postgres-backed lease (LEASE-1).

The distributed deployment profiles (``distributed-api``, ``distributed-worker``,
``hostile-third-party``) advertise ``background_leadership_mode: "lease-elected"``
in the deployment contract. The contract guarantee is: *exactly one* participating
runtime process runs the APScheduler maintenance jobs (stuck-run watchdog,
EffectRecord TTL cleanup, orphaned-approved recovery, db-pool metrics, etc.) at a
time. Before LEASE-1 this guarantee was advertised but not enforced — every
process whose local startup hooks succeeded self-elected, so N API replicas ran
N schedulers. This module enforces the advertised contract using the
``background_task_leases`` table.

Mechanism
---------
A single row keyed on ``name`` carries ``owner_id`` + ``expires_at``. A process
becomes (or stays) leader by atomically claiming or renewing that row:

  * No row exists      -> INSERT (the table's UNIQUE(name) resolves the insert race).
  * Row owned by us    -> renew: bump ``heartbeat_at`` and extend ``expires_at``.
  * Row expired        -> take over: set ``owner_id`` to us.
  * Row owned + live   -> not acquired; stay a follower.

``SELECT ... FOR UPDATE`` serialises contenders on PostgreSQL so only one wins a
contested round. Every lease-electing profile requires PostgreSQL, so the row
lock is always available in production; on the SQLite unit-test harness the lock
clause is a no-op and the single-threaded tests exercise the state logic only.

Failover
--------
``BackgroundLeadershipElector`` runs on *every* lease-electing process on a daemon
thread. Each tick it re-attempts the claim:
  * The leader renews (claim by same owner) and keeps its scheduler running.
  * A follower's claim fails while the leader's lease is live, and succeeds once
    the leader dies and its lease expires (after at most ``LEASE_TTL_SECONDS``),
    at which point the follower starts its scheduler via ``on_acquire``.
  * A leader that loses the lease (e.g. a long stop-the-world pause let a follower
    take over) detects the loss on its next tick and stands down via ``on_lose``,
    preventing split-brain.

Clock note: expiry is evaluated against the kernel clock (``utcnow``) of the
calling process, not the database clock. ``LEASE_TTL_SECONDS`` (60s) is wide
relative to ``LEASE_HEARTBEAT_SECONDS`` (20s) to tolerate the clock skew expected
between co-deployed instances.

Fencing (LEASE-FENCE-1)
-----------------------
Expiry bounds how LONG two leaders coexist and does nothing about what the stale
one WRITES: a leader stalled past its TTL learns it lost the lease at its next
tick, and a job already running keeps running as leader. So the row carries a
monotonic ``fence`` — 1 on the first claim, unchanged on renew, +1 on every
takeover — and a leader-only job whose re-run is NOT harmless calls
``assert_lease_fence(db, background_leader_fence())`` INSIDE its own transaction,
before its commit. The check reads the row ``FOR SHARE``: a takeover's
``FOR UPDATE`` blocks until the job commits, and a takeover that already
committed leaves a higher fence, so the stale leader is REFUSED rather than
asked to notice. Design: ``docs/design/LEASE_FENCE_DESIGN.md`` — §2 names which
jobs are fenced and why the other ten deliberately are not.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError

from AINDY.db.models.background_task_lease import BackgroundTaskLease

logger = logging.getLogger(__name__)

LEASE_NAME = "background_runner"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


LEASE_TTL_SECONDS = _int_env("AINDY_BACKGROUND_LEASE_TTL_SECONDS", 60)
LEASE_HEARTBEAT_SECONDS = _int_env("AINDY_BACKGROUND_LEASE_HEARTBEAT_SECONDS", 20)


def background_owner_id() -> str:
    """Stable, per-process owner identity.

    ``HOSTNAME`` is the container id under Docker/Kubernetes (unique per replica);
    the pid suffix disambiguates multiple processes sharing a host.
    """
    host = os.getenv("HOSTNAME") or "local"
    return f"{host}:{os.getpid()}"


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class LeaseHold:
    """What a successful claim returns: who holds it, under which fence, until when."""

    owner_id: str
    fence: int
    expires_at: datetime


class LeaseFenceLost(RuntimeError):
    """The lease row's fence is not the one this process holds — a takeover happened.

    Raised INSIDE a leader-only job's transaction by ``assert_lease_fence``; the job must
    roll back and do nothing. Not a bug: it is the fence doing its one job.
    """


def claim_lease(
    db,
    owner_id: str,
    *,
    name: str = LEASE_NAME,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> Optional[LeaseHold]:
    """Atomically claim, renew, or take over the background lease; return the hold or ``None``.

    Renew and acquire share one implementation — a renew is simply a claim by the current
    owner. The fence moves ONLY on a takeover (LEASE-FENCE-1): 1 on a fresh insert, unchanged
    on renew, ``+1`` when an expired lease changes hands.
    """
    from AINDY.db.database import utcnow

    now = utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    try:
        row = (
            db.query(BackgroundTaskLease)
            .filter(BackgroundTaskLease.name == name)
            .with_for_update()
            .first()
        )
        if row is None:
            db.add(
                BackgroundTaskLease(
                    name=name,
                    owner_id=owner_id,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires,
                    fence=1,
                )
            )
            db.commit()
            return LeaseHold(owner_id, 1, expires)

        if row.owner_id == owner_id:
            # Renew — same owner extends its hold; the fence is untouched.
            row.heartbeat_at = now
            row.expires_at = expires
            db.commit()
            return LeaseHold(owner_id, int(row.fence or 0), expires)

        current_expiry = _as_utc(row.expires_at)
        if current_expiry is None or current_expiry <= now:
            # Previous leader's lease has lapsed — take over, and move the fence so anything
            # the previous leader still has in flight is refused at its next fenced commit.
            # `or 0` covers a row that predates the column (Alembic 0019 default).
            new_fence = int(row.fence or 0) + 1
            row.owner_id = owner_id
            row.acquired_at = now
            row.heartbeat_at = now
            row.expires_at = expires
            row.fence = new_fence
            db.commit()
            logger.info(
                "[leadership] lease %r taken over by owner_id=%s (previous lease expired; fence=%d)",
                name,
                owner_id,
                new_fence,
            )
            return LeaseHold(owner_id, new_fence, expires)

        # Live lease held by another owner.
        db.rollback()
        return None
    except IntegrityError:
        # Lost the INSERT race against a concurrent fresh claim — the other
        # process holds the lease; UNIQUE(name) rejected our insert.
        db.rollback()
        return None
    except Exception:
        db.rollback()
        raise


def try_acquire_lease(
    db,
    owner_id: str,
    *,
    name: str = LEASE_NAME,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> bool:
    """Boolean form of :func:`claim_lease` — ``True`` iff ``owner_id`` holds the lease after."""
    return claim_lease(db, owner_id, name=name, ttl_seconds=ttl_seconds) is not None


# Renewing and acquiring are the same atomic operation (claim by current owner).
renew_lease = try_acquire_lease


def assert_lease_fence(
    db,
    expected_fence: Optional[int],
    *,
    name: str = LEASE_NAME,
    job: str = "unknown",
) -> None:
    """Refuse a leader-only write if leadership changed hands — call INSIDE the job's transaction.

    Reads the lease row ``FOR SHARE`` (``with_for_update(read=True)``): a takeover's
    ``FOR UPDATE`` blocks until this transaction ends, so a job that passed the check commits
    before anyone can become leader; a takeover that already committed left a higher fence and
    this raises ``LeaseFenceLost``. On SQLite the lock clause is a no-op (as ``claim_lease``
    already documents for itself) and only the comparison runs.

    ``expected_fence=None`` means this process holds no fenced lease — the ``single-instance``
    in-process profile, or a hold that predates the column — and the check is skipped: there is
    nothing to compare, and refusing would stop maintenance on every non-distributed deployment.
    """
    if expected_fence is None:
        return
    row = (
        db.query(BackgroundTaskLease)
        .filter(BackgroundTaskLease.name == name)
        .with_for_update(read=True)
        .first()
    )
    current = None if row is None else int(row.fence or 0)
    if current != expected_fence:
        _count_fence_refusal(job)
        logger.warning(
            "[leadership] fence refused job=%s: held fence=%s, row fence=%s (owner=%s) — "
            "leadership changed hands; this process's write is refused",
            job, expected_fence, current, None if row is None else row.owner_id,
        )
        raise LeaseFenceLost(f"lease fence moved: held {expected_fence}, row {current}")


def _count_fence_refusal(job: str) -> None:
    try:
        from AINDY.platform_layer.metrics import lease_fence_refusals_total

        lease_fence_refusals_total.labels(job=job).inc()
    except Exception:  # noqa: BLE001 — observability never decides
        pass


def release_lease(db, owner_id: str, *, name: str = LEASE_NAME) -> None:
    """Release the lease if (and only if) ``owner_id`` currently holds it."""
    try:
        row = (
            db.query(BackgroundTaskLease)
            .filter(
                BackgroundTaskLease.name == name,
                BackgroundTaskLease.owner_id == owner_id,
            )
            .with_for_update()
            .first()
        )
        if row is not None:
            db.delete(row)
            db.commit()
            logger.info("[leadership] lease %r released by owner_id=%s", name, owner_id)
    except Exception as exc:
        db.rollback()
        logger.warning(
            "[leadership] lease release failed owner_id=%s: %s", owner_id, exc
        )


def current_lease(db, *, name: str = LEASE_NAME) -> Optional[BackgroundTaskLease]:
    """Return the current lease row (or ``None``) — read-only, for observability."""
    return (
        db.query(BackgroundTaskLease)
        .filter(BackgroundTaskLease.name == name)
        .first()
    )


class BackgroundLeadershipElector:
    """Daemon-thread lease elector with acquire/lose transition callbacks.

    Construct via :func:`get_background_elector` (process singleton). ``on_acquire``
    is invoked the moment this process becomes leader (start the scheduler);
    ``on_lose`` when it stops being leader (stand the scheduler down). Both are
    invoked at most once per transition and are exception-isolated.
    """

    def __init__(
        self,
        *,
        db_factory: Callable[[], object],
        owner_id: str,
        on_acquire: Optional[Callable[[], None]] = None,
        on_lose: Optional[Callable[[], None]] = None,
        name: str = LEASE_NAME,
        ttl_seconds: int = LEASE_TTL_SECONDS,
        heartbeat_seconds: int = LEASE_HEARTBEAT_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._db_factory = db_factory
        self.owner_id = owner_id
        self._on_acquire = on_acquire
        self._on_lose = on_lose
        self._name = name
        self._ttl = ttl_seconds
        self._interval = heartbeat_seconds
        self._enabled = enabled
        self._is_leader = False
        self._hold: Optional[LeaseHold] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def fence(self) -> Optional[int]:
        """The fence this process holds leadership under, or ``None`` when it is not leader."""
        hold = self._hold
        return hold.fence if (hold is not None and self._is_leader) else None

    def _attempt(self) -> bool:
        if not self._enabled:
            return False
        db = self._db_factory()
        try:
            hold = claim_lease(db, self.owner_id, name=self._name, ttl_seconds=self._ttl)
            self._hold = hold
            return hold is not None
        finally:
            db.close()

    @staticmethod
    def _safe_call(fn: Optional[Callable[[], None]]) -> None:
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:
            logger.error("[leadership] leadership transition callback failed: %s", exc)

    def _apply(self, acquired: bool) -> None:
        with self._lock:
            if acquired and not self._is_leader:
                self._is_leader = True
                logger.info(
                    "[leadership] %s elected background leader (owner_id=%s)",
                    self._name,
                    self.owner_id,
                )
                self._safe_call(self._on_acquire)
            elif not acquired and self._is_leader:
                self._is_leader = False
                logger.critical(
                    "[leadership] %s lost background lease (owner_id=%s) — standing down "
                    "to prevent split-brain",
                    self._name,
                    self.owner_id,
                )
                self._safe_call(self._on_lose)

    def elect_once(self) -> bool:
        """Run one election round synchronously; return current leadership."""
        try:
            acquired = self._attempt()
        except Exception as exc:
            logger.error("[leadership] lease attempt failed: %s", exc)
            acquired = False
        self._apply(acquired)
        return self._is_leader

    def start(self) -> None:
        """Launch the background renew/failover loop (idempotent)."""
        if not self._enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="background-leadership-elector", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.elect_once()

    def stop(self, *, release: bool = True) -> None:
        """Stop the loop and (by default) release the lease if held."""
        self._stop.set()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(1.0, float(self._interval)))
        self._thread = None
        if release and self._is_leader:
            db = self._db_factory()
            try:
                release_lease(db, self.owner_id, name=self._name)
            finally:
                db.close()
        self._is_leader = False


_ELECTOR: Optional[BackgroundLeadershipElector] = None
_ELECTOR_LOCK = threading.Lock()


def get_background_elector(**kwargs) -> BackgroundLeadershipElector:
    """Return the process-singleton elector, constructing it on first call."""
    global _ELECTOR
    with _ELECTOR_LOCK:
        if _ELECTOR is None:
            _ELECTOR = BackgroundLeadershipElector(**kwargs)
        return _ELECTOR


def stop_background_elector(*, release: bool = True) -> None:
    """Stop and discard the process-singleton elector (shutdown path)."""
    global _ELECTOR
    with _ELECTOR_LOCK:
        elector = _ELECTOR
        _ELECTOR = None
    if elector is not None:
        elector.stop(release=release)


def reset_background_elector() -> None:
    """Drop the singleton without stopping it — test isolation helper."""
    global _ELECTOR
    with _ELECTOR_LOCK:
        _ELECTOR = None


def background_leader_status() -> bool:
    """Whether this process currently holds background leadership."""
    elector = _ELECTOR
    return bool(elector and elector.is_leader)


def background_leader_fence() -> Optional[int]:
    """The fence this process leads under, or ``None`` (no elector — in-process profile — or
    not leader). Pass it to ``assert_lease_fence`` from a leader-only job."""
    elector = _ELECTOR
    return elector.fence if elector is not None else None
