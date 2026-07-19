"""NODUS-WARMPOOL-1 Phase 1 — a single long-lived (warm) Nodus worker.

The default execution path spawns a fresh worker subprocess per Nodus execution, which
cold-starts the whole plugin stack (~12s on heavy app profiles) before the script runs.
This module keeps ONE worker process alive so that import/plugin-load cost is paid once
and amortized across every execution; each request still runs through
``nodus_worker.run_one``, which rebuilds all per-request state, so a reused process never
leaks state between runs.

Opt-in — ``AINDY_NODUS_WARM_POOL`` (default off). When off, ``warm_pool_enabled()`` is
false and the adapter uses the existing fresh-subprocess path unchanged. **Serial**
(Phase 1): one request at a time under a lock. A pool of N workers for concurrency is the
Phase 2 follow-up. Any warm-path failure is surfaced to the adapter, which falls back to a
fresh subprocess — so enabling the pool can never make execution *worse* than the default.

Protocol: length-prefixed JSON (4-byte big-endian length + UTF-8 body) over the worker's
stdin/stdout, matching ``nodus_worker.serve_forever``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WORKER_PATH = str(Path(__file__).parent / "nodus_worker.py")
_DEFAULT_MAX_REQUESTS = 500


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


class WorkerCrashed(RuntimeError):
    """The warm worker died or closed its pipe mid-request."""


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
    """Phase 1: a single warm worker guarded by a lock (serial), with respawn + recycle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._worker: Optional[WarmNodusWorker] = None

    def _ensure_worker(self) -> WarmNodusWorker:
        if self._worker is None or not self._worker.alive():
            self._worker = WarmNodusWorker()
            return self._worker
        limit = _max_requests()
        if limit and self._worker.requests >= limit:
            logger.info("[NodusWarmPool] recycling worker after %d requests", self._worker.requests)
            self._worker.kill()
            self._worker = WarmNodusWorker()
        return self._worker

    def execute(self, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        with self._lock:
            worker = self._ensure_worker()
            try:
                return worker.execute(payload, timeout_s=timeout_s)
            except (WorkerCrashed, TimeoutError):
                # A stuck/dead worker cannot serve the next request. Drop it (no retry — a
                # partially-run request must not double-execute side effects); the next call
                # spawns a fresh worker. The adapter maps the raised error to a failure/
                # timeout envelope or falls back to a fresh subprocess.
                worker.kill()
                self._worker = None
                raise

    def shutdown(self) -> None:
        with self._lock:
            if self._worker is not None:
                self._worker.kill()
                self._worker = None


_POOL: Optional[NodusWorkerPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> NodusWorkerPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = NodusWorkerPool()
    return _POOL


def reset_pool() -> None:
    """Test helper — tear down the singleton pool."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown()
        _POOL = None
