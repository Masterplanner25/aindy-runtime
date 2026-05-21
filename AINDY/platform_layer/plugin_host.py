from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PLUGIN_HOST_START_TIMEOUT_SECONDS = 10.0
DEFAULT_PLUGIN_HOST_EXECUTE_TIMEOUT_SECONDS = 30.0
DEFAULT_PLUGIN_HOST_HEARTBEAT_TIMEOUT_SECONDS = 15.0
DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_MAX_SECONDS = 60.0
DEFAULT_PLUGIN_HOST_CONSECUTIVE_FAILURE_QUARANTINE_THRESHOLD = 4
DEFAULT_PLUGIN_HOST_TIMEOUT_QUARANTINE_THRESHOLD = 2
DEFAULT_PLUGIN_HOST_CONTRACT_VIOLATION_QUARANTINE_THRESHOLD = 2
DEFAULT_PLUGIN_HOST_QUARANTINE_SECONDS = 300.0
PLUGIN_HOST_PROTOCOL_VERSION = "2026-05-20"

_HOSTS_LOCK = threading.RLock()
_HOSTS: dict[str, "PluginHostRecord"] = {}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_child_env() -> dict[str, str]:
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "DATABASE_URL",
            "AINDY_ALLOW_SQLITE",
            "ENV",
            "TESTING",
            "TEST_MODE",
            "AINDY_SKIP_MONGO_PING",
            "SKIP_MONGO_PING",
            "AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS",
        }
    }
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    return child_env


def _host_process_args() -> list[str]:
    return [sys.executable, "-m", "AINDY.platform_layer.extension_worker", "--host"]


@dataclass
class PluginHostRecord:
    name: str
    handler: str
    plugin_root: str
    owner_class: str
    granted_capabilities: list[str]
    resource_access: dict[str, Any] = field(default_factory=dict)
    heartbeat_timeout_seconds: float = DEFAULT_PLUGIN_HOST_HEARTBEAT_TIMEOUT_SECONDS
    process: subprocess.Popen[str] | None = None
    state: str = "stopped"
    launch_count: int = 0
    restart_count: int = 0
    request_count: int = 0
    success_count: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    timeout_failures: int = 0
    contract_violations: int = 0
    crash_failures: int = 0
    last_error: str | None = None
    last_failure_kind: str | None = None
    last_exit_code: int | None = None
    last_start_at: str | None = None
    last_stop_at: str | None = None
    last_heartbeat_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    circuit_open_until: str | None = None
    quarantined_until: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    recent_failures: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=10),
    )
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _response_queue: queue.Queue[str] = field(default_factory=queue.Queue, repr=False)
    _stderr_lines: deque[str] = field(default_factory=lambda: deque(maxlen=20), repr=False)
    _stdout_thread: threading.Thread | None = field(default=None, repr=False)
    _stderr_thread: threading.Thread | None = field(default=None, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            process = self.process
            pid = process.pid if process is not None and process.poll() is None else None
            running = pid is not None
            heartbeat_healthy = self._heartbeat_fresh_unlocked()
            if self._quarantine_active_unlocked():
                lifecycle_state = "quarantined"
            elif self._circuit_open_unlocked():
                lifecycle_state = "backoff"
            elif self.state == "running" and running and not heartbeat_healthy:
                lifecycle_state = "heartbeat_lost"
            elif self.state in {"crashed", "failed", "heartbeat_lost"}:
                lifecycle_state = self.state
            elif running:
                lifecycle_state = "running"
            else:
                lifecycle_state = self.state
            return {
                "name": self.name,
                "handler": self.handler,
                "plugin_root": self.plugin_root,
                "owner_class": self.owner_class,
                "granted_capabilities": list(self.granted_capabilities),
                "resource_access": dict(self.resource_access),
                "protocol_version": PLUGIN_HOST_PROTOCOL_VERSION,
                "lifecycle_state": lifecycle_state,
                "healthy": running and heartbeat_healthy and lifecycle_state == "running",
                "pid": pid,
                "launch_count": self.launch_count,
                "restart_count": self.restart_count,
                "request_count": self.request_count,
                "success_count": self.success_count,
                "total_failures": self.total_failures,
                "consecutive_failures": self.consecutive_failures,
                "timeout_failures": self.timeout_failures,
                "contract_violations": self.contract_violations,
                "crash_failures": self.crash_failures,
                "last_error": self.last_error,
                "last_failure_kind": self.last_failure_kind,
                "last_exit_code": self.last_exit_code,
                "last_start_at": self.last_start_at,
                "last_stop_at": self.last_stop_at,
                "last_heartbeat_at": self.last_heartbeat_at,
                "last_success_at": self.last_success_at,
                "last_failure_at": self.last_failure_at,
                "circuit_open_until": self.circuit_open_until,
                "quarantined_until": self.quarantined_until,
                "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
                "recent_failures": list(self.recent_failures),
                "provenance": dict(self.provenance),
            }

    def _heartbeat_fresh_unlocked(self) -> bool:
        if not self.last_heartbeat_at:
            return False
        try:
            last = datetime.fromisoformat(self.last_heartbeat_at)
        except Exception:
            return False
        return (datetime.now(timezone.utc) - last).total_seconds() <= self.heartbeat_timeout_seconds

    def _circuit_open_unlocked(self) -> bool:
        if not self.circuit_open_until:
            return False
        try:
            until = datetime.fromisoformat(self.circuit_open_until)
        except Exception:
            return False
        return datetime.now(timezone.utc) < until

    def _quarantine_active_unlocked(self) -> bool:
        if not self.quarantined_until:
            return False
        try:
            until = datetime.fromisoformat(self.quarantined_until)
        except Exception:
            return False
        return datetime.now(timezone.utc) < until


def _append_stderr_line(record: PluginHostRecord, line: str) -> None:
    cleaned = str(line or "").rstrip()
    if cleaned:
        record._stderr_lines.append(cleaned)


def _future_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))).isoformat()


def _pipe_reader(record: PluginHostRecord, pipe, *, target: str) -> None:
    try:
        while True:
            line = pipe.readline()
            if not line:
                break
            if target == "stdout":
                record._response_queue.put(str(line).rstrip("\r\n"))
            else:
                _append_stderr_line(record, str(line))
    except Exception as exc:
        if target == "stderr":
            _append_stderr_line(record, f"reader error: {exc}")


def _mark_failure(
    record: PluginHostRecord,
    *,
    state: str,
    error: str,
    kind: str = "runtime_failure",
    exit_code: int | None = None,
) -> None:
    record.state = state
    record.last_error = error
    record.last_failure_kind = kind
    record.last_failure_at = _utcnow_iso()
    record.total_failures += 1
    record.consecutive_failures += 1
    if kind == "timeout":
        record.timeout_failures += 1
    if kind == "contract_violation":
        record.contract_violations += 1
    if kind == "crash":
        record.crash_failures += 1
    backoff_seconds = min(
        DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_BASE_SECONDS
        * (2 ** max(record.consecutive_failures - 1, 0)),
        DEFAULT_PLUGIN_HOST_FAILURE_BACKOFF_MAX_SECONDS,
    )
    record.circuit_open_until = _future_iso(backoff_seconds)
    record.recent_failures.append(
        {
            "at": record.last_failure_at,
            "kind": kind,
            "error": error,
            "exit_code": exit_code,
            "backoff_seconds": backoff_seconds,
        }
    )
    if (
        record.consecutive_failures >= DEFAULT_PLUGIN_HOST_CONSECUTIVE_FAILURE_QUARANTINE_THRESHOLD
        or record.timeout_failures >= DEFAULT_PLUGIN_HOST_TIMEOUT_QUARANTINE_THRESHOLD
        or record.contract_violations >= DEFAULT_PLUGIN_HOST_CONTRACT_VIOLATION_QUARANTINE_THRESHOLD
    ):
        record.quarantined_until = _future_iso(DEFAULT_PLUGIN_HOST_QUARANTINE_SECONDS)
        record.state = "quarantined"
    if exit_code is not None:
        record.last_exit_code = exit_code


def _mark_success(record: PluginHostRecord) -> None:
    record.success_count += 1
    record.request_count += 1
    record.last_success_at = _utcnow_iso()
    record.last_heartbeat_at = record.last_success_at
    record.state = "running"
    record.consecutive_failures = 0
    record.timeout_failures = 0
    record.last_error = None
    record.last_failure_kind = None
    record.circuit_open_until = None
    record.quarantined_until = None


def _classify_failure(*, error: str, crashed: bool) -> str:
    cleaned = str(error or "").lower()
    if crashed:
        return "crash"
    if "timed out" in cleaned:
        return "timeout"
    if "invalid status" in cleaned or "non-dict" in cleaned or "must be a dict" in cleaned:
        return "contract_violation"
    return "runtime_failure"


def _assert_host_not_quarantined(record: PluginHostRecord) -> None:
    if record._quarantine_active_unlocked():
        raise RuntimeError(
            f"plugin host {record.name!r} is quarantined until {record.quarantined_until}"
        )


def _assert_host_not_in_backoff(record: PluginHostRecord) -> None:
    if record._circuit_open_unlocked():
        raise RuntimeError(
            f"plugin host {record.name!r} is in restart backoff until {record.circuit_open_until}"
        )


def _spawn_host_process(record: PluginHostRecord) -> None:
    process = subprocess.Popen(
        _host_process_args(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=_build_child_env(),
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("plugin host subprocess did not expose stdio pipes")

    record.process = process
    record._response_queue = queue.Queue()
    record._stderr_lines = deque(maxlen=20)
    record._stdout_thread = threading.Thread(
        target=_pipe_reader,
        args=(record, process.stdout),
        kwargs={"target": "stdout"},
        daemon=True,
        name=f"plugin-host-stdout-{record.name}",
    )
    record._stderr_thread = threading.Thread(
        target=_pipe_reader,
        args=(record, process.stderr),
        kwargs={"target": "stderr"},
        daemon=True,
        name=f"plugin-host-stderr-{record.name}",
    )
    record._stdout_thread.start()
    record._stderr_thread.start()
    record.launch_count += 1
    record.state = "starting"
    record.last_start_at = _utcnow_iso()
    record.last_stop_at = None
    record.last_error = None
    record.last_exit_code = None


def _process_stderr_excerpt(record: PluginHostRecord) -> str:
    if not record._stderr_lines:
        return ""
    return " | stderr=" + " | ".join(record._stderr_lines)


def _wait_for_response(
    record: PluginHostRecord,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining == 0.0:
            raise TimeoutError("plugin host command timed out")
        try:
            raw = record._response_queue.get(timeout=min(0.25, remaining))
        except queue.Empty as exc:
            process = record.process
            if process is not None and process.poll() is not None:
                raise RuntimeError(
                    f"plugin host exited with code {process.returncode}{_process_stderr_excerpt(record)}"
                ) from exc
            continue
        try:
            return json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"plugin host returned invalid JSON: {exc}") from exc


def _send_command(
    record: PluginHostRecord,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    process = record.process
    if process is None or process.stdin is None:
        raise RuntimeError("plugin host process is not running")
    if process.poll() is not None:
        raise RuntimeError(
            f"plugin host exited with code {process.returncode}{_process_stderr_excerpt(record)}"
        )
    try:
        process.stdin.write(json.dumps(payload, default=str) + "\n")
        process.stdin.flush()
    except Exception as exc:
        raise RuntimeError(f"failed to send plugin host command: {exc}") from exc
    return _wait_for_response(record, timeout_seconds=timeout_seconds)


def _prepare_context(
    *,
    extension_name: str,
    owner_class: str,
    granted_capabilities: list[str],
    runtime_context: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(runtime_context or {})
    base["extension_name"] = extension_name
    base["owner_class"] = owner_class
    base["granted_capabilities"] = list(granted_capabilities)
    base["node_name"] = extension_name
    return base


def _start_record(record: PluginHostRecord, *, runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    _assert_host_not_quarantined(record)
    _spawn_host_process(record)
    response = _send_command(
        record,
        {
            "command": "start",
            "handler": record.handler,
            "plugin_root": record.plugin_root,
            "context": _prepare_context(
                extension_name=record.name,
                owner_class=record.owner_class,
                granted_capabilities=record.granted_capabilities,
                runtime_context=runtime_context,
            ),
        },
        timeout_seconds=DEFAULT_PLUGIN_HOST_START_TIMEOUT_SECONDS,
    )
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error") or "plugin host start failed"))
    merged_provenance = dict(response.get("provenance") or {})
    merged_provenance.update(record.provenance)
    record.provenance = merged_provenance
    record.state = "running"
    record.last_heartbeat_at = _utcnow_iso()
    return record.snapshot()


def _terminate_record_process(record: PluginHostRecord, *, force_kill: bool = False) -> None:
    process = record.process
    if process is None:
        return
    try:
        if not force_kill and process.poll() is None:
            _send_command(
                record,
                {"command": "shutdown"},
                timeout_seconds=5.0,
            )
    except Exception:
        pass
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=3)
        except Exception:
            pass
    finally:
        record.last_exit_code = process.returncode
        record.process = None
        record.last_stop_at = _utcnow_iso()
        if record.state not in {"failed", "crashed", "quarantined", "heartbeat_lost"}:
            record.state = "stopped"


def start_plugin_host(
    *,
    name: str,
    handler: str,
    plugin_root: str | Path,
    owner_class: str,
    granted_capabilities: list[str],
    resource_access: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    force_restart: bool = False,
) -> dict[str, Any]:
    plugin_root_str = str(plugin_root)
    with _HOSTS_LOCK:
        record = _HOSTS.get(name)
        if record is None:
            record = PluginHostRecord(
                name=name,
                handler=handler,
                plugin_root=plugin_root_str,
                owner_class=owner_class,
                granted_capabilities=list(granted_capabilities),
                resource_access=dict(resource_access or {}),
                provenance=dict(provenance or {}),
            )
            _HOSTS[name] = record
    with record._lock:
        if force_restart and not record._quarantine_active_unlocked():
            record.circuit_open_until = None
        _assert_host_not_quarantined(record)
        config_changed = (
            record.handler != handler
            or record.plugin_root != plugin_root_str
            or record.owner_class != owner_class
            or list(record.granted_capabilities) != list(granted_capabilities)
            or dict(record.resource_access) != dict(resource_access or {})
            or dict(record.provenance) != dict(provenance or {})
        )
        if config_changed:
            record.handler = handler
            record.plugin_root = plugin_root_str
            record.owner_class = owner_class
            record.granted_capabilities = list(granted_capabilities)
            record.resource_access = dict(resource_access or {})
            record.provenance = dict(provenance or {})
            force_restart = True
        if force_restart and record.process is not None:
            record.restart_count += 1
            _terminate_record_process(record)
        if record.process is None or record.process.poll() is not None:
            if record.process is not None and record.process.poll() is not None:
                _mark_failure(
                    record,
                    state="crashed",
                    error=f"plugin host exited with code {record.process.returncode}",
                    kind="crash",
                    exit_code=record.process.returncode,
                )
                record.restart_count += 1
            try:
                return _start_record(record, runtime_context=runtime_context)
            except Exception as exc:
                _mark_failure(record, state="failed", error=str(exc))
                _terminate_record_process(record, force_kill=True)
                raise
        return record.snapshot()


def heartbeat_plugin_host(name: str) -> dict[str, Any]:
    record = _HOSTS.get(name)
    if record is None:
        raise KeyError(f"plugin host {name!r} is not registered")
    with record._lock:
        if record._quarantine_active_unlocked() or record._circuit_open_unlocked():
            return record.snapshot()
        process = record.process
        if process is None:
            record.state = "stopped"
            return record.snapshot()
        if process.poll() is not None:
            _mark_failure(
                record,
                state="crashed",
                error=f"plugin host exited with code {process.returncode}",
                kind="crash",
                exit_code=process.returncode,
            )
            record.process = None
            return record.snapshot()
        try:
            response = _send_command(
                record,
                {"command": "heartbeat"},
                timeout_seconds=5.0,
            )
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error") or "heartbeat failed"))
            record.last_heartbeat_at = _utcnow_iso()
            record.state = "running"
        except Exception as exc:
            _mark_failure(record, state="heartbeat_lost", error=str(exc), kind=_classify_failure(error=str(exc), crashed=False))
        return record.snapshot()


def execute_plugin_host(
    *,
    name: str,
    state: dict[str, Any],
    runtime_context: dict[str, Any],
) -> dict[str, Any]:
    record = _HOSTS.get(name)
    if record is None:
        raise KeyError(f"plugin host {name!r} is not registered")
    with record._lock:
        _assert_host_not_quarantined(record)
        _assert_host_not_in_backoff(record)
        retries_remaining = 1
        while True:
            process = record.process
            if process is None or process.poll() is not None:
                if process is not None and process.poll() is not None:
                    _mark_failure(
                        record,
                        state="crashed",
                        error=f"plugin host exited with code {process.returncode}",
                        kind="crash",
                        exit_code=process.returncode,
                    )
                if retries_remaining < 0:
                    raise RuntimeError("plugin host is unavailable")
                record.restart_count += 1
                _start_record(record, runtime_context=runtime_context)
            try:
                response = _send_command(
                    record,
                    {
                        "command": "execute",
                        "state": state,
                        "context": _prepare_context(
                            extension_name=record.name,
                            owner_class=record.owner_class,
                            granted_capabilities=record.granted_capabilities,
                            runtime_context=runtime_context,
                        ),
                    },
                    timeout_seconds=DEFAULT_PLUGIN_HOST_EXECUTE_TIMEOUT_SECONDS,
                )
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "plugin host execution failed"))
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("plugin host returned a non-dict plugin result")
                _mark_success(record)
                return result
            except Exception as exc:
                process = record.process
                crashed = process is not None and process.poll() is not None
                failure_kind = _classify_failure(error=str(exc), crashed=crashed)
                _mark_failure(
                    record,
                    state="crashed" if crashed else "failed",
                    error=str(exc),
                    kind=failure_kind,
                    exit_code=process.returncode if crashed and process is not None else None,
                )
                if retries_remaining <= 0 or record._quarantine_active_unlocked():
                    raise
                retries_remaining -= 1
                record.restart_count += 1
                _terminate_record_process(record, force_kill=True)
                _start_record(record, runtime_context=runtime_context)


def restart_plugin_host(name: str, *, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
    record = _HOSTS.get(name)
    if record is None:
        raise KeyError(f"plugin host {name!r} is not registered")
    with record._lock:
        _assert_host_not_quarantined(record)
        record.circuit_open_until = None
        record.restart_count += 1
        _terminate_record_process(record)
        return _start_record(record, runtime_context=runtime_context)


def shutdown_plugin_host(name: str, *, remove: bool = False) -> bool:
    with _HOSTS_LOCK:
        record = _HOSTS.get(name)
    if record is None:
        return False
    with record._lock:
        _terminate_record_process(record)
    if remove:
        with _HOSTS_LOCK:
            _HOSTS.pop(name, None)
    return True


def get_plugin_host(name: str, *, probe: bool = False) -> dict[str, Any] | None:
    record = _HOSTS.get(name)
    if record is None:
        return None
    if probe:
        return heartbeat_plugin_host(name)
    return record.snapshot()


def plugin_host_inventory(*, probe: bool = False) -> dict[str, Any]:
    with _HOSTS_LOCK:
        names = sorted(_HOSTS)
    hosts: list[dict[str, Any]] = []
    for name in names:
        host = get_plugin_host(name, probe=probe)
        if host is not None:
            hosts.append(host)
    if any(host["lifecycle_state"] == "quarantined" for host in hosts):
        overall_status = "unavailable"
    elif any(host["lifecycle_state"] in {"crashed", "failed", "backoff"} for host in hosts):
        overall_status = "degraded"
    elif any(host["lifecycle_state"] == "heartbeat_lost" for host in hosts):
        overall_status = "degraded"
    else:
        overall_status = "ok"
    return {
        "present": bool(hosts),
        "protocol_version": PLUGIN_HOST_PROTOCOL_VERSION,
        "overall_status": overall_status,
        "host_count": len(hosts),
        "hosts": hosts,
        "operator_note": (
            "Third-party plugin nodes run behind a runtime-owned plugin host boundary. "
            "The runtime tracks host lifecycle and health separately from plugin payload results, "
            "and may apply restart backoff or quarantine after repeated failures."
        ),
    }


def shutdown_all_plugin_hosts() -> None:
    with _HOSTS_LOCK:
        names = list(_HOSTS)
    for name in names:
        try:
            shutdown_plugin_host(name, remove=True)
        except Exception:
            logger.exception("plugin host shutdown failed for %s", name)


def reset_plugin_hosts() -> None:
    shutdown_all_plugin_hosts()


atexit.register(shutdown_all_plugin_hosts)
