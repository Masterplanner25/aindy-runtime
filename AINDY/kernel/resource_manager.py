"""
Resource Manager — A.I.N.D.Y. OS Resource Tracking and Quota Enforcement

Tracks CPU time, memory, and syscall counts per ExecutionUnit.
Enforces per-tenant and per-execution quotas.
Thread-safe; designed as a process-level singleton.

Quota defaults
--------------
  MAX_WALL_TIME_MS           300 000   (5 minutes wall-clock per execution unit)
  MAX_MEMORY_BYTES           268 435 456  (256 MiB)
  MAX_SYSCALLS_PER_EXECUTION 100
  MAX_CONCURRENT_PER_TENANT  5
  MAX_TOKENS_PER_EXECUTION   0  (unlimited — COST-GOVERNOR-1 phase 4, opt-in)
  MAX_TOKENS_PER_TENANT_WINDOW 0 (unlimited — opt-in; window = TENANT_KEY_TTL_SECONDS)

These can be overridden via environment variables:
  AINDY_QUOTA_CPU_MS         (sets MAX_WALL_TIME_MS; env var name kept for compatibility)
  AINDY_QUOTA_MEMORY_BYTES
  AINDY_QUOTA_MAX_SYSCALLS
  AINDY_QUOTA_MAX_CONCURRENT
  AINDY_QUOTA_MAX_TOKENS          (per execution unit / agent run; 0 = unlimited)
  AINDY_QUOTA_MAX_TENANT_TOKENS   (per tenant window; 0 = unlimited)

Usage
-----
    from AINDY.kernel.resource_manager import get_resource_manager

    rm = get_resource_manager()

    # Before executing:
    ok, reason = rm.can_execute(tenant_id="user-123", eu_id="eu-abc")
    if not ok:
        raise ResourceLimitError(reason)

    rm.mark_started("user-123", "eu-abc")
    try:
        ...
        rm.record_usage("eu-abc", {"wall_time_ms": 250, "syscall_count": 3})
        ok, reason = rm.check_quota("eu-abc")
        if not ok:
            raise ResourceLimitError(reason)
    finally:
        rm.mark_completed("user-123", "eu-abc")
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid as _uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from AINDY.config import settings

logger = logging.getLogger(__name__)


def _count_evicted(n: int) -> None:
    """Record snapshots evicted unreaped. Never fatal — metrics are optional."""
    try:
        from AINDY.platform_layer.metrics import resource_usage_evicted_total

        resource_usage_evicted_total.inc(n)
    except Exception:  # pragma: no cover - metrics optional
        logger.debug("[ResourceManager] eviction metric skipped", exc_info=True)

# Redis key schema
# ----------------
# Tenant concurrency counter: aindy:quota:concurrent:{tenant_id}
#
# Per-EU usage tracking remains process-local by design. Only tenant
# concurrent execution count is coordinated across instances.

# ── Quota constants ───────────────────────────────────────────────────────────

def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


MAX_WALL_TIME_MS: int = _int_env("AINDY_QUOTA_CPU_MS", 300_000)  # env var kept for operator compatibility
MAX_MEMORY_BYTES: int = _int_env("AINDY_QUOTA_MEMORY_BYTES", 256 * 1024 * 1024)
MAX_SYSCALLS_PER_EXECUTION: int = _int_env("AINDY_QUOTA_MAX_SYSCALLS", 100)
MAX_CONCURRENT_PER_TENANT: int = _int_env("AINDY_QUOTA_MAX_CONCURRENT", 5)
# COST-GOVERNOR-1 phase 4 — the LLM token ceilings. ★ Default 0 = UNLIMITED, i.e. the governor
# ships OPT-IN: a budget that refuses nothing until an operator names a number. Every other
# ceiling here has a non-zero default because its dimension self-limits; spend does not, and a
# wrong default here refuses real work — so the operator sets it against a measured baseline
# (`aindy_llm_tokens_total`, which phase 2 proved moves).
MAX_TOKENS_PER_EXECUTION: int = _int_env("AINDY_QUOTA_MAX_TOKENS", 0)
MAX_TOKENS_PER_TENANT_WINDOW: int = _int_env("AINDY_QUOTA_MAX_TENANT_TOKENS", 0)

RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
EU_KEY_TTL_SECONDS = 3600
TENANT_KEY_TTL_SECONDS = 86400
# QUOTA-ACCRUAL-ORPHAN-1 — how often the in-memory store looks for snapshots past
# EU_KEY_TTL_SECONDS. The sweep is O(n) over the store, so it is rate-limited rather than
# run on every admission check.
_EVICTION_SWEEP_INTERVAL_SECONDS = 60.0


# ── Exceptions ────────────────────────────────────────────────────────────────

class ResourceLimitError(Exception):
    """Raised when a resource quota is exceeded during execution.

    The error message includes the RESOURCE_LIMIT_EXCEEDED code so callers
    can detect it without isinstance() checks.
    """


# ── Usage snapshot ────────────────────────────────────────────────────────────

@dataclass
class UsageSnapshot:
    """Accumulated resource usage for a single ExecutionUnit."""

    eu_id: str
    tenant_id: str
    wall_time_ms: int = 0
    memory_bytes: int = 0
    syscall_count: int = 0
    # COST-GOVERNOR-1 phase 3 — LLM tokens (prompt + completion) attributed to this unit by
    # `token_meter.observe_llm_usage`. The dominant cost of an LLM execution unit, and until
    # this field existed the one dimension the quota system did not carry.
    tokens: int = 0
    # Monotonic creation time — the in-memory store's analogue of the Redis backend's
    # EU_KEY_TTL_SECONDS expiry (QUOTA-ACCRUAL-ORPHAN-1). Not part of to_dict().
    created_at: float = field(default_factory=time.monotonic, compare=False, repr=False)

    def to_dict(self) -> dict:
        return {
            "eu_id": self.eu_id,
            "tenant_id": self.tenant_id,
            "wall_time_ms": self.wall_time_ms,
            "memory_bytes": self.memory_bytes,
            "syscall_count": self.syscall_count,
            "tokens": self.tokens,
        }


class RedisResourceBackend:
    """Redis-backed resource quota state shared across processes."""

    _DECR_FLOOR_LUA = """
local value = redis.call('DECR', KEYS[1])
if value < 0 then
    redis.call('SET', KEYS[1], 0)
    value = 0
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return value
"""

    _SET_IF_GREATER_LUA = """
local current = redis.call('GET', KEYS[1])
if (not current) or (tonumber(ARGV[1]) > tonumber(current)) then
    redis.call('SET', KEYS[1], ARGV[1])
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

    def __init__(self, redis_url: str) -> None:
        import redis  # type: ignore[import]

        self._redis = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        self._decr_floor = self._redis.register_script(self._DECR_FLOOR_LUA)
        self._set_if_greater = self._redis.register_script(self._SET_IF_GREATER_LUA)

    def _tenant_key(self, tenant_id: str) -> str:
        return f"aindy:rm:tenant:{tenant_id}:active"

    def _cpu_key(self, eu_id: str) -> str:
        return f"aindy:rm:eu:{eu_id}:cpu_ms"

    def _syscalls_key(self, eu_id: str) -> str:
        return f"aindy:rm:eu:{eu_id}:syscalls"

    def _memory_key(self, eu_id: str) -> str:
        return f"aindy:rm:eu:{eu_id}:memory_bytes"

    def _tokens_key(self, eu_id: str) -> str:
        return f"aindy:rm:eu:{eu_id}:tokens"

    def _tenant_tokens_key(self, tenant_id: str) -> str:
        return f"aindy:rm:tenant:{tenant_id}:tokens"

    def increment_tenant_active(self, tenant_id: str) -> int:
        key = self._tenant_key(tenant_id)
        value = int(self._redis.incr(key))
        self._redis.expire(key, TENANT_KEY_TTL_SECONDS)
        return value

    def decrement_tenant_active(self, tenant_id: str) -> int:
        key = self._tenant_key(tenant_id)
        value = self._decr_floor(keys=[key], args=[str(TENANT_KEY_TTL_SECONDS)])
        return int(value)

    def get_tenant_active(self, tenant_id: str) -> int:
        value = self._redis.get(self._tenant_key(tenant_id))
        return int(value) if value is not None else 0

    def add_cpu_ms(self, eu_id: str, ms: int) -> int:
        key = self._cpu_key(eu_id)
        value = int(self._redis.incrby(key, ms))
        self._redis.expire(key, EU_KEY_TTL_SECONDS)
        return value

    def get_cpu_ms(self, eu_id: str) -> int:
        value = self._redis.get(self._cpu_key(eu_id))
        return int(value) if value is not None else 0

    def increment_syscalls(self, eu_id: str) -> int:
        key = self._syscalls_key(eu_id)
        value = int(self._redis.incr(key))
        self._redis.expire(key, EU_KEY_TTL_SECONDS)
        return value

    def get_syscalls(self, eu_id: str) -> int:
        value = self._redis.get(self._syscalls_key(eu_id))
        return int(value) if value is not None else 0

    def set_memory_if_greater(self, eu_id: str, bytes_used: int) -> None:
        self._set_if_greater(
            keys=[self._memory_key(eu_id)],
            args=[str(bytes_used), str(EU_KEY_TTL_SECONDS)],
        )

    def add_tokens(self, eu_id: str, count: int) -> int:
        key = self._tokens_key(eu_id)
        value = int(self._redis.incrby(key, count))
        self._redis.expire(key, EU_KEY_TTL_SECONDS)
        return value

    def get_tokens(self, eu_id: str) -> int:
        value = self._redis.get(self._tokens_key(eu_id))
        return int(value) if value is not None else 0

    def add_tenant_tokens(self, tenant_id: str, count: int) -> int:
        key = self._tenant_tokens_key(tenant_id)
        value = int(self._redis.incrby(key, count))
        self._redis.expire(key, TENANT_KEY_TTL_SECONDS)
        return value

    def get_tenant_tokens(self, tenant_id: str) -> int:
        value = self._redis.get(self._tenant_tokens_key(tenant_id))
        return int(value) if value is not None else 0

    def delete_eu(self, eu_id: str) -> None:
        self._redis.delete(
            self._cpu_key(eu_id),
            self._syscalls_key(eu_id),
            self._memory_key(eu_id),
            self._tokens_key(eu_id),
        )

    def reset_all(self) -> None:
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match="aindy:rm:*", count=100)
            if keys:
                self._redis.delete(*keys)
            if int(cursor) == 0:
                break


_RESOURCE_BACKEND: RedisResourceBackend | None = None
_RESOURCE_BACKEND_LOCK = threading.Lock()
_RESOURCE_BACKEND_INITIALIZED = False


def _get_backend() -> RedisResourceBackend | None:
    """Return a cached Redis resource backend when enabled."""
    global _RESOURCE_BACKEND
    global _RESOURCE_BACKEND_INITIALIZED

    if _RESOURCE_BACKEND_INITIALIZED:
        return _RESOURCE_BACKEND

    with _RESOURCE_BACKEND_LOCK:
        if _RESOURCE_BACKEND_INITIALIZED:
            return _RESOURCE_BACKEND

        redis_url = os.getenv("REDIS_URL")
        test_mode = os.getenv("TEST_MODE", "0").lower() in {"1", "true", "yes"}
        if redis_url and not test_mode:
            try:
                _RESOURCE_BACKEND = RedisResourceBackend(redis_url)
            except Exception as exc:
                logger.warning(
                    "[ResourceManager] redis backend unavailable error=%s",
                    exc,
                )
                _RESOURCE_BACKEND = None
        else:
            _RESOURCE_BACKEND = None
        _RESOURCE_BACKEND_INITIALIZED = True
        return _RESOURCE_BACKEND


# ── ResourceManager ───────────────────────────────────────────────────────────

class ResourceManager:
    """Thread-safe resource tracker and quota enforcer.

    Maintains in-memory usage counters for each active ExecutionUnit and
    per-tenant concurrency counts.  All methods are safe to call from
    multiple threads simultaneously.

    Lifecycle per execution unit:
        1. can_execute(tenant_id, eu_id)   → check quota before start
        2. mark_started(tenant_id, eu_id)  → increment active counter
        3. record_usage(eu_id, usage)      → accumulate usage deltas
        4. check_quota(eu_id)              → mid-execution limit check
        5. mark_completed(tenant_id, eu_id)→ decrement active counter

    Note: eu_id may be None for anonymous executions; those still
    count toward tenant concurrency but have no per-EU usage tracking.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend = _get_backend()
        self.MAX_CONCURRENT_PER_TENANT = MAX_CONCURRENT_PER_TENANT
        self._redis = None
        self._redis_check_lock = threading.Lock()
        self._redis_last_check: float = 0.0
        self._redis_mode_logged: bool | None = None
        self._REDIS_CHECK_INTERVAL = 30.0
        # eu_id → UsageSnapshot
        self._usage: dict[str, UsageSnapshot] = {}
        # tenant_id → active execution count
        self._active_counts: dict[str, int] = {}
        self._tenant_active = self._active_counts
        # eu_id → tenant_id (for cleanup on mark_completed with unknown eu_id)
        self._eu_tenant: dict[str, str] = {}
        self._pending_purge: set[str] = set()
        self._last_eviction_sweep: float = time.monotonic()
        # COST-GOVERNOR-1 phase 3 — tenant → (tokens, window_started_at). The in-memory
        # analogue of the Redis tenant tokens key, which carries TENANT_KEY_TTL_SECONDS.
        self._tenant_tokens: dict[str, tuple[int, float]] = {}
        # AGENT-HARDEN-8 PR2 — per-capability fixed-window rate counters (in-memory
        # fallback when Redis is absent): key → (bucket, count).
        self._rate_windows: dict[str, tuple[int, int]] = {}
        self._rate_lock = threading.Lock()

    def rate_limit_hit(
        self,
        key: str,
        *,
        limit: int,
        window_secs: int,
        now: float | None = None,
    ) -> tuple[int, bool]:
        """Record one hit against a fixed-window rate counter; return (count, exceeded).

        Uses the shared Redis counters when available (so the limit is enforced
        across instances) and falls back to a thread-safe in-memory window
        otherwise. ``exceeded`` is True once the count for the current window
        passes ``limit``. Fail-open: any backend error returns ``(0, False)`` so a
        counter hiccup never blocks a permitted call.
        """
        if limit <= 0 or window_secs <= 0:
            return 0, False
        import time as _time

        ts = _time.time() if now is None else now
        bucket = int(ts // window_secs)

        redis_client = None
        try:
            redis_client = self._get_redis()
        except Exception:
            redis_client = None

        if redis_client is not None:
            try:
                rkey = f"aindy:rm:rate:{key}:{bucket}"
                count = int(redis_client.incr(rkey))
                redis_client.expire(rkey, window_secs * 2)
                return count, count > limit
            except Exception as exc:
                self._drop_redis_client("[resource_manager] rate counter redis error: %s", exc)

        with self._rate_lock:
            state = self._rate_windows.get(key)
            count = 1 if (state is None or state[0] != bucket) else state[1] + 1
            self._rate_windows[key] = (bucket, count)
            # Opportunistic prune of stale windows to bound memory.
            if len(self._rate_windows) > 4096:
                for k, (b, _c) in list(self._rate_windows.items()):
                    if b < bucket:
                        self._rate_windows.pop(k, None)
        return count, count > limit

    def _concurrency_key(self, tenant_id: str) -> str:
        return f"aindy:quota:concurrent:{tenant_id}"

    def _get_redis(self):
        now = time.monotonic()
        if (now - self._redis_last_check) <= self._REDIS_CHECK_INTERVAL:
            return self._redis

        with self._redis_check_lock:
            now = time.monotonic()
            if (now - self._redis_last_check) <= self._REDIS_CHECK_INTERVAL:
                return self._redis

            self._redis_last_check = now
            redis_url = settings.REDIS_URL or os.getenv("REDIS_URL")
            if not redis_url:
                self._redis = None
                self._redis_mode_logged = None
                return None

            try:
                import redis as _redis_lib

                client = _redis_lib.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1,
                )
                client.ping()
                self._redis = client
                try:
                    from AINDY.platform_layer.metrics import quota_redis_mode

                    quota_redis_mode.set(1)
                except Exception:
                    pass
                if self._redis_mode_logged is False:
                    logger.warning("[resource_manager] Redis reconnected; using shared quota counters")
                self._redis_mode_logged = True
            except Exception as exc:
                if self._redis_mode_logged is not False:
                    logger.warning("[resource_manager] Redis unavailable: %s", exc)
                self._redis = None
                try:
                    from AINDY.platform_layer.metrics import quota_redis_mode

                    quota_redis_mode.set(0)
                except Exception:
                    pass
                self._redis_mode_logged = False
            return self._redis

    def _drop_redis_client(self, message: str, exc: Exception) -> None:
        logger.warning(message, exc)
        with self._redis_check_lock:
            self._redis = None
            self._redis_last_check = time.monotonic()
            self._redis_mode_logged = False
        try:
            from AINDY.platform_layer.metrics import quota_redis_fallback_total, quota_redis_mode

            quota_redis_mode.set(0)
            quota_redis_fallback_total.inc()
        except Exception:
            pass

    def is_redis_mode(self) -> bool:
        return self._get_redis() is not None

    def _backend_get_tenant_active(self, tenant_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.get_tenant_active(tenant_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis get_tenant_active failed tenant=%s error=%s",
                    tenant_id,
                    exc,
                )
                return None
            raise

    def _backend_increment_tenant_active(self, tenant_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.increment_tenant_active(tenant_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis increment_tenant_active failed tenant=%s error=%s",
                    tenant_id,
                    exc,
                )
                return None
            raise

    def _backend_decrement_tenant_active(self, tenant_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.decrement_tenant_active(tenant_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis decrement_tenant_active failed tenant=%s error=%s",
                    tenant_id,
                    exc,
                )
                return None
            raise

    def _backend_add_cpu_ms(self, eu_id: str, ms: int) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.add_cpu_ms(eu_id, ms)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis add_cpu_ms failed eu=%s error=%s",
                    eu_id,
                    exc,
                )
                return None
            raise

    def _backend_get_cpu_ms(self, eu_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.get_cpu_ms(eu_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis get_cpu_ms failed eu=%s error=%s",
                    eu_id,
                    exc,
                )
                return None
            raise

    def _backend_increment_syscalls(self, eu_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.increment_syscalls(eu_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis increment_syscalls failed eu=%s error=%s",
                    eu_id,
                    exc,
                )
                return None
            raise

    def _backend_get_syscalls(self, eu_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.get_syscalls(eu_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis get_syscalls failed eu=%s error=%s",
                    eu_id,
                    exc,
                )
                return None
            raise

    def _backend_set_memory_if_greater(self, eu_id: str, bytes_used: int) -> None:
        if self._backend is None:
            return
        try:
            self._backend.set_memory_if_greater(eu_id, bytes_used)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis set_memory_if_greater failed eu=%s error=%s",
                    eu_id,
                    exc,
                )
                return
            raise

    def _backend_add_tokens(self, eu_id: str, count: int) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.add_tokens(eu_id, count)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning("[ResourceManager] redis add_tokens failed eu=%s error=%s", eu_id, exc)
                return None
            raise

    def _backend_get_tokens(self, eu_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.get_tokens(eu_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning("[ResourceManager] redis get_tokens failed eu=%s error=%s", eu_id, exc)
                return None
            raise

    def _backend_add_tenant_tokens(self, tenant_id: str, count: int) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.add_tenant_tokens(tenant_id, count)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis add_tenant_tokens failed tenant=%s error=%s", tenant_id, exc
                )
                return None
            raise

    def _backend_get_tenant_tokens(self, tenant_id: str) -> int | None:
        if self._backend is None:
            return None
        try:
            return self._backend.get_tenant_tokens(tenant_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis get_tenant_tokens failed tenant=%s error=%s", tenant_id, exc
                )
                return None
            raise

    def _backend_delete_eu(self, eu_id: str) -> None:
        if self._backend is None:
            return
        try:
            self._backend.delete_eu(eu_id)
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning(
                    "[ResourceManager] redis delete_eu failed eu=%s error=%s",
                    eu_id,
                    exc,
                )
                return
            raise

    def _backend_reset_all(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.reset_all()
        except Exception as exc:
            import redis  # type: ignore[import]
            if isinstance(exc, redis.RedisError):
                logger.warning("[ResourceManager] redis reset_all failed error=%s", exc)
                return
            raise

    # ── Pre-execution checks ──────────────────────────────────────────────────

    def can_execute(
        self,
        tenant_id: str,
        eu_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Check whether an execution may start.

        Checks:
        1. Per-tenant concurrent execution limit.

        Args:
            tenant_id: Tenant (user) requesting execution.
            eu_id:     Optional ExecutionUnit ID (for logging).

        Returns:
            (True, None) if execution is allowed.
            (False, reason_str) if a quota is exceeded.
        """
        self._sweep()

        if settings.is_testing:
            return True, None

        tid = str(tenant_id)
        redis_client = self._get_redis()
        if redis_client is not None:
            key = self._concurrency_key(tid)
            try:
                active = int(redis_client.get(key) or 0)
            except Exception as exc:
                self._drop_redis_client(
                    "[resource_manager] Redis get failed, using local: %s",
                    exc,
                )
                with self._lock:
                    active = self._active_counts.get(tid, 0)
        else:
            with self._lock:
                active = self._active_counts.get(tid, 0)

        if active >= self.MAX_CONCURRENT_PER_TENANT:
            reason = (
                f"{RESOURCE_LIMIT_EXCEEDED}: tenant {tenant_id!r} at "
                f"concurrent limit ({active}/{self.MAX_CONCURRENT_PER_TENANT})"
            )
            logger.warning("[ResourceManager] %s eu=%s", reason, eu_id)
            return False, reason
        return True, None

    def check_quota(self, eu_id: str) -> tuple[bool, str | None]:
        """Check per-execution quotas for an in-progress ExecutionUnit.

        Checks:
        1. Cumulative CPU time.
        2. Syscall count.
        (Memory is tracked but not enforced here — it requires OS integration.)

        Args:
            eu_id: ExecutionUnit ID to check.

        Returns:
            (True, None) if within limits.
            (False, reason_str) if a quota is exceeded.
        """
        self._sweep()

        if settings.is_testing:
            return True, None

        eid = str(eu_id)

        # ★ QUOTA-ACCRUAL-ORPHAN-1 — this used to re-run `can_execute(snap.tenant_id)` here,
        # i.e. re-decide ADMISSION for a unit that is already admitted and therefore already
        # in the tenant's active count. At exactly MAX_CONCURRENT_PER_TENANT that refused
        # every syscall of the last unit admitted (5/5 >= 5). It never fired only because no
        # dispatch snapshot carried a tenant — each dispatch minted its own unit with
        # tenant_id="" — and the moment the pipeline hands the dispatcher its real unit
        # (which has one) it would have. Admission is decided once, before mark_started;
        # a mid-execution check is about THIS unit's budget, which is what the docstring
        # has always said.
        with self._lock:
            snap = self._usage.get(eid)
            if snap is None:
                return True, None
            local_wall_time_ms = snap.wall_time_ms
            local_syscall_count = snap.syscall_count

        redis_wall_time_ms = self._backend_get_cpu_ms(eid)
        if redis_wall_time_ms is None:
            redis_wall_time_ms = local_wall_time_ms

        redis_syscall_count = self._backend_get_syscalls(eid)
        if redis_syscall_count is None:
            redis_syscall_count = local_syscall_count

        if redis_wall_time_ms > MAX_WALL_TIME_MS:
            reason = (
                f"{RESOURCE_LIMIT_EXCEEDED}: eu {eu_id!r} exceeded "
                f"wall_time_ms limit ({redis_wall_time_ms} > {MAX_WALL_TIME_MS})"
            )
            logger.warning("[ResourceManager] %s", reason)
            return False, reason

        if redis_syscall_count > MAX_SYSCALLS_PER_EXECUTION:
            reason = (
                f"{RESOURCE_LIMIT_EXCEEDED}: eu {eu_id!r} exceeded "
                f"syscall_count limit ({redis_syscall_count} > {MAX_SYSCALLS_PER_EXECUTION})"
            )
            logger.warning("[ResourceManager] %s", reason)
            return False, reason

        return True, None

    # ── Store hygiene (QUOTA-ACCRUAL-ORPHAN-1) ────────────────────────────────

    def _sweep(self) -> None:
        """Drop reaped snapshots, and — at most once per minute — expired ones.

        Two populations, deliberately distinct:

        * ``_pending_purge`` holds units whose owner called ``mark_completed``. Dropping
          them is the normal reap; it is deferred to here so a caller can still read final
          usage after completion.
        * Anything older than ``EU_KEY_TTL_SECONDS`` with no ``mark_completed`` is a unit
          nobody owned — accrued by a dispatch whose caller supplied no execution unit, or
          re-opened by work that inherited a unit after its owner returned (an async job
          submitted from a request keeps the request's ids on purpose, for trace
          continuity). The Redis backend already bounds these with the same TTL; the
          in-memory store did not, so a long-lived process grew one snapshot per such call
          forever. **Evictions are counted and logged** — a silent prune would be
          indistinguishable from a reap, and a non-zero count is the list of callers that
          still dispatch without owning a unit.
        """
        if self._pending_purge:
            with self._lock:
                for eid in list(self._pending_purge):
                    self._usage.pop(eid, None)
                    self._eu_tenant.pop(eid, None)
                self._pending_purge.clear()

        now = time.monotonic()
        if now - self._last_eviction_sweep < _EVICTION_SWEEP_INTERVAL_SECONDS:
            return
        self._last_eviction_sweep = now
        cutoff = now - EU_KEY_TTL_SECONDS
        tenant_cutoff = now - TENANT_KEY_TTL_SECONDS
        with self._lock:
            expired = [eid for eid, snap in self._usage.items() if snap.created_at < cutoff]
            for eid in expired:
                self._usage.pop(eid, None)
                self._eu_tenant.pop(eid, None)
            # Tenant token windows are NOT orphans — they expire by design, silently, exactly
            # as the Redis key does. Phase 4 decides the budget window; this is parity.
            for tid in [t for t, (_, started) in self._tenant_tokens.items() if started < tenant_cutoff]:
                self._tenant_tokens.pop(tid, None)
        if expired:
            _count_evicted(len(expired))
            logger.warning(
                "[ResourceManager] evicted %d usage snapshot(s) older than %ds that no owner "
                "reaped (QUOTA-ACCRUAL-ORPHAN-1) — a caller is dispatching without an "
                "execution unit; see aindy_syscall_unowned_unit_total for which syscalls",
                len(expired), EU_KEY_TTL_SECONDS,
            )

    @contextmanager
    def owned_execution(self, tenant_id: str, eu_id: str | None = None) -> Iterator[str]:
        """Own an execution unit's quota lifecycle for the duration of a block.

        The rule this exists to make cheap: **a caller that dispatches syscalls must own an
        execution unit — admit it, start it, and reap it.** The pipeline does this for
        routes; a transport that dispatches on its own (the MCP server) has to do it
        itself, or every call it makes accrues onto a unit nothing ever clears.

        Admission (``can_execute``) is decided once, here; the unit is then started under
        ``tenant_id`` and marked completed on exit whatever the block did. Yields the unit
        id to pass as ``execution_unit_id``. Raises ``ResourceLimitError`` when the tenant
        is at its concurrency limit — the caller decides how that surfaces.
        """
        eid = str(eu_id or _uuid.uuid4())
        ok, reason = self.can_execute(tenant_id, eid)
        if not ok:
            raise ResourceLimitError(reason or RESOURCE_LIMIT_EXCEEDED)
        self.mark_started(tenant_id, eid)
        try:
            yield eid
        finally:
            self.mark_completed(tenant_id, eid)

    @contextmanager
    def observed_unit(self, tenant_id: str, eu_id: str) -> Iterator[str]:
        """Own a unit's usage snapshot WITHOUT admitting it (COST-GOVERNOR-1 phase 3).

        `owned_execution` admits against the tenant's concurrency limit and starts the unit;
        that changes admission semantics for whatever it wraps. This does not: it creates the
        snapshot under `tenant_id` so usage attributed to `eu_id` has a subject, yields, and
        purges on exit. Read `get_usage(eu_id)` INSIDE the block if you want the final numbers.

        Built for the agent run: tokens spent under `execute_run` need somewhere to land that
        carries the run's identity, but whether an agent run should ALSO be admitted and
        syscall-capped as one unit is `EXEC-ENV-BIND-1` phase 4's subject decision, not this
        helper's to make by side effect.
        """
        eid = str(eu_id)
        tid = str(tenant_id or "")
        with self._lock:
            if eid not in self._usage:
                self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id=tid)
            elif tid and not self._usage[eid].tenant_id:
                self._usage[eid].tenant_id = tid
        try:
            yield eid
        finally:
            self.purge_eu(eid)

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def mark_started(self, tenant_id: str, eu_id: str | None = None) -> None:
        """Increment active execution counter for *tenant_id*.

        Also initialises the UsageSnapshot for *eu_id* if supplied.

        Args:
            tenant_id: The tenant starting an execution.
            eu_id:     Optional ExecutionUnit ID.
        """
        tid = str(tenant_id)
        redis_client = self._get_redis()
        if redis_client is not None:
            key = self._concurrency_key(tid)
            try:
                redis_client.incr(key)
            except Exception as exc:
                self._drop_redis_client(
                    "[resource_manager] Redis incr failed, using local: %s",
                    exc,
                )
                with self._lock:
                    self._active_counts[tid] = self._active_counts.get(tid, 0) + 1
        else:
            with self._lock:
                self._active_counts[tid] = self._active_counts.get(tid, 0) + 1

        with self._lock:
            if eu_id:
                eid = str(eu_id)
                if eid not in self._usage:
                    self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id=tid)
                self._eu_tenant[eid] = tid

    def reset_tenant_quota(self, tenant_id: str) -> None:
        tid = str(tenant_id)
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                redis_client.set(self._concurrency_key(tid), 0)
                return
            except Exception as exc:
                self._drop_redis_client(
                    "[resource_manager] Redis reset failed, using local: %s",
                    exc,
                )
        with self._lock:
            self._active_counts[tid] = 0

    def reset_all_concurrency_counters(self) -> int:
        """Delete all tenant concurrency keys in Redis and clear local counts.

        Called at API startup: any requests in-flight when the previous process
        died never called mark_completed, so their Redis counters are stuck.
        Resetting on startup brings all counters back to 0.

        Returns the number of Redis keys deleted (0 if not in Redis mode).
        """
        with self._lock:
            self._active_counts.clear()

        redis_client = self._get_redis()
        if redis_client is None:
            return 0
        deleted = 0
        try:
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(
                    cursor=cursor, match="aindy:quota:concurrent:*", count=200
                )
                if keys:
                    redis_client.delete(*keys)
                    deleted += len(keys)
                if int(cursor) == 0:
                    break
        except Exception as exc:
            logger.warning("[ResourceManager] reset_all_concurrency_counters failed: %s", exc)
        return deleted

    def mark_completed(self, tenant_id: str, eu_id: str | None = None) -> None:
        """Decrement active execution counter for *tenant_id*.

        Does NOT remove the UsageSnapshot so callers can read final usage
        after completion.  The snapshot is retained in memory until
        ``purge_eu(eu_id)`` is called.

        Capacity event
        --------------
        If this completion causes the tenant's active count to drop from
        at-or-above ``MAX_CONCURRENT_PER_TENANT`` to below it (i.e. the
        full → available transition), ``notify_event("resource_available")``
        is fired on the SchedulerEngine so that any FlowRun waiting on
        ``waiting_for="resource_available"`` is immediately re-enqueued.

        The event fires at most once per completion call — concurrent
        completions that decrement from N > MAX to MAX-1 still below the
        limit do NOT fire, preventing scheduler floods.  The notify call
        is made **outside** ``_lock`` to avoid lock-ordering deadlocks with
        ``SchedulerEngine._lock``.  Any scheduler failure is swallowed and
        logged at DEBUG level so quota state is never corrupted by a
        downstream error.

        Args:
            tenant_id: The tenant whose execution is completing.
            eu_id:     Optional ExecutionUnit ID.
        """
        tid = str(tenant_id)
        capacity_freed = False
        effective_current = 0
        effective_new_active = 0
        redis_client = self._get_redis()
        if redis_client is not None:
            key = self._concurrency_key(tid)
            try:
                effective_current = int(redis_client.get(key) or 0)
                effective_new_active = int(redis_client.decr(key))
                if effective_new_active < 0:
                    logger.warning(
                        "[ResourceManager] tenant concurrency counter underflow tenant=%s value=%d",
                        tid,
                        effective_new_active,
                    )
                    self.reset_tenant_quota(tid)
                    effective_new_active = 0
            except Exception as exc:
                self._drop_redis_client(
                    "[resource_manager] Redis decr failed, using local: %s",
                    exc,
                )
                with self._lock:
                    count = self._active_counts.get(tid, 0)
                    effective_current = count
                    if count > 0:
                        self._active_counts[tid] = count - 1
                    effective_new_active = self._active_counts.get(tid, 0)
        else:
            with self._lock:
                count = self._active_counts.get(tid, 0)
                effective_current = count
                if count > 0:
                    self._active_counts[tid] = count - 1
                effective_new_active = self._active_counts.get(tid, 0)

        with self._lock:
            if eu_id:
                self._pending_purge.add(str(eu_id))
            capacity_freed = (
                effective_current >= self.MAX_CONCURRENT_PER_TENANT
                and effective_new_active < self.MAX_CONCURRENT_PER_TENANT
            )

        if capacity_freed:
            # ── Outside _lock — no re-entrant deadlock risk ───────────────
            try:
                from AINDY.kernel.event_bus import publish_event
                publish_event("resource_available")
                logger.info(
                    "[ResourceManager] capacity freed tenant=%s active=%d→%d "
                    "— published resource_available to all instances",
                    tid, effective_current, effective_new_active,
                )
            except Exception as _exc:
                logger.debug(
                    "[ResourceManager] publish_event resource_available failed "
                    "(non-fatal): %s", _exc
                )

    # ── Usage recording ───────────────────────────────────────────────────────

    def record_cpu(self, eu_id: str, ms: int) -> None:
        eid = str(eu_id)
        delta = int(ms)
        with self._lock:
            if eid not in self._usage:
                self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id="")
            self._usage[eid].wall_time_ms += delta
        self._backend_add_cpu_ms(eid, delta)

    def record_memory(self, eu_id: str, bytes_used: int) -> None:
        eid = str(eu_id)
        value = int(bytes_used)
        with self._lock:
            if eid not in self._usage:
                self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id="")
            self._usage[eid].memory_bytes = max(self._usage[eid].memory_bytes, value)
        self._backend_set_memory_if_greater(eid, value)

    def record_syscall(self, eu_id: str, count: int = 1) -> None:
        eid = str(eu_id)
        steps = max(0, int(count))
        if steps == 0:
            return
        with self._lock:
            if eid not in self._usage:
                self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id="")
            self._usage[eid].syscall_count += steps
        for _ in range(steps):
            self._backend_increment_syscalls(eid)

    def record_tokens(self, eu_id: str, count: int, *, tenant_id: str | None = None) -> None:
        """Accrue LLM tokens onto a unit (COST-GOVERNOR-1 phase 3). Not enforced.

        `tenant_id`, when given, stamps a snapshot this call has to create — the other
        `record_*` methods create tenant-less snapshots because their callers are dispatches
        that have not been through `mark_started`; a token accrual usually knows its tenant.
        """
        eid = str(eu_id)
        delta = int(count)
        if delta <= 0:
            return
        with self._lock:
            if eid not in self._usage:
                self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id=str(tenant_id or ""))
            self._usage[eid].tokens += delta
        self._backend_add_tokens(eid, delta)

    def record_tenant_tokens(self, tenant_id: str, count: int) -> None:
        """Accrue LLM tokens onto a tenant's rolling window (COST-GOVERNOR-1 phase 3).

        The per-tenant counter the governor will read. Expires after
        `TENANT_KEY_TTL_SECONDS` on both backends; what the budget WINDOW should be — daily,
        monthly, per grant — is phase 4's decision, and this counter's expiry is not it.
        """
        tid = str(tenant_id)
        delta = int(count)
        if not tid or delta <= 0:
            return
        with self._lock:
            current, started = self._tenant_tokens.get(tid, (0, time.monotonic()))
            self._tenant_tokens[tid] = (current + delta, started)
        self._backend_add_tenant_tokens(tid, delta)

    def get_tenant_tokens(self, tenant_id: str) -> int:
        """Tokens accrued to a tenant in the current window (Redis-authoritative when shared)."""
        tid = str(tenant_id)
        shared = self._backend_get_tenant_tokens(tid)
        if shared is not None:
            return shared
        with self._lock:
            return self._tenant_tokens.get(tid, (0, 0.0))[0]

    # ── Token reservation (COST-GOVERNOR-1 phase 4) ───────────────────────────
    #
    # ★ Admission and accounting are ONE operation. A read-then-compare lets N concurrent
    # callers all pass the same check; a reservation pre-fills the counter it will be checked
    # against, so the (N+1)th caller sees the N reservations already there. On Redis that is
    # INCRBY-then-compare with a compensating DECRBY on refusal; in memory it is one critical
    # section. The reservation is an ESTIMATE and never becomes the record: the meter records
    # the ACTUAL inside the call, and `release_*` takes the estimate back out.

    def reserve_tokens(self, eu_id: str, count: int, cap: int, *, tenant_id: str | None = None) -> bool:
        """Pre-fill `count` tokens onto a unit if that keeps it within `cap`. False = refused."""
        eid = str(eu_id)
        delta = int(count)
        if cap <= 0 or delta <= 0:
            return True
        shared = self._backend_add_tokens(eid, delta)
        if shared is not None:
            if shared > cap:
                self._backend_add_tokens(eid, -delta)
                return False
            with self._lock:
                if eid not in self._usage:
                    self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id=str(tenant_id or ""))
                self._usage[eid].tokens += delta
            return True
        with self._lock:
            if eid not in self._usage:
                self._usage[eid] = UsageSnapshot(eu_id=eid, tenant_id=str(tenant_id or ""))
            snap = self._usage[eid]
            if snap.tokens + delta > cap:
                return False
            snap.tokens += delta
            return True

    def release_tokens(self, eu_id: str, count: int) -> None:
        """Take a reservation back out (the actual has been recorded by the meter, or the call never happened)."""
        eid = str(eu_id)
        delta = int(count)
        if delta <= 0:
            return
        with self._lock:
            snap = self._usage.get(eid)
            if snap is not None:
                snap.tokens = max(0, snap.tokens - delta)
        self._backend_add_tokens(eid, -delta)

    def reserve_tenant_tokens(self, tenant_id: str, count: int, cap: int) -> bool:
        """Pre-fill `count` tokens onto a tenant's window if that keeps it within `cap`."""
        tid = str(tenant_id)
        delta = int(count)
        if cap <= 0 or delta <= 0 or not tid:
            return True
        shared = self._backend_add_tenant_tokens(tid, delta)
        if shared is not None:
            if shared > cap:
                self._backend_add_tenant_tokens(tid, -delta)
                return False
            with self._lock:
                current, started = self._tenant_tokens.get(tid, (0, time.monotonic()))
                self._tenant_tokens[tid] = (current + delta, started)
            return True
        with self._lock:
            current, started = self._tenant_tokens.get(tid, (0, time.monotonic()))
            if current + delta > cap:
                return False
            self._tenant_tokens[tid] = (current + delta, started)
            return True

    def release_tenant_tokens(self, tenant_id: str, count: int) -> None:
        tid = str(tenant_id)
        delta = int(count)
        if delta <= 0 or not tid:
            return
        with self._lock:
            current, started = self._tenant_tokens.get(tid, (0, time.monotonic()))
            self._tenant_tokens[tid] = (max(0, current - delta), started)
        self._backend_add_tenant_tokens(tid, -delta)

    def record_usage(self, eu_id: str, usage: dict) -> None:
        """Accumulate resource usage for an ExecutionUnit.

        Deltas are ADDED to existing counters except memory_bytes which
        tracks the maximum observed value (high-water mark).

        Args:
            eu_id:  ExecutionUnit ID.
            usage:  Dict with keys: wall_time_ms (int), memory_bytes (int),
                    syscall_count (int), tokens (int).  Missing keys are treated as 0.
        """
        self.record_cpu(eu_id, int(usage.get("wall_time_ms", 0)))
        self.record_memory(eu_id, int(usage.get("memory_bytes", 0)))
        self.record_syscall(eu_id, int(usage.get("syscall_count", 0)))
        self.record_tokens(eu_id, int(usage.get("tokens", 0)))

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_usage(self, eu_id: str) -> dict:
        """Return the current UsageSnapshot for *eu_id* as a dict.

        Returns an empty usage dict if *eu_id* is unknown.
        """
        with self._lock:
            snap = self._usage.get(str(eu_id))
            if snap is None:
                return {
                    "eu_id": eu_id, "wall_time_ms": 0, "memory_bytes": 0, "syscall_count": 0, "tokens": 0,
                }
            return snap.to_dict()

    def get_tenant_active(self, tenant_id: str) -> int:
        """Return the number of active executions for *tenant_id*."""
        tid = str(tenant_id)
        redis_client = self._get_redis()
        if redis_client is not None:
            key = self._concurrency_key(tid)
            try:
                return int(redis_client.get(key) or 0)
            except Exception as exc:
                self._drop_redis_client(
                    "[resource_manager] Redis get failed, using local: %s",
                    exc,
                )
        with self._lock:
            return self._active_counts.get(tid, 0)

    def get_tenant_summary(self, tenant_id: str) -> dict:
        """Return a summary of resource usage for all EUs belonging to *tenant_id*.

        Aggregates across all known UsageSnapshots for the tenant.
        """
        tid = str(tenant_id)
        active_executions = self.get_tenant_active(tid)
        with self._lock:
            snaps = [s for s in self._usage.values() if s.tenant_id == tid]
            return {
                "tenant_id": tid,
                "active_executions": active_executions,
                "execution_count": len(snaps),
                "total_wall_time_ms": sum(s.wall_time_ms for s in snaps),
                "peak_memory_bytes": max((s.memory_bytes for s in snaps), default=0),
                "total_syscalls": sum(s.syscall_count for s in snaps),
                # Live snapshots only; reaped units are gone. The rolling window below is the
                # number a tenant budget would read.
                "total_tokens": sum(s.tokens for s in snaps),
                "window_tokens": self._tenant_tokens.get(tid, (0, 0.0))[0],
                "quota_limits": {
                    "max_wall_time_ms": MAX_WALL_TIME_MS,
                    "max_memory_bytes": MAX_MEMORY_BYTES,
                    "max_syscalls_per_execution": MAX_SYSCALLS_PER_EXECUTION,
                    "max_concurrent_executions": MAX_CONCURRENT_PER_TENANT,
                    "max_tokens_per_execution": MAX_TOKENS_PER_EXECUTION,
                    "max_tokens_per_tenant_window": MAX_TOKENS_PER_TENANT_WINDOW,
                },
            }

    def purge_eu(self, eu_id: str) -> None:
        """Remove the UsageSnapshot for *eu_id* from memory.

        Safe to call at any point after ``mark_completed()``.
        """
        eid = str(eu_id)
        with self._lock:
            self._usage.pop(eid, None)
            self._eu_tenant.pop(eid, None)
        self._backend_delete_eu(eid)

    def reset(self) -> None:
        """Clear ALL in-memory state.

        For use in tests only. Never call in production.
        """
        with self._lock:
            self._usage.clear()
            self._active_counts.clear()
            self._eu_tenant.clear()
            self._pending_purge.clear()
            self._tenant_tokens.clear()
            self._last_eviction_sweep = time.monotonic()


# ── Module-level singleton ────────────────────────────────────────────────────

_RESOURCE_MANAGER: ResourceManager | None = None
_RM_LOCK = threading.Lock()


def get_resource_manager() -> ResourceManager:
    """Return the module-level ResourceManager singleton.

    Thread-safe double-checked locking.  Use this in all production code.
    Tests should call ``ResourceManager()`` directly for isolation.
    """
    global _RESOURCE_MANAGER
    if _RESOURCE_MANAGER is None:
        with _RM_LOCK:
            if _RESOURCE_MANAGER is None:
                _RESOURCE_MANAGER = ResourceManager()
    return _RESOURCE_MANAGER
