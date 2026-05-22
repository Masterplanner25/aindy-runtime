from __future__ import annotations

import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any

from AINDY.config import settings

SANDBOX_RUNNER_INTERFACE_VERSION = "2026-05-21"
SANDBOX_PLATFORM_MATRIX_VERSION = "2026-05-21"
RUNNER_INSECURE_DEV_SUBPROCESS = "insecure_dev_subprocess"
RUNNER_CONTAINERIZED_OCI = "containerized_oci"
RUNNER_SELECTION_AUTO = "auto"
SUPPORTED_SANDBOX_RUNNERS = (
    RUNNER_INSECURE_DEV_SUBPROCESS,
    RUNNER_CONTAINERIZED_OCI,
)

PLATFORM_LINUX = "linux"
PLATFORM_WINDOWS = "windows"
PLATFORM_MACOS = "darwin"
PLATFORM_OTHER = "other"


def list_supported_sandbox_runners() -> list[dict[str, Any]]:
    return [
        {
            "runner_type": RUNNER_INSECURE_DEV_SUBPROCESS,
            "stability": "current",
            "isolation_claim": "none",
            "execution_boundary": "subprocess-json-rpc",
            "resource_limit_enforcement": "none",
            "operator_note": (
                "This runner launches the extension worker in a local subprocess. "
                "It improves failure containment and policy enforcement, but it is "
                "not a general sandbox."
            ),
        },
        {
            "runner_type": RUNNER_CONTAINERIZED_OCI,
            "stability": "current",
            "isolation_claim": "container-boundary",
            "execution_boundary": "container-stdio-json-rpc",
            "kernel_control_reporting": "explicit",
            "resource_limit_enforcement": "container-runtime-hard-limits",
            "operator_note": (
                "This runner launches the extension worker in an operator-supplied "
                "container image with a read-only plugin mount and minimal environment. "
                "It is materially stronger than the dev subprocess runner, but it is "
                "still not a blanket security guarantee."
            ),
        },
    ]


def _platform_label(platform_name: str) -> str:
    if platform_name == PLATFORM_LINUX:
        return "Linux"
    if platform_name == PLATFORM_WINDOWS:
        return "Windows"
    if platform_name == PLATFORM_MACOS:
        return "macOS"
    return "Other"


def _normalize_platform_name(name: str | None = None) -> str:
    normalized = str(name or _normalized_platform_system() or "").strip().lower()
    if normalized == PLATFORM_LINUX:
        return PLATFORM_LINUX
    if normalized == PLATFORM_WINDOWS:
        return PLATFORM_WINDOWS
    if normalized == PLATFORM_MACOS:
        return PLATFORM_MACOS
    return PLATFORM_OTHER


def _platform_matrix_entry(
    *,
    platform_name: str,
    container_runtime: str,
    runtime_available: bool,
) -> dict[str, Any]:
    linux = platform_name == PLATFORM_LINUX
    windows = platform_name == PLATFORM_WINDOWS
    macos = platform_name == PLATFORM_MACOS
    available_runner_types = [RUNNER_INSECURE_DEV_SUBPROCESS]
    if runtime_available:
        available_runner_types.append(RUNNER_CONTAINERIZED_OCI)

    available_hardening_controls = {
        "insecure_dev_subprocess": [],
        "containerized_oci": [
            "disable_network",
            "read_only_rootfs",
        ],
    }
    if linux and runtime_available:
        available_hardening_controls["containerized_oci"].extend(
            [
                "no_new_privileges",
                "drop_all_capabilities",
                "pids_limit",
                "seccomp_profile",
                "apparmor_profile",
                "selinux_label",
            ]
        )

    unsupported_guarantees = [
        "in-process Python sandboxing",
        "uniform third-party plugin isolation across all host platforms",
    ]
    degraded_modes: list[str] = []
    if not runtime_available:
        degraded_modes.append(
            f"containerized_oci unavailable because container runtime {container_runtime!r} is not on PATH"
        )
        unsupported_guarantees.append("containerized third-party plugin sandbox execution")
    if windows or macos or platform_name == PLATFORM_OTHER:
        degraded_modes.append(
            "containerized_oci cannot report Linux-only kernel controls such as no_new_privileges, "
            "seccomp, AppArmor, or SELinux on this host platform"
        )
        unsupported_guarantees.append(
            "Linux-grade hardened third-party plugin sandbox guarantees on non-Linux hosts"
        )
    if macos:
        degraded_modes.append(
            "containerized_oci relies on host container virtualization and does not imply native macOS kernel policy enforcement"
        )
    if platform_name == PLATFORM_OTHER:
        degraded_modes.append(
            "host platform is not part of the explicitly characterized sandbox support set"
        )

    return {
        "platform": platform_name,
        "label": _platform_label(platform_name),
        "available_runner_types": available_runner_types,
        "available_hardening_controls": available_hardening_controls,
        "production_safe_third_party_plugin_execution": bool(linux and runtime_available),
        "unsupported_guarantees": unsupported_guarantees,
        "degraded_modes": degraded_modes,
        "operator_note": (
            "Production-safe third-party plugin sandbox guarantees are currently characterized only "
            "for Linux hosts with a compatible container runtime available."
        ),
    }


def sandbox_platform_capability_matrix(
    *,
    current_platform: str | None = None,
    container_runtime: str | None = None,
) -> dict[str, Any]:
    runtime_name = str(container_runtime or settings.AINDY_PLUGIN_CONTAINER_RUNTIME or "docker").strip() or "docker"
    runtime_available = shutil.which(runtime_name) is not None
    current_name = _normalize_platform_name(current_platform)
    return {
        "schema_version": SANDBOX_PLATFORM_MATRIX_VERSION,
        "current_platform": current_name,
        "current_environment": _platform_matrix_entry(
            platform_name=current_name,
            container_runtime=runtime_name,
            runtime_available=runtime_available,
        ),
        "supported_platforms": {
            PLATFORM_LINUX: _platform_matrix_entry(
                platform_name=PLATFORM_LINUX,
                container_runtime=runtime_name,
                runtime_available=runtime_available,
            ),
            PLATFORM_WINDOWS: _platform_matrix_entry(
                platform_name=PLATFORM_WINDOWS,
                container_runtime=runtime_name,
                runtime_available=runtime_available,
            ),
            PLATFORM_MACOS: _platform_matrix_entry(
                platform_name=PLATFORM_MACOS,
                container_runtime=runtime_name,
                runtime_available=runtime_available,
            ),
            PLATFORM_OTHER: _platform_matrix_entry(
                platform_name=PLATFORM_OTHER,
                container_runtime=runtime_name,
                runtime_available=runtime_available,
            ),
        },
    }


def resolve_sandbox_runner_type(explicit: str | None = None) -> str:
    requested = str(explicit or settings.AINDY_PLUGIN_SANDBOX_RUNNER or RUNNER_SELECTION_AUTO).strip()
    if requested and requested != RUNNER_SELECTION_AUTO:
        if requested not in SUPPORTED_SANDBOX_RUNNERS:
            raise ValueError(
                f"Unsupported sandbox runner {requested!r}. "
                f"Supported values: {', '.join(SUPPORTED_SANDBOX_RUNNERS)}."
            )
        return requested
    deployment_profile = str(os.getenv("AINDY_DEPLOYMENT_PROFILE", "") or "").strip()
    if deployment_profile in {"distributed-api", "distributed-worker"}:
        return RUNNER_CONTAINERIZED_OCI
    if str(settings.EXECUTION_MODE).lower() == "distributed":
        return RUNNER_CONTAINERIZED_OCI
    return RUNNER_INSECURE_DEV_SUBPROCESS


class SandboxRunner(ABC):
    @property
    @abstractmethod
    def runner_type(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def start(
        self,
        *,
        handler: str,
        plugin_root: str | Path,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def execute(
        self,
        *,
        state: dict[str, Any],
        runtime_context: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def heartbeat(self, *, timeout_seconds: float) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def shutdown(self, *, force: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_running(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def pid(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    def returncode(self) -> int | None:
        raise NotImplementedError


def create_sandbox_runner(runner_type: str | None = None) -> SandboxRunner:
    resolved = resolve_sandbox_runner_type(runner_type)
    if resolved == RUNNER_INSECURE_DEV_SUBPROCESS:
        return InsecureDevSubprocessRunner()
    if resolved == RUNNER_CONTAINERIZED_OCI:
        return ContainerizedOciSandboxRunner()
    raise ValueError(
        f"Unsupported sandbox runner {resolved!r}. "
        f"Supported values: {', '.join(item['runner_type'] for item in list_supported_sandbox_runners())}."
    )


class _JsonRpcProcessRunner(SandboxRunner):
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._stderr_lines: deque[str] = deque(maxlen=20)
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(
        self,
        *,
        handler: str,
        plugin_root: str | Path,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any]:
        self._spawn_process(plugin_root)
        response = self._send_command(
            {
                "command": "start",
                "handler": handler,
                "plugin_root": self._worker_plugin_root(plugin_root),
                "context": dict(runtime_context or {}),
            },
            timeout_seconds=10.0,
        )
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "plugin host start failed"))
        return response

    def execute(
        self,
        *,
        state: dict[str, Any],
        runtime_context: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return self._send_command(
            {
                "command": "execute",
                "state": state,
                "context": dict(runtime_context or {}),
            },
            timeout_seconds=timeout_seconds,
        )

    def heartbeat(self, *, timeout_seconds: float) -> dict[str, Any]:
        return self._send_command(
            {"command": "heartbeat"},
            timeout_seconds=timeout_seconds,
        )

    def shutdown(self, *, force: bool = False) -> None:
        process = self._process
        if process is None:
            return
        try:
            if not force and process.poll() is None:
                self._send_command({"command": "shutdown"}, timeout_seconds=5.0)
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
            self._process = None

    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def pid(self) -> int | None:
        process = self._process
        if process is None or process.poll() is not None:
            return None
        return process.pid

    def returncode(self) -> int | None:
        process = self._process
        if process is None:
            return None
        return process.returncode

    @abstractmethod
    def _process_args(self, plugin_root: str | Path) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def _build_child_env(self) -> dict[str, str]:
        raise NotImplementedError

    def _worker_plugin_root(self, plugin_root: str | Path) -> str:
        return str(plugin_root)

    def _spawn_process(self, plugin_root: str | Path) -> None:
        process = subprocess.Popen(
            self._process_args(plugin_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=self._build_child_env(),
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            raise RuntimeError(f"{self.runner_type} did not expose stdio pipes")

        self._process = process
        self._response_queue = queue.Queue()
        self._stderr_lines = deque(maxlen=20)
        self._stdout_thread = threading.Thread(
            target=self._pipe_reader,
            args=(process.stdout,),
            kwargs={"target": "stdout"},
            daemon=True,
            name=f"{self.runner_type}-stdout",
        )
        self._stderr_thread = threading.Thread(
            target=self._pipe_reader,
            args=(process.stderr,),
            kwargs={"target": "stderr"},
            daemon=True,
            name=f"{self.runner_type}-stderr",
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _pipe_reader(self, pipe, *, target: str) -> None:
        try:
            while True:
                line = pipe.readline()
                if not line:
                    break
                if target == "stdout":
                    self._response_queue.put(str(line).rstrip("\r\n"))
                else:
                    self._append_stderr_line(str(line))
        except Exception as exc:
            if target == "stderr":
                self._append_stderr_line(f"reader error: {exc}")

    def _append_stderr_line(self, line: str) -> None:
        cleaned = str(line or "").rstrip()
        if cleaned:
            self._stderr_lines.append(cleaned)

    def _stderr_excerpt(self) -> str:
        if not self._stderr_lines:
            return ""
        return " | stderr=" + " | ".join(self._stderr_lines)

    def _wait_for_response(self, *, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                raise TimeoutError("plugin host command timed out")
            try:
                raw = self._response_queue.get(timeout=min(0.25, remaining))
            except queue.Empty as exc:
                process = self._process
                if process is not None and process.poll() is not None:
                    raise RuntimeError(
                        f"plugin host exited with code {process.returncode}{self._stderr_excerpt()}"
                    ) from exc
                continue
            try:
                return json.loads(raw or "{}")
            except Exception as exc:
                raise RuntimeError(f"plugin host returned invalid JSON: {exc}") from exc

    def _send_command(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("plugin host process is not running")
        if process.poll() is not None:
            raise RuntimeError(
                f"plugin host exited with code {process.returncode}{self._stderr_excerpt()}"
            )
        try:
            process.stdin.write(json.dumps(payload, default=str) + "\n")
            process.stdin.flush()
        except Exception as exc:
            raise RuntimeError(f"failed to send plugin host command: {exc}") from exc
        return self._wait_for_response(timeout_seconds=timeout_seconds)


class InsecureDevSubprocessRunner(_JsonRpcProcessRunner):
    @property
    def runner_type(self) -> str:
        return RUNNER_INSECURE_DEV_SUBPROCESS

    def metadata(self) -> dict[str, Any]:
        return {
            "runner_type": self.runner_type,
            "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
            "execution_boundary": "subprocess-json-rpc",
            "isolation_claim": "none",
            "resource_limits": {
                "enforcement": "none",
                "wall_clock_timeout_seconds": None,
                "memory_limit": None,
                "cpu_limit": None,
                "cpu_shares": None,
                "process_limit": None,
                "operator_note": (
                    "The insecure development subprocess runner does not enforce hard "
                    "memory, CPU, or process quotas. Only higher-level timeout and "
                    "quarantine behavior applies."
                ),
            },
            "development_class": "insecure-development-runner",
            "selection_mode": "explicit-or-auto",
            "operator_note": (
                "The insecure development subprocess runner isolates plugin execution "
                "behind a runtime-owned subprocess protocol. It is not a sandbox."
            ),
        }

    def _build_child_env(self) -> dict[str, str]:
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

    def _process_args(self, plugin_root: str | Path) -> list[str]:
        _ = plugin_root
        return [sys.executable, "-m", "AINDY.platform_layer.extension_worker", "--host"]


class ContainerizedOciSandboxRunner(_JsonRpcProcessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.container_runtime = str(settings.AINDY_PLUGIN_CONTAINER_RUNTIME or "docker").strip() or "docker"
        self.image = str(settings.AINDY_PLUGIN_CONTAINER_IMAGE or "").strip()
        self.plugin_mount_path = str(settings.AINDY_PLUGIN_CONTAINER_PLUGIN_MOUNT_PATH or "/plugin-root").strip() or "/plugin-root"
        self.workdir = str(settings.AINDY_PLUGIN_CONTAINER_WORKDIR or "/tmp").strip() or "/tmp"
        self.no_new_privileges = bool(settings.AINDY_PLUGIN_CONTAINER_NO_NEW_PRIVILEGES)
        self.drop_all_capabilities = bool(settings.AINDY_PLUGIN_CONTAINER_DROP_ALL_CAPABILITIES)
        self.disable_network = bool(settings.AINDY_PLUGIN_CONTAINER_DISABLE_NETWORK)
        self.read_only_rootfs = bool(settings.AINDY_PLUGIN_CONTAINER_READ_ONLY_ROOTFS)
        self.pids_limit = int(settings.AINDY_PLUGIN_CONTAINER_PIDS_LIMIT or 0)
        self.memory_limit = str(settings.AINDY_PLUGIN_CONTAINER_MEMORY_LIMIT or "").strip()
        self.cpu_limit = float(settings.AINDY_PLUGIN_CONTAINER_CPU_LIMIT or 0.0)
        self.cpu_shares = int(settings.AINDY_PLUGIN_CONTAINER_CPU_SHARES or 0)
        self.seccomp_profile = str(settings.AINDY_PLUGIN_CONTAINER_SECCOMP_PROFILE or "").strip()
        self.apparmor_profile = str(settings.AINDY_PLUGIN_CONTAINER_APPARMOR_PROFILE or "").strip()
        self.selinux_label = str(settings.AINDY_PLUGIN_CONTAINER_SELINUX_LABEL or "").strip()
        self.writable_tmp = bool(settings.AINDY_PLUGIN_CONTAINER_WRITABLE_TMP)
        self.tmpfs_size = str(settings.AINDY_PLUGIN_CONTAINER_TMPFS_SIZE or "64m").strip() or "64m"

    @property
    def runner_type(self) -> str:
        return RUNNER_CONTAINERIZED_OCI

    def metadata(self) -> dict[str, Any]:
        kernel_controls = inspect_container_kernel_controls(
            container_runtime=self.container_runtime,
            requested_controls={
                "no_new_privileges": self.no_new_privileges,
                "drop_all_capabilities": self.drop_all_capabilities,
                "disable_network": self.disable_network,
                "read_only_rootfs": self.read_only_rootfs,
                "pids_limit": self.pids_limit > 0,
                "seccomp_profile": bool(self.seccomp_profile),
                "apparmor_profile": bool(self.apparmor_profile),
                "selinux_label": bool(self.selinux_label),
            },
        )
        resource_limits = inspect_container_resource_limits(
            container_runtime=self.container_runtime,
            requested_limits={
                "memory_limit": self.memory_limit,
                "cpu_limit": self.cpu_limit,
                "cpu_shares": self.cpu_shares,
                "process_limit": self.pids_limit,
                "wall_clock_timeout_seconds": 30.0,
            },
        )
        return {
            "runner_type": self.runner_type,
            "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
            "execution_boundary": "container-stdio-json-rpc",
            "isolation_claim": "container-boundary",
            "container_runtime": self.container_runtime,
            "image": self.image or None,
            "plugin_mount_path": self.plugin_mount_path,
            "plugin_mount_mode": "read-only",
            "writable_tmp": self.writable_tmp,
            "tmpfs_size": self.tmpfs_size if self.writable_tmp else None,
            "workdir": self.workdir,
            "kernel_controls": kernel_controls,
            "resource_limits": resource_limits,
            "selection_mode": "explicit-or-auto",
            "operator_note": (
                "The containerized OCI runner requires an operator-provided image with "
                "aindy-runtime installed. The runtime does not silently fall back to the "
                "development subprocess runner when this mode is selected."
            ),
        }

    def _worker_plugin_root(self, plugin_root: str | Path) -> str:
        _ = plugin_root
        return self.plugin_mount_path

    def _build_child_env(self) -> dict[str, str]:
        child_env = {"PYTHONIOENCODING": "utf-8"}
        if os.getenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS"):
            child_env["AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS"] = os.getenv(
                "AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS",
                "",
            )
        return child_env

    def _ensure_container_runtime_ready(self) -> None:
        if not self.image:
            raise RuntimeError(
                "container sandbox runner requires AINDY_PLUGIN_CONTAINER_IMAGE"
            )
        if shutil.which(self.container_runtime) is None:
            raise RuntimeError(
                f"container sandbox runner unavailable: runtime {self.container_runtime!r} was not found on PATH"
            )

    def _process_args(self, plugin_root: str | Path) -> list[str]:
        self._ensure_container_runtime_ready()
        resolved_plugin_root = str(Path(plugin_root).resolve())
        args = [
            self.container_runtime,
            "run",
            "--rm",
            "-i",
            "--mount",
            f"type=bind,src={resolved_plugin_root},dst={self.plugin_mount_path},readonly",
            "--workdir",
            self.workdir,
        ]
        if self.disable_network:
            args.extend(["--network", "none"])
        if self.read_only_rootfs:
            args.append("--read-only")
        if self.drop_all_capabilities:
            args.extend(["--cap-drop", "ALL"])
        if self.no_new_privileges:
            args.extend(["--security-opt", "no-new-privileges"])
        if self.pids_limit > 0:
            args.extend(["--pids-limit", str(self.pids_limit)])
        if self.memory_limit:
            args.extend(["--memory", self.memory_limit])
        if self.cpu_limit > 0:
            args.extend(["--cpus", f"{self.cpu_limit:g}"])
        if self.cpu_shares > 0:
            args.extend(["--cpu-shares", str(self.cpu_shares)])
        if self.seccomp_profile and _supports_seccomp(self.container_runtime):
            args.extend(["--security-opt", f"seccomp={self.seccomp_profile}"])
        if self.apparmor_profile and _supports_apparmor(self.container_runtime):
            args.extend(["--security-opt", f"apparmor={self.apparmor_profile}"])
        if self.selinux_label and _supports_selinux_label(self.container_runtime):
            args.extend(["--security-opt", f"label={self.selinux_label}"])
        if self.writable_tmp:
            args.extend(
                [
                    "--mount",
                    f"type=tmpfs,dst=/tmp,tmpfs-size={self.tmpfs_size}",
                ]
            )
        for key, value in self._build_child_env().items():
            args.extend(["--env", f"{key}={value}"])
        args.extend(
            [
                self.image,
                "python",
                "-m",
                "AINDY.platform_layer.extension_worker",
                "--host",
            ]
        )
        return args


def _normalized_platform_system() -> str:
    return str(platform.system() or "").strip().lower()


def _supports_linux_container_kernel_controls() -> bool:
    return _normalized_platform_system() == "linux"


def _supports_seccomp(container_runtime: str) -> bool:
    return _supports_linux_container_kernel_controls() and container_runtime in {"docker", "podman"}


def _supports_apparmor(container_runtime: str) -> bool:
    return _supports_linux_container_kernel_controls() and container_runtime == "docker"


def _supports_selinux_label(container_runtime: str) -> bool:
    return _supports_linux_container_kernel_controls() and container_runtime in {"docker", "podman"}


def inspect_container_kernel_controls(
    *,
    container_runtime: str,
    requested_controls: dict[str, bool] | None = None,
) -> dict[str, Any]:
    requested = dict(requested_controls or {})
    system_name = _normalized_platform_system()
    runtime_found = shutil.which(container_runtime) is not None
    linux_controls = _supports_linux_container_kernel_controls()

    supported_controls: dict[str, bool] = {
        "no_new_privileges": linux_controls and runtime_found,
        "drop_all_capabilities": linux_controls and runtime_found,
        "disable_network": runtime_found,
        "read_only_rootfs": runtime_found,
        "pids_limit": linux_controls and runtime_found,
        "seccomp_profile": _supports_seccomp(container_runtime) and runtime_found,
        "apparmor_profile": _supports_apparmor(container_runtime) and runtime_found,
        "selinux_label": _supports_selinux_label(container_runtime) and runtime_found,
    }
    active_controls = sorted(
        name
        for name, enabled in requested.items()
        if enabled and supported_controls.get(name, False)
    )
    unsupported_controls = [
        {
            "control": name,
            "reason": _unsupported_control_reason(
                name=name,
                container_runtime=container_runtime,
                runtime_found=runtime_found,
                system_name=system_name,
            ),
        }
        for name, enabled in requested.items()
        if enabled and not supported_controls.get(name, False)
    ]
    return {
        "reporting_version": SANDBOX_RUNNER_INTERFACE_VERSION,
        "platform": system_name or "unknown",
        "container_runtime": container_runtime,
        "runtime_available": runtime_found,
        "requested_controls": sorted(name for name, enabled in requested.items() if enabled),
        "supported_controls": sorted(name for name, supported in supported_controls.items() if supported),
        "active_controls": active_controls,
        "unsupported_controls": unsupported_controls,
        "operator_note": (
            "Kernel-level hardening is reported explicitly per environment. "
            "Only controls listed as active are expected to be enforced for this runner."
        ),
    }


def inspect_container_resource_limits(
    *,
    container_runtime: str,
    requested_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested = dict(requested_limits or {})
    runtime_found = shutil.which(container_runtime) is not None
    system_name = _normalized_platform_system()
    hard_limit_supported = runtime_found

    effective_limits = {
        "wall_clock_timeout_seconds": requested.get("wall_clock_timeout_seconds"),
        "memory_limit": requested.get("memory_limit") or None,
        "cpu_limit": requested.get("cpu_limit") or None,
        "cpu_shares": requested.get("cpu_shares") or None,
        "process_limit": requested.get("process_limit") or None,
    }
    unsupported_limits: list[dict[str, str]] = []
    if not runtime_found:
        for name, value in effective_limits.items():
            if value not in {None, 0, 0.0, ""}:
                unsupported_limits.append(
                    {
                        "limit": name,
                        "reason": f"container runtime {container_runtime!r} is not available on PATH",
                    }
                )

    return {
        "platform": system_name or "unknown",
        "container_runtime": container_runtime,
        "enforcement": "container-runtime-hard-limits" if hard_limit_supported else "unavailable",
        "runtime_available": runtime_found,
        "effective_limits": effective_limits,
        "unsupported_limits": unsupported_limits,
        "operator_note": (
            "Memory, CPU, and process limits are hard limits only when enforced by the "
            "container runtime. Wall-clock timeout remains a host-side execution bound."
        ),
    }


def _unsupported_control_reason(
    *,
    name: str,
    container_runtime: str,
    runtime_found: bool,
    system_name: str,
) -> str:
    if not runtime_found:
        return f"container runtime {container_runtime!r} is not available on PATH"
    if system_name != "linux" and name in {
        "no_new_privileges",
        "drop_all_capabilities",
        "pids_limit",
        "seccomp_profile",
        "apparmor_profile",
        "selinux_label",
    }:
        return f"{name} is not reported as enforceable on platform {system_name or 'unknown'}"
    if name == "seccomp_profile":
        return f"seccomp profile injection is not supported for runtime {container_runtime!r}"
    if name == "apparmor_profile":
        return f"AppArmor profile injection is not supported for runtime {container_runtime!r}"
    if name == "selinux_label":
        return f"SELinux label controls are not supported for runtime {container_runtime!r}"
    return f"{name} is not supported in the current environment"
