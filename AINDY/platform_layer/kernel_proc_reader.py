from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

from AINDY.platform_layer.sandbox_runner import (
    VERIFICATION_METHOD_KERNEL_OBSERVABLE,
    VERIFICATION_METHOD_WORKER_SELF_REPORT,
)


def _linux_unavailable_result(
    pid: int,
    *,
    source: str,
    error: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    return {
        "available": False,
        "pid": pid,
        "source": source,
        "error": error,
        **extra,
    }


def _is_linux() -> bool:
    return platform.system().lower() == "linux"


def read_seccomp_status(pid: int) -> dict[str, Any]:
    source = "proc_status"
    if not _is_linux():
        return _linux_unavailable_result(
            pid,
            source=source,
            error="kernel proc reads are available only on Linux hosts",
            extra={
                "seccomp_mode": None,
                "seccomp_active": None,
                "seccomp_mode_label": None,
            },
        )
    try:
        status_path = Path(f"/proc/{pid}/status")
        seccomp_mode: int | None = None
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Seccomp:"):
                _, raw_value = line.split(":", 1)
                seccomp_mode = int(raw_value.strip())
                break
        if seccomp_mode is None:
            raise ValueError("Seccomp field not found in /proc status")
        mode_labels = {0: "none", 1: "strict", 2: "filter"}
        return {
            "available": True,
            "pid": pid,
            "seccomp_mode": seccomp_mode,
            "seccomp_active": seccomp_mode == 2,
            "seccomp_mode_label": mode_labels.get(seccomp_mode, "unknown"),
            "source": source,
            "error": None,
        }
    except Exception as exc:
        return _linux_unavailable_result(
            pid,
            source=source,
            error=str(exc),
            extra={
                "seccomp_mode": None,
                "seccomp_active": None,
                "seccomp_mode_label": None,
            },
        )


def read_cgroup_membership(pid: int) -> dict[str, Any]:
    source = "proc_cgroup"
    if not _is_linux():
        return _linux_unavailable_result(
            pid,
            source=source,
            error="kernel proc reads are available only on Linux hosts",
            extra={
                "cgroup_version": None,
                "entries": [],
                "in_named_cgroup": None,
            },
        )
    try:
        cgroup_path = Path(f"/proc/{pid}/cgroup")
        raw_lines = [
            line.strip()
            for line in cgroup_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        entries: list[dict[str, str]] = []
        for line in raw_lines:
            parts = line.split(":", 2)
            if len(parts) != 3:
                raise ValueError(f"malformed cgroup entry: {line!r}")
            hierarchy_id, controllers, path = parts
            entries.append(
                {
                    "hierarchy_id": hierarchy_id,
                    "controllers": controllers,
                    "path": path,
                }
            )
        cgroup_version = "v2" if any(line.startswith("0::/") for line in raw_lines) else "v1"
        return {
            "available": True,
            "pid": pid,
            "cgroup_version": cgroup_version,
            "entries": entries,
            "in_named_cgroup": any(
                str(entry.get("path") or "").strip() not in {"", "/"}
                for entry in entries
            ),
            "source": source,
            "error": None,
        }
    except Exception as exc:
        return _linux_unavailable_result(
            pid,
            source=source,
            error=str(exc),
            extra={
                "cgroup_version": None,
                "entries": [],
                "in_named_cgroup": None,
            },
        )


def read_namespace_ids(pid: int) -> dict[str, Any]:
    source = "proc_ns"
    if not _is_linux():
        return _linux_unavailable_result(
            pid,
            source=source,
            error="kernel proc reads are available only on Linux hosts",
            extra={"namespaces": {}},
        )
    try:
        namespace_dir = Path(f"/proc/{pid}/ns")
        namespaces: dict[str, str] = {}
        for entry in sorted(namespace_dir.iterdir(), key=lambda item: item.name):
            namespaces[entry.name] = os.readlink(entry)
        return {
            "available": True,
            "pid": pid,
            "namespaces": namespaces,
            "source": source,
            "error": None,
        }
    except Exception as exc:
        return _linux_unavailable_result(
            pid,
            source=source,
            error=str(exc),
            extra={"namespaces": {}},
        )


def compare_namespace_ids(worker_ns: dict[str, Any], runtime_ns: dict[str, Any]) -> dict[str, Any]:
    worker_namespaces = dict(worker_ns or {})
    runtime_namespaces = dict(runtime_ns or {})
    common_namespaces = sorted(set(worker_namespaces) & set(runtime_namespaces))
    if not common_namespaces:
        return {
            "separated_namespaces": [],
            "shared_namespaces": [],
            "net_isolated": False,
            "mnt_isolated": False,
            "pid_isolated": False,
            "comparison_available": False,
        }
    separated = [
        namespace
        for namespace in common_namespaces
        if worker_namespaces.get(namespace) != runtime_namespaces.get(namespace)
    ]
    shared = [
        namespace
        for namespace in common_namespaces
        if worker_namespaces.get(namespace) == runtime_namespaces.get(namespace)
    ]
    return {
        "separated_namespaces": separated,
        "shared_namespaces": shared,
        "net_isolated": "net" in separated,
        "mnt_isolated": "mnt" in separated,
        "pid_isolated": "pid" in separated,
        "comparison_available": True,
    }


def read_all_kernel_evidence(pid: int, runtime_pid: int) -> dict[str, Any]:
    seccomp = read_seccomp_status(pid)
    cgroup = read_cgroup_membership(pid)
    namespaces = read_namespace_ids(pid)
    runtime_namespaces = read_namespace_ids(runtime_pid)
    namespace_comparison = compare_namespace_ids(
        namespaces.get("namespaces") or {},
        runtime_namespaces.get("namespaces") or {},
    )
    kernel_observable = bool(
        seccomp.get("available")
        and cgroup.get("available")
        and namespaces.get("available")
        and runtime_namespaces.get("available")
        and namespace_comparison.get("comparison_available")
    )
    return {
        "pid": pid,
        "seccomp": seccomp,
        "cgroup": cgroup,
        "namespaces": namespaces,
        "runtime_namespaces": runtime_namespaces,
        "namespace_comparison": namespace_comparison,
        "kernel_observable": kernel_observable,
        "verification_method": (
            VERIFICATION_METHOD_KERNEL_OBSERVABLE
            if kernel_observable
            else VERIFICATION_METHOD_WORKER_SELF_REPORT
        ),
    }
