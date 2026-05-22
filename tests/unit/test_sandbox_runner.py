from __future__ import annotations

import json
import queue

import pytest


pytestmark = pytest.mark.runtime_only


@pytest.fixture
def clean_plugin_hosts():
    from AINDY.platform_layer.plugin_host import reset_plugin_hosts

    reset_plugin_hosts()
    try:
        yield
    finally:
        reset_plugin_hosts()


class _FakeReadablePipe:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()

    def put_line(self, line: str) -> None:
        self._queue.put(line)

    def readline(self) -> str:
        return self._queue.get()


class _FakeWritablePipe:
    def __init__(self, on_line) -> None:
        self._on_line = on_line
        self._buffer = ""

    def write(self, chunk: str) -> int:
        self._buffer += chunk
        return len(chunk)

    def flush(self) -> None:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._on_line(line)


class _FakeHostProcess:
    def __init__(self, args, **kwargs) -> None:
        self.args = list(args)
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode = None
        self.stdout = _FakeReadablePipe()
        self.stderr = _FakeReadablePipe()
        self.stdin = _FakeWritablePipe(self._handle_line)

    def _handle_line(self, raw: str) -> None:
        payload = json.loads(raw)
        command = payload["command"]
        if command == "start":
            response = {
                "ok": True,
                "provenance": {
                    "module_name": "sample_plugin",
                    "source_path": payload.get("plugin_root"),
                },
            }
        elif command == "heartbeat":
            response = {"ok": True, "pid": 999, "request_count": 1, "started": True}
        elif command == "execute":
            response = {"ok": True, "result": {"status": "SUCCESS", "output_patch": {"mode": "container"}}}
        elif command == "shutdown":
            response = {"ok": True, "status": "shutting_down"}
            self.returncode = 0
        else:
            response = {"ok": False, "error": f"unsupported command {command!r}"}
        self.stdout.put_line(json.dumps(response) + "\n")

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 0

    def kill(self) -> None:
        self.returncode = 1

    def wait(self, timeout=None) -> int:
        _ = timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_auto_runner_selection_prefers_container_for_distributed(monkeypatch):
    from AINDY.config import settings
    from AINDY.platform_layer.sandbox_runner import resolve_sandbox_runner_type

    monkeypatch.setattr(settings, "AINDY_PLUGIN_SANDBOX_RUNNER", "auto")
    monkeypatch.setattr(settings, "EXECUTION_MODE", "distributed")
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", "distributed-api")

    assert resolve_sandbox_runner_type() == "containerized_oci"


def test_auto_runner_selection_prefers_dev_subprocess_for_single_instance(monkeypatch):
    from AINDY.config import settings
    from AINDY.platform_layer.sandbox_runner import resolve_sandbox_runner_type

    monkeypatch.setattr(settings, "AINDY_PLUGIN_SANDBOX_RUNNER", "auto")
    monkeypatch.setattr(settings, "EXECUTION_MODE", "thread")
    monkeypatch.delenv("AINDY_DEPLOYMENT_PROFILE", raising=False)

    assert resolve_sandbox_runner_type() == "insecure_dev_subprocess"


def test_container_runner_executes_through_plugin_host(monkeypatch, tmp_path, clean_plugin_hosts):
    from AINDY.config import settings
    from AINDY.platform_layer.plugin_host import (
        execute_plugin_host,
        shutdown_plugin_host,
        start_plugin_host,
    )
    from AINDY.platform_layer import sandbox_runner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_WRITABLE_TMP", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_NO_NEW_PRIVILEGES", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_DROP_ALL_CAPABILITIES", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_DISABLE_NETWORK", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_READ_ONLY_ROOTFS", True)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_PIDS_LIMIT", 64)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_MEMORY_LIMIT", "512m")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_CPU_LIMIT", 1.5)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_CPU_SHARES", 512)
    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox_runner.subprocess, "Popen", _FakeHostProcess)

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    snapshot = start_plugin_host(
        name="container-plugin",
        handler="sample_plugin:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
        runner_type="containerized_oci",
    )
    result = execute_plugin_host(
        name="container-plugin",
        state={"value": 1},
        runtime_context={"user_id": "u-1"},
    )

    assert snapshot["runner_type"] == "containerized_oci"
    assert snapshot["runner"]["container_runtime"] == "docker"
    assert snapshot["runner"]["image"] == "ghcr.io/example/aindy-runtime:test"
    assert snapshot["runner"]["plugin_mount_mode"] == "read-only"
    assert snapshot["runner"]["writable_tmp"] is True
    assert "no_new_privileges" in snapshot["runner"]["kernel_controls"]["active_controls"]
    assert "drop_all_capabilities" in snapshot["runner"]["kernel_controls"]["active_controls"]
    assert "disable_network" in snapshot["runner"]["kernel_controls"]["active_controls"]
    assert "read_only_rootfs" in snapshot["runner"]["kernel_controls"]["active_controls"]
    assert "pids_limit" in snapshot["runner"]["kernel_controls"]["active_controls"]
    assert snapshot["resource_limits"]["enforcement"] == "container-runtime-hard-limits"
    assert snapshot["resource_limits"]["effective_limits"]["memory_limit"] == "512m"
    assert snapshot["resource_limits"]["effective_limits"]["cpu_limit"] == 1.5
    assert snapshot["resource_limits"]["effective_limits"]["cpu_shares"] == 512
    assert snapshot["resource_limits"]["effective_limits"]["process_limit"] == 64
    assert snapshot["resource_limits"]["effective_limits"]["wall_clock_timeout_seconds"] == 30.0
    assert result["status"] == "SUCCESS"
    assert result["output_patch"]["mode"] == "container"
    assert shutdown_plugin_host("container-plugin") is True


def test_container_runner_unavailable_fails_closed_without_fallback(monkeypatch, tmp_path, clean_plugin_hosts):
    from AINDY.config import settings
    from AINDY.platform_layer.plugin_host import start_plugin_host

    monkeypatch.setattr(settings, "AINDY_PLUGIN_SANDBOX_RUNNER", "auto")
    monkeypatch.setattr(settings, "EXECUTION_MODE", "distributed")
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", "distributed-api")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "")

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="AINDY_PLUGIN_CONTAINER_IMAGE"):
        start_plugin_host(
            name="missing-container-runner",
            handler="sample_plugin:handler",
            plugin_root=plugin_dir,
            owner_class="external-third-party",
            granted_capabilities=[],
        )


def test_container_kernel_controls_report_supported_and_unsupported(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import inspect_container_kernel_controls
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")

    report = inspect_container_kernel_controls(
        container_runtime="docker",
        requested_controls={
            "no_new_privileges": True,
            "drop_all_capabilities": True,
            "disable_network": True,
            "read_only_rootfs": True,
            "pids_limit": True,
            "seccomp_profile": True,
            "apparmor_profile": True,
            "selinux_label": True,
        },
    )

    assert report["platform"] == "linux"
    assert report["runtime_available"] is True
    assert "no_new_privileges" in report["active_controls"]
    assert "seccomp_profile" in report["active_controls"]
    assert "apparmor_profile" in report["active_controls"]
    assert "selinux_label" in report["active_controls"]
    assert report["unsupported_controls"] == []


def test_container_kernel_controls_report_unsupported_explicitly_on_non_linux(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import inspect_container_kernel_controls
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Windows")

    report = inspect_container_kernel_controls(
        container_runtime="docker",
        requested_controls={
            "no_new_privileges": True,
            "seccomp_profile": True,
            "apparmor_profile": True,
            "selinux_label": True,
        },
    )

    assert report["platform"] == "windows"
    assert report["active_controls"] == []
    unsupported = {entry["control"]: entry["reason"] for entry in report["unsupported_controls"]}
    assert "no_new_privileges" in unsupported
    assert "seccomp_profile" in unsupported
    assert "apparmor_profile" in unsupported
    assert "selinux_label" in unsupported


def test_container_resource_limits_report_unavailable_runtime_explicitly(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import inspect_container_resource_limits
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: None)
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")

    report = inspect_container_resource_limits(
        container_runtime="docker",
        requested_limits={
            "memory_limit": "256m",
            "cpu_limit": 1.0,
            "cpu_shares": 256,
            "process_limit": 32,
            "wall_clock_timeout_seconds": 30.0,
        },
    )

    assert report["enforcement"] == "unavailable"
    assert report["runtime_available"] is False
    assert report["effective_limits"]["memory_limit"] == "256m"
    assert report["effective_limits"]["cpu_limit"] == 1.0
    assert report["effective_limits"]["process_limit"] == 32
    unsupported = {entry["limit"]: entry["reason"] for entry in report["unsupported_limits"]}
    assert "memory_limit" in unsupported
    assert "cpu_limit" in unsupported
    assert "process_limit" in unsupported


def test_sandbox_platform_matrix_reports_linux_hardened_support(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")

    matrix = sandbox_platform_capability_matrix()

    assert matrix["schema_version"] == "2026-05-21"
    assert matrix["current_platform"] == "linux"
    assert matrix["current_environment"]["production_safe_third_party_plugin_execution"] is True
    assert "containerized_oci" in matrix["current_environment"]["available_runner_types"]
    assert "seccomp_profile" in matrix["current_environment"]["available_hardening_controls"]["containerized_oci"]


def test_sandbox_platform_matrix_reports_windows_degraded_support(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Windows")

    matrix = sandbox_platform_capability_matrix()

    assert matrix["current_platform"] == "windows"
    assert matrix["current_environment"]["production_safe_third_party_plugin_execution"] is False
    assert "containerized_oci" in matrix["current_environment"]["available_runner_types"]
    assert any(
        "linux-only kernel controls" in entry.lower()
        for entry in matrix["current_environment"]["degraded_modes"]
    )
