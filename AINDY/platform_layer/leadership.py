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
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import timedelta, timezone
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


def try_acquire_lease(
    db,
    owner_id: str,
    *,
    name: str = LEASE_NAME,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> bool:
    """Atomically claim, renew, or take over the background lease.

    Returns ``True`` iff ``owner_id`` holds the lease after the call. Renew and
    acquire share one implementation — a renew is simply a claim by the current
    owner.
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
                )
            )
            db.commit()
            return True

        if row.owner_id == owner_id:
            # Renew — same owner extends its hold.
            row.heartbeat_at = now
            row.expires_at = expires
            db.commit()
            return True

        current_expiry = _as_utc(row.expires_at)
        if current_expiry is None or current_expiry <= now:
            # Previous leader's lease has lapsed — take over.
            row.owner_id = owner_id
            row.acquired_at = now
            row.heartbeat_at = now
            row.expires_at = expires
            db.commit()
            logger.info(
                "[leadership] lease %r taken over by owner_id=%s (previous lease expired)",
                name,
                owner_id,
            )
            return True

        # Live lease held by another owner.
        db.rollback()
        return False
    except IntegrityError:
        # Lost the INSERT race against a concurrent fresh claim — the other
        # process holds the lease; UNIQUE(name) rejected our insert.
        db.rollback()
        return False
    except Exception:
        db.rollback()
        raise


# Renewing and acquiring are the same atomic operation (claim by current owner).
renew_lease = try_acquire_lease


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
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def _attempt(self) -> bool:
        if not self._enabled:
            return False
        db = self._db_factory()
        try:
            return try_acquire_lease(
                db, self.owner_id, name=self._name, ttl_seconds=self._ttl
            )
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
