"""NODUS-WARMPOOL-1 Phase 1 — a single long-lived (warm) Nodus worker.

The default execution path spawns a fresh worker subprocess per Nodus execution, which
cold-starts the whole plugin stack (~12s on heavy app profiles) before the script runs.
This module keeps ONE worker process alive so that import/plugin-load cost is paid once
and amortized across every execution; each request still runs through
``nodus_worker.run_one``, which rebuilds all per-request state, so a reused process never
leaks state between runs.

Opt-in — ``AINDY_NODUS_WARM_POOL`` (default off). When off, ``warm_pool_enabled()`` is
false and the adapter uses the existing fresh-subprocess path unchanged. **Phase 2** — a
bounded pool of up to ``AINDY_NODUS_WARM_POOL_SIZE`` (default 4) workers, each serving one
request at a time, so up to N executions run concurrently. When all N are busy a caller
waits up to ``AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS`` (default 2000) for one to free, then the
pool raises :class:`PoolBusy` and the adapter spills the request to a fresh subprocess
(bounded backpressure). Any warm-path failure is surfaced to the adapter, which falls back
to a fresh subprocess — so enabling the pool can never make execution *worse* than the
default.

Protocol: length-prefixed JSON (4-byte big-endian length + UTF-8 body) over the worker's
stdin/stdout, matching ``nodus_worker.serve_forever``.
"""
from __future__ import annotations

import collections
import contextlib
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WORKER_PATH = str(Path(__file__).parent / "nodus_worker.py")
_DEFAULT_MAX_REQUESTS = 500
_DEFAULT_POOL_SIZE = 4
_DEFAULT_ACQUIRE_TIMEOUT_MS = 2000
_DEFAULT_PREWARM_TIMEOUT_MS = 120_000
_WARMUP_PAYLOAD = {"__warmup__": True}


def prewarm_enabled() -> bool:
    """NODUS-WARMPOOL-1 Phase 3 — eagerly warm the pool in the background on first use."""
    return os.getenv("AINDY_NODUS_WARM_PREWARM", "").strip().lower() in {"1", "true", "yes"}


def _prewarm_timeout_s() -> float:
    raw = os.getenv("AINDY_NODUS_WARM_PREWARM_TIMEOUT_MS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PREWARM_TIMEOUT_MS / 1000.0
    return max(1, value) / 1000.0


def warm_pool_enabled() -> bool:
    return os.getenv("AINDY_NODUS_WARM_POOL", "").strip().lower() in {"1", "true", "yes"}


def _max_requests() -> int:
    """Recycle a worker after this many requests (bounds per-process leaks). 0 = never."""
    raw = os.getenv("AINDY_NODUS_WARM_MAX_REQUESTS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_REQUESTS
    return value if value >= 0 else _DEFAULT_MAX_REQUESTS


def _pool_size() -> int:
    """Phase 2 — max concurrent warm workers (each serves one request at a time)."""
    raw = os.getenv("AINDY_NODUS_WARM_POOL_SIZE", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_POOL_SIZE
    return value if value >= 1 else _DEFAULT_POOL_SIZE


def _acquire_timeout_s() -> float:
    """How long a caller waits for a free warm worker before spilling to a fresh subprocess."""
    raw = os.getenv("AINDY_NODUS_WARM_ACQUIRE_TIMEOUT_MS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_ACQUIRE_TIMEOUT_MS / 1000.0
    return max(0, value) / 1000.0


class WorkerCrashed(RuntimeError):
    """The warm worker died or closed its pipe mid-request."""


class PoolBusy(RuntimeError):
    """All warm workers are busy and the pool is at capacity (caller should spill)."""


class WarmNodusWorker:
    """A single long-lived worker subprocess speaking length-prefixed JSON."""

    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, _WORKER_PATH, "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # worker uses its own log handlers; keep frames clean
            bufsize=0,
        )
        self.requests = 0

    def alive(self) -> bool:
        return self._proc.poll() is None

    def execute(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        """Send one request and return the parsed result dict.

        Raises ``TimeoutError`` if the worker does not respond within ``timeout_s`` (the
        script overran or the worker is stuck — the process is unusable afterward) or
        ``WorkerCrashed`` if the worker died. The caller (pool) drops the worker in both
        cases so it is never reused after a fault.
        """
        data = json.dumps(payload).encode("utf-8")
        try:
            self._proc.stdin.write(struct.pack(">I", len(data)))
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise WorkerCrashed(f"warm worker stdin write failed: {exc}") from exc

        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def _read() -> None:
            try:
                header = self._read_exact(4)
                if header is None:
                    raise WorkerCrashed("warm worker closed the pipe (EOF)")
                (length,) = struct.unpack(">I", header)
                body = self._read_exact(length)
                if body is None:
                    raise WorkerCrashed("warm worker sent a truncated response")
                result_box["r"] = json.loads(body.decode("utf-8"))
            except BaseException as exc:  # noqa: BLE001 — surfaced to the caller
                error_box["e"] = exc

        # A reader thread + join(timeout) gives a cross-platform read timeout (select on
        # pipes does not work on Windows). On timeout the process is left for the pool to kill.
        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout_s)
        if reader.is_alive():
            raise TimeoutError(f"warm worker exceeded {timeout_s}s")
        if "e" in error_box:
            raise error_box["e"]
        self.requests += 1
        return result_box["r"]

    def _read_exact(self, n: int) -> Optional[bytes]:
        buf = bytearray()
        out = self._proc.stdout
        while len(buf) < n:
            chunk = out.read(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def kill(self) -> None:
        with contextlib.suppress(Exception):
            self._proc.kill()
        with contextlib.suppress(Exception):
            self._proc.wait(timeout=2)


class NodusWorkerPool:
    """Phase 2 — a bounded pool of warm workers for concurrency.

    Up to ``_pool_size()`` long-lived workers, each serving one request at a time
    (checked out under a condition, returned to an idle set on completion). A caller
    gets an idle worker, or grows the pool if there is room, or waits up to
    ``_acquire_timeout_s()`` for one to free; past that the pool raises :class:`PoolBusy`
    and the adapter spills the request to a fresh subprocess (bounded backpressure — the
    warm path never blocks unboundedly). A faulted (crashed/timed-out) worker is dropped,
    not returned; an over-``_max_requests()`` worker is recycled on return.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._idle: list[WarmNodusWorker] = []
        self._size = 0  # total live workers (idle + checked-out)
        self._closing = False  # Phase 3 — set by drain(); rejects new checkouts
        # Seed the known events at 0 so stats() always reports every counter.
        self._stats: "collections.Counter[str]" = collections.Counter(
            {"spawned": 0, "recycled": 0, "crashed": 0, "spilled": 0, "served": 0}
        )

    # ── Phase 3: metrics ─────────────────────────────────────────────────────
    def _metric(self, event: str, n: int = 1) -> None:
        self._stats[event] += n
        try:
            from AINDY.platform_layer.metrics import nodus_warm_pool_events_total

            nodus_warm_pool_events_total.labels(event=event).inc(n)
        except Exception:
            pass

    def _emit_gauges(self) -> None:
        """Update the worker-count gauges. Caller holds ``self._cond``."""
        try:
            from AINDY.platform_layer.metrics import nodus_warm_pool_workers

            idle = len(self._idle)
            nodus_warm_pool_workers.labels(state="total").set(self._size)
            nodus_warm_pool_workers.labels(state="idle").set(idle)
            nodus_warm_pool_workers.labels(state="busy").set(self._size - idle)
        except Exception:
            pass

    def stats(self) -> dict[str, int]:
        """Point-in-time pool counters + worker counts (Phase 3 observability)."""
        with self._cond:
            idle = len(self._idle)
            return {**dict(self._stats), "size": self._size, "idle": idle, "busy": self._size - idle}

    def _checkout(self) -> WarmNodusWorker:
        deadline = time.monotonic() + _acquire_timeout_s()
        limit = _max_requests()
        with self._cond:
            while True:
                if self._closing:
                    self._metric("spilled")
                    raise PoolBusy("warm pool is draining")
                # Hand out a healthy, non-exhausted idle worker; reap the rest.
                while self._idle:
                    worker = self._idle.pop()
                    if worker.alive() and (not limit or worker.requests < limit):
                        return worker
                    worker.kill()
                    self._size -= 1
                # Grow the pool if there is headroom.
                if self._size < _pool_size():
                    worker = WarmNodusWorker()
                    self._size += 1
                    self._metric("spawned")
                    self._emit_gauges()
                    return worker
                # Saturated — wait briefly for a release, else signal a spill.
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cond.wait(remaining):
                    self._metric("spilled")
                    raise PoolBusy(f"all {_pool_size()} warm workers busy")

    def _checkin(self, worker: WarmNodusWorker, *, healthy: bool) -> None:
        limit = _max_requests()
        with self._cond:
            if healthy and worker.alive() and (not limit or worker.requests < limit):
                self._idle.append(worker)
            else:
                if healthy and limit and worker.requests >= limit:
                    logger.info("[NodusWarmPool] recycling worker after %d requests", worker.requests)
                    self._metric("recycled")
                elif not healthy:
                    self._metric("crashed")
                worker.kill()
                self._size -= 1
            self._emit_gauges()
            self._cond.notify()

    def execute(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        worker = self._checkout()
        try:
            result = worker.execute(payload, timeout_s=timeout_s)
        except (WorkerCrashed, TimeoutError):
            # A stuck/dead worker cannot be reused; drop it (no retry — a partially-run
            # request must not double-execute side effects). The adapter maps the raised
            # error to a failure/timeout envelope or falls back to a fresh subprocess.
            self._checkin(worker, healthy=False)
            raise
        self._checkin(worker, healthy=True)
        self._metric("served")
        return result

    # ── Phase 3: eager pre-warm ──────────────────────────────────────────────
    def prewarm(self, count: Optional[int] = None) -> int:
        """Spawn up to ``count`` (default pool size) workers and pay their plugin-stack
        load ahead of real traffic, so the first executions hit hot workers. Returns the
        number successfully warmed. Best-effort — a failed warm-up just spawns fewer."""
        target = count if count is not None else _pool_size()
        warmed = 0
        for _ in range(target):
            with self._cond:
                if self._closing or self._size >= _pool_size():
                    break
                worker = WarmNodusWorker()
                self._size += 1
                self._metric("spawned")
                self._emit_gauges()
            try:
                worker.execute(_WARMUP_PAYLOAD, timeout_s=_prewarm_timeout_s())
                healthy = worker.alive()
            except Exception as exc:
                logger.warning("[NodusWarmPool] prewarm worker failed: %s", exc)
                healthy = False
            with self._cond:
                if healthy:
                    self._idle.append(worker)
                    warmed += 1
                else:
                    worker.kill()
                    self._size -= 1
                self._emit_gauges()
                self._cond.notify()
        if warmed:
            logger.info("[NodusWarmPool] pre-warmed %d worker(s)", warmed)
        return warmed

    # ── Phase 3: graceful drain ──────────────────────────────────────────────
    def drain(self, timeout_s: float = 30.0) -> None:
        """Stop handing out workers, wait up to ``timeout_s`` for in-flight requests to
        finish, then kill all workers. New checkouts raise :class:`PoolBusy` (spill)."""
        deadline = time.monotonic() + timeout_s
        with self._cond:
            self._closing = True
            while (self._size - len(self._idle)) > 0:  # busy workers remain
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cond.wait(remaining):
                    break
            for worker in self._idle:
                worker.kill()
            self._size -= len(self._idle)
            self._idle.clear()
            self._emit_gauges()
            self._cond.notify_all()

    def shutdown(self) -> None:
        with self._cond:
            for worker in self._idle:
                worker.kill()
            self._size -= len(self._idle)
            self._idle.clear()
            self._emit_gauges()
            self._cond.notify_all()


_POOL: Optional[NodusWorkerPool] = None
_POOL_LOCK = threading.Lock()


def _background_prewarm(pool: NodusWorkerPool) -> None:
    try:
        pool.prewarm()
    except Exception as exc:  # pre-warm is best-effort; never crash the app
        logger.warning("[NodusWarmPool] background prewarm failed: %s", exc)


def get_pool() -> NodusWorkerPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = NodusWorkerPool()
                # Phase 3 — eager pre-warm in the background so the first real executions
                # hit hot workers (never blocks the caller; opt-in via AINDY_NODUS_WARM_PREWARM).
                if prewarm_enabled():
                    threading.Thread(
                        target=_background_prewarm, args=(_POOL,), daemon=True
                    ).start()
    return _POOL


def reset_pool() -> None:
    """Test helper — tear down the singleton pool."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown()
        _POOL = None
