from __future__ import annotations

import atexit
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from AINDY.platform_layer.sandbox_runner import (
    RUNNER_INSECURE_DEV_SUBPROCESS,
    RUNNER_STRONG_SANDBOX_VM,
    SANDBOX_RUNNER_INTERFACE_VERSION,
    SandboxRunner,
    create_sandbox_runner,
    list_supported_sandbox_runners,
    resolve_sandbox_runner_type,
)
from AINDY.platform_layer.sandbox_certification import sandbox_certification_profile
from AINDY.platform_layer.deployment_contract import (
    get_api_runtime_state,
    hostile_third_party_attestation_violations,
    hostile_third_party_profile_required,
    resolve_api_deployment_profile,
    validate_external_third_party_plugin_runtime_policy,
)

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
RUNTIME_API_CHANNEL_TTL_SECONDS = 30.0

_HOSTS_LOCK = threading.RLock()
_HOSTS: dict[str, "PluginHostRecord"] = {}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PluginHostRecord:
    name: str
    handler: str
    plugin_root: str
    owner_class: str
    granted_capabilities: list[str]
    resource_access: dict[str, Any] = field(default_factory=dict)
    runner_type: str = RUNNER_INSECURE_DEV_SUBPROCESS
    sandbox_instance_id: str = field(default_factory=lambda: secrets.token_hex(12))
    heartbeat_timeout_seconds: float = DEFAULT_PLUGIN_HOST_HEARTBEAT_TIMEOUT_SECONDS
    runner: SandboxRunner | None = None
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

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            runner = self.runner
            pid = runner.pid() if runner is not None else None
            running = pid is not None
            heartbeat_healthy = self._heartbeat_fresh_unlocked()
            runner_metadata = (
                dict(runner.metadata()) if runner is not None else _runner_metadata(self.runner_type)
            )
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
                "resource_limits": (
                    dict(runner_metadata.get("resource_limits") or {})
                ),
                "protocol_version": PLUGIN_HOST_PROTOCOL_VERSION,
                "runner_type": self.runner_type,
                "runner": runner_metadata,
                "sandbox_attestation": _host_sandbox_attestation(
                    runner_type=self.runner_type,
                    runner_metadata=runner_metadata,
                    resource_access=dict(self.resource_access),
                    provenance=dict(self.provenance),
                ),
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
    _ = record
    _ = line


def _future_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))).isoformat()


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


def _prepare_context(
    *,
    extension_name: str,
    owner_class: str,
    granted_capabilities: list[str],
    sandbox_instance_id: str,
    runner_type: str,
    runtime_context: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(runtime_context or {})
    channel_id = secrets.token_hex(8)
    channel_nonce = secrets.token_hex(12)
    issued_at = time.time()
    expires_at = issued_at + RUNTIME_API_CHANNEL_TTL_SECONDS
    plugin_context = {
        "user_id": str(base.get("user_id") or ""),
        "run_id": str(base.get("run_id") or base.get("trace_id") or ""),
        "trace_id": str(base.get("trace_id") or base.get("run_id") or ""),
        "workflow_type": str(base.get("workflow_type") or ""),
        "flow_name": str(base.get("flow_name") or ""),
        "extension_name": extension_name,
        "owner_class": owner_class,
        "granted_capabilities": list(granted_capabilities),
        "node_name": extension_name,
        "runtime_api": {
            "channel_type": "worker-authenticated-rpc",
            "channel_version": "2026-05-22",
            "runtime_channel_id": channel_id,
            "sandbox_instance_id": sandbox_instance_id,
            "expires_at": expires_at,
        },
    }
    runtime_api_auth = dict(plugin_context)
    runtime_api_auth["auth_version"] = "2026-05-22"
    runtime_api_auth["runtime_channel_id"] = channel_id
    runtime_api_auth["runtime_channel_token"] = secrets.token_hex(16)
    runtime_api_auth["runtime_channel_nonce"] = channel_nonce
    runtime_api_auth["issued_at"] = issued_at
    runtime_api_auth["expires_at"] = expires_at
    runtime_api_auth["sandbox_instance_id"] = sandbox_instance_id
    runtime_api_auth["runner_type"] = runner_type
    return {
        "plugin_context": plugin_context,
        "runtime_api_auth": runtime_api_auth,
    }


def _runner_metadata(runner_type: str) -> dict[str, Any]:
    runner = create_sandbox_runner(runner_type)
    return dict(runner.metadata())


def _sandbox_isolation_class(*, runner_type: str, runner_metadata: dict[str, Any]) -> str:
    if runner_type == RUNNER_INSECURE_DEV_SUBPROCESS:
        return "insecure-dev-subprocess"
    if runner_type == RUNNER_STRONG_SANDBOX_VM:
        hardening_controls = dict(runner_metadata.get("hardening_controls") or {})
        resource_limits = dict(runner_metadata.get("resource_limits") or {})
        if hardening_controls.get("active_controls") and resource_limits.get("enforcement") == "sandbox-runtime-hard-limits":
            return "strong-sandbox-vm"
        return "strong-sandbox-vm-unavailable"
    kernel_controls = dict(runner_metadata.get("kernel_controls") or {})
    resource_limits = dict(runner_metadata.get("resource_limits") or {})
    if kernel_controls.get("active_controls") or resource_limits.get("enforcement") == "container-runtime-hard-limits":
        return "containerized-hardened-sandbox"
    return "containerized-sandbox"


def _host_sandbox_attestation(
    *,
    runner_type: str,
    runner_metadata: dict[str, Any],
    resource_access: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    kernel_controls = dict(runner_metadata.get("kernel_controls") or {})
    hardening_controls = dict(runner_metadata.get("hardening_controls") or {})
    resource_limits = dict(runner_metadata.get("resource_limits") or {})
    launch_attestation = dict(runner_metadata.get("launch_attestation") or {})
    network_policy = dict(resource_access.get("network") or {})
    filesystem_policy = dict(resource_access.get("filesystem") or {})
    provenance_status = {
        "extension_id": provenance.get("extension_id"),
        "version": provenance.get("version"),
        "source_type": provenance.get("source_type"),
        "verification": provenance.get("verification"),
        "integrity": dict(provenance.get("integrity") or {}),
    }
    runtime_identity = dict(runner_metadata.get("runtime_identity") or {})
    certification = sandbox_certification_profile(
        runner_type=runner_type,
        runner_metadata=runner_metadata,
    )
    mount_mode = dict(launch_attestation.get("mount_mode") or {})
    writable_temp = dict(launch_attestation.get("writable_temp") or {})
    host_path_access = dict(launch_attestation.get("host_path_access") or {})
    network_mode = dict(launch_attestation.get("network_mode") or {})
    return {
        "runner_type": runner_type,
        "assurance_class": runner_metadata.get("assurance_class"),
        "isolation_class": _sandbox_isolation_class(
            runner_type=runner_type,
            runner_metadata=runner_metadata,
        ),
        "execution_boundary": runner_metadata.get("execution_boundary"),
        "isolation_claim": runner_metadata.get("isolation_claim"),
        "requested_hardening_controls": list(
            kernel_controls.get("requested_controls") or hardening_controls.get("requested_controls") or []
        ),
        "active_hardening_controls": list(
            kernel_controls.get("active_controls") or hardening_controls.get("active_controls") or []
        ),
        "verified_hardening_controls": list(
            ((launch_attestation.get("hardening_profiles") or {}).get("verified_controls") or [])
        ),
        "supported_hardening_controls": list(
            kernel_controls.get("supported_controls") or hardening_controls.get("supported_controls") or []
        ),
        "unsupported_hardening_controls": list(
            kernel_controls.get("unsupported_controls") or hardening_controls.get("unsupported_controls") or []
        ),
        "effective_resource_limits": resource_limits,
        "launch_attestation": launch_attestation,
        "mount_isolation": {
            "artifact_mount": mount_mode,
            "writable_temp": writable_temp,
            "host_path_access": host_path_access,
            "filesystem_default": filesystem_policy.get("default"),
            "filesystem_writes": filesystem_policy.get("writes"),
        },
        "runtime_identity": runtime_identity,
        "certification": certification,
        "network_isolation": {
            "boundary": network_mode,
            "outbound_default": network_policy.get("default"),
            "deny_by_default": (
                network_policy.get("default") == "deny"
                or network_mode.get("active") == "none"
            ),
            "capability_required": network_policy.get("capability_required"),
            "private_target_policy": network_policy.get("private_target_policy"),
        },
        "network_policy": network_policy,
        "filesystem_policy": filesystem_policy,
        "provenance_status": provenance_status,
        "operator_note": (
            "This attestation distinguishes requested policy, active runner metadata, and launch-verified "
            "backend state for this plugin host. Verified fields are limited to what the runtime directly "
            "observes at launch time."
        ),
    }


def _inventory_sandbox_attestation(hosts: list[dict[str, Any]]) -> dict[str, Any]:
    assurance_classes_present = sorted(
        {
            str((host.get("sandbox_attestation") or {}).get("assurance_class") or "")
            for host in hosts
            if (host.get("sandbox_attestation") or {}).get("assurance_class")
        }
    )
    isolation_classes_present = sorted(
        {
            str((host.get("sandbox_attestation") or {}).get("isolation_class") or "")
            for host in hosts
            if (host.get("sandbox_attestation") or {}).get("isolation_class")
        }
    )
    runner_types_present = sorted(
        {
            str((host.get("sandbox_attestation") or {}).get("runner_type") or "")
            for host in hosts
            if (host.get("sandbox_attestation") or {}).get("runner_type")
        }
    )
    active_controls = sorted(
        {
            str(control)
            for host in hosts
            for control in list((host.get("sandbox_attestation") or {}).get("active_hardening_controls") or [])
        }
    )
    certification_tiers_present = sorted(
        {
            str(
                ((host.get("sandbox_attestation") or {}).get("certification") or {}).get(
                    "certification_tier"
                )
                or ""
            )
            for host in hosts
            if ((host.get("sandbox_attestation") or {}).get("certification") or {}).get(
                "certification_tier"
            )
        }
    )
    return {
        "present": bool(hosts),
        "host_count": len(hosts),
        "runner_types_present": runner_types_present,
        "assurance_classes_present": assurance_classes_present,
        "isolation_classes_present": isolation_classes_present,
        "certification_tiers_present": certification_tiers_present,
        "active_hardening_controls_present": active_controls,
        "hosts": [
            {
                "name": host.get("name"),
                "runner_type": (host.get("sandbox_attestation") or {}).get("runner_type"),
                "assurance_class": (host.get("sandbox_attestation") or {}).get("assurance_class"),
                "isolation_class": (host.get("sandbox_attestation") or {}).get("isolation_class"),
                "requested_hardening_controls": list(
                    (host.get("sandbox_attestation") or {}).get("requested_hardening_controls") or []
                ),
                "active_hardening_controls": list(
                    (host.get("sandbox_attestation") or {}).get("active_hardening_controls") or []
                ),
                "verified_hardening_controls": list(
                    (host.get("sandbox_attestation") or {}).get("verified_hardening_controls") or []
                ),
                "effective_resource_limits": dict(
                    (host.get("sandbox_attestation") or {}).get("effective_resource_limits") or {}
                ),
                "launch_attestation": dict(
                    (host.get("sandbox_attestation") or {}).get("launch_attestation") or {}
                ),
                "mount_isolation": dict(
                    (host.get("sandbox_attestation") or {}).get("mount_isolation") or {}
                ),
                "runtime_identity": dict(
                    (host.get("sandbox_attestation") or {}).get("runtime_identity") or {}
                ),
                "certification": dict(
                    (host.get("sandbox_attestation") or {}).get("certification") or {}
                ),
                "network_isolation": dict(
                    (host.get("sandbox_attestation") or {}).get("network_isolation") or {}
                ),
                "network_policy": dict(
                    (host.get("sandbox_attestation") or {}).get("network_policy") or {}
                ),
                "filesystem_policy": dict(
                    (host.get("sandbox_attestation") or {}).get("filesystem_policy") or {}
                ),
                "provenance_status": dict(
                    (host.get("sandbox_attestation") or {}).get("provenance_status") or {}
                ),
            }
            for host in hosts
        ],
        "operator_note": (
            "Sandbox attestation summarizes requested policy, active runner metadata, launch-verified "
            "state, pinned runtime identity, resource limits, and provenance for third-party plugin hosts."
        ),
    }


def _start_record(record: PluginHostRecord, *, runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    _assert_host_not_quarantined(record)
    record.runner = create_sandbox_runner(record.runner_type)
    record.sandbox_instance_id = secrets.token_hex(12)
    record.launch_count += 1
    record.state = "starting"
    record.last_start_at = _utcnow_iso()
    record.last_stop_at = None
    record.last_error = None
    record.last_exit_code = None
    response = record.runner.start(
        handler=record.handler,
        plugin_root=record.plugin_root,
        runtime_context=_prepare_context(
            extension_name=record.name,
            owner_class=record.owner_class,
            granted_capabilities=record.granted_capabilities,
            sandbox_instance_id=record.sandbox_instance_id,
            runner_type=record.runner_type,
            runtime_context=runtime_context,
        ),
    )
    merged_provenance = dict(response.get("provenance") or {})
    merged_provenance.update(record.provenance)
    record.provenance = merged_provenance
    record.state = "running"
    record.last_heartbeat_at = _utcnow_iso()
    snapshot = record.snapshot()
    active_profile_name = str(
        get_api_runtime_state().get("deployment_profile") or ""
    ).strip()
    if not active_profile_name or active_profile_name == "unknown":
        try:
            active_profile_name, _ = resolve_api_deployment_profile()
        except Exception:
            active_profile_name = ""
    if (
        record.owner_class == "external-third-party"
        and hostile_third_party_profile_required(active_profile_name)
    ):
        violations = hostile_third_party_attestation_violations(
            dict(snapshot.get("sandbox_attestation") or {})
        )
        if violations:
            _mark_failure(
                record,
                state="failed",
                error=(
                    "plugin host launch did not satisfy hostile-third-party sandbox "
                    f"attestation requirements: {', '.join(violations)}"
                ),
                kind="contract_violation",
            )
            _terminate_record_process(record, force_kill=True)
            raise RuntimeError(
                "hostile-third-party sandbox admission failed because live sandbox "
                f"attestation requirements were not verified: {', '.join(violations)}"
            )
        snapshot = record.snapshot()
    return snapshot


def _terminate_record_process(record: PluginHostRecord, *, force_kill: bool = False) -> None:
    runner = record.runner
    if runner is None:
        return
    try:
        record.last_exit_code = runner.returncode()
        runner.shutdown(force=force_kill)
    except Exception:
        pass
    finally:
        record.last_exit_code = runner.returncode()
        record.runner = None
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
    runner_type: str | None = None,
    force_restart: bool = False,
) -> dict[str, Any]:
    plugin_root_str = str(plugin_root)
    if owner_class == "external-third-party":
        validate_external_third_party_plugin_runtime_policy(identifier=name)
    resolved_runner_type = resolve_sandbox_runner_type(runner_type)
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
                runner_type=resolved_runner_type,
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
            or record.runner_type != resolved_runner_type
            or dict(record.provenance) != dict(provenance or {})
        )
        if config_changed:
            record.handler = handler
            record.plugin_root = plugin_root_str
            record.owner_class = owner_class
            record.granted_capabilities = list(granted_capabilities)
            record.resource_access = dict(resource_access or {})
            record.runner_type = resolved_runner_type
            record.provenance = dict(provenance or {})
            force_restart = True
        if force_restart and record.runner is not None:
            record.restart_count += 1
            _terminate_record_process(record)
        if record.runner is None or not record.runner.is_running():
            if record.runner is not None and not record.runner.is_running() and record.runner.returncode() is not None:
                _mark_failure(
                    record,
                    state="crashed",
                    error=f"plugin host exited with code {record.runner.returncode()}",
                    kind="crash",
                    exit_code=record.runner.returncode(),
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
        runner = record.runner
        if runner is None:
            record.state = "stopped"
            return record.snapshot()
        if not runner.is_running():
            _mark_failure(
                record,
                state="crashed",
                error=f"plugin host exited with code {runner.returncode()}",
                kind="crash",
                exit_code=runner.returncode(),
            )
            record.runner = None
            return record.snapshot()
        try:
            response = runner.heartbeat(timeout_seconds=5.0)
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
            runner = record.runner
            if runner is None or not runner.is_running():
                if runner is not None and runner.returncode() is not None:
                    _mark_failure(
                        record,
                        state="crashed",
                        error=f"plugin host exited with code {runner.returncode()}",
                        kind="crash",
                        exit_code=runner.returncode(),
                    )
                if retries_remaining < 0:
                    raise RuntimeError("plugin host is unavailable")
                record.restart_count += 1
                _start_record(record, runtime_context=runtime_context)
                runner = record.runner
            try:
                response = runner.execute(
                    state=state,
                    runtime_context=_prepare_context(
                        extension_name=record.name,
                        owner_class=record.owner_class,
                        granted_capabilities=record.granted_capabilities,
                        sandbox_instance_id=record.sandbox_instance_id,
                        runner_type=record.runner_type,
                        runtime_context=runtime_context,
                    ),
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
                runner = record.runner
                crashed = runner is not None and (not runner.is_running()) and runner.returncode() is not None
                failure_kind = _classify_failure(error=str(exc), crashed=crashed)
                _mark_failure(
                    record,
                    state="crashed" if crashed else "failed",
                    error=str(exc),
                    kind=failure_kind,
                    exit_code=runner.returncode() if crashed and runner is not None else None,
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
    default_runner_type = resolve_sandbox_runner_type()
    default_runner_note = (
        "The current default runner is insecure_dev_subprocess, which is not a sandbox."
        if default_runner_type == RUNNER_INSECURE_DEV_SUBPROCESS
        else (
            "The current default runner is strong_sandbox_vm, which requires a Linux host plus a dedicated sandbox launcher and image."
            if default_runner_type == RUNNER_STRONG_SANDBOX_VM
            else "The current default runner is containerized_oci, which requires an operator-supplied container runtime and image."
        )
    )
    return {
        "present": bool(hosts),
        "protocol_version": PLUGIN_HOST_PROTOCOL_VERSION,
        "sandbox_runner_interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
        "default_runner_type": default_runner_type,
        "runner_types_present": sorted({str(host.get("runner_type") or "") for host in hosts if host.get("runner_type")}),
        "available_runners": list_supported_sandbox_runners(),
        "overall_status": overall_status,
        "host_count": len(hosts),
        "hosts": hosts,
        "sandbox_attestation": _inventory_sandbox_attestation(hosts),
        "operator_note": (
            "Third-party plugin nodes run behind a runtime-owned plugin host boundary. "
            "The runtime tracks host lifecycle and health separately from plugin payload results, "
            "and may apply restart backoff or quarantine after repeated failures. "
            f"{default_runner_note}"
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
