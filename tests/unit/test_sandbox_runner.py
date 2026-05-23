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
        self._started_handler = None
        self._started_plugin_root = None
        self._extension_name = None
        self._owner_class = None
        self._sandbox_instance_id = None
        self.stdout = _FakeReadablePipe()
        self.stderr = _FakeReadablePipe()
        self.stdin = _FakeWritablePipe(self._handle_line)

    def _handle_line(self, raw: str) -> None:
        payload = json.loads(raw)
        command = payload["command"]
        context = dict(payload.get("context") or {})
        plugin_context = dict(context.get("plugin_context") or {})
        runtime_api = dict(plugin_context.get("runtime_api") or {})
        if command == "start":
            self._started_handler = payload.get("handler")
            self._started_plugin_root = payload.get("plugin_root")
            self._extension_name = str(plugin_context.get("extension_name") or "")
            self._owner_class = str(plugin_context.get("owner_class") or "")
            self._sandbox_instance_id = str(runtime_api.get("sandbox_instance_id") or "")
            response = {
                "ok": True,
                "provenance": {
                    "module_name": "sample_plugin",
                    "source_path": payload.get("plugin_root"),
                },
            }
        elif command == "heartbeat":
            response = {"ok": True, "pid": 999, "request_count": 1, "started": True}
        elif command == "probe":
            response = {
                "ok": True,
                "pid": 999,
                "started": True,
                "probe": {
                    "worker_instance_id": "worker-fake-1",
                    "verification_scope": "live-worker-self-report-over-authenticated-rpc",
                    "session_continuity": {
                        "started": True,
                        "started_at": "2026-05-22T00:00:00+00:00",
                        "request_count": 1,
                        "handler": self._started_handler or "sample_plugin:handler",
                        "plugin_root": self._started_plugin_root,
                        "extension_name": self._extension_name,
                        "owner_class": self._owner_class,
                        "sandbox_instance_id": self._sandbox_instance_id,
                    },
                    "isolation_state": {
                        "import_guard_active": True,
                        "filesystem_guard_active": True,
                        "network_guard_active": True,
                        "guards_installed": True,
                        "environment_stripped": True,
                        "runtime_modules_pruned": True,
                    },
                    "boundary_metadata": {
                        "runtime_api_channel_hidden": True,
                        "private_targets_allowed": False,
                        "provenance": {"module_name": "sample_plugin"},
                    },
                    "mount_network_state": {
                        "artifact_read_access": {"status": "passed", "verified": True},
                        "artifact_write_blocked": {"status": "passed", "verified": True},
                        "writable_temp_scope": {"status": "passed", "verified": True},
                        "host_path_access_blocked": {"status": "passed", "verified": True},
                        "network_policy": {
                            "socket_guard_active": {"status": "passed", "verified": True},
                            "deny_by_default_outbound": {"status": "passed", "verified": True},
                            "private_target_blocking": {"status": "passed", "verified": True},
                            "expected_boundary_mode": {"status": "passed", "verified": True},
                        },
                    },
                },
            }
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


class _FakeStrongProbeFailureProcess(_FakeHostProcess):
    def _handle_line(self, raw: str) -> None:
        payload = json.loads(raw)
        if payload["command"] == "probe":
            response = {
                "ok": True,
                "pid": 1001,
                "started": True,
                "probe": {
                    "worker_instance_id": "",
                    "verification_scope": "live-worker-self-report-over-authenticated-rpc",
                    "session_continuity": {
                        "started": True,
                        "started_at": "2026-05-22T00:00:00+00:00",
                        "request_count": 0,
                        "handler": "sample_plugin:handler",
                        "plugin_root": payload.get("plugin_root"),
                        "extension_name": "wrong-extension",
                        "owner_class": "external-third-party",
                        "sandbox_instance_id": "wrong-sandbox",
                    },
                    "isolation_state": {
                        "import_guard_active": False,
                        "filesystem_guard_active": True,
                        "network_guard_active": False,
                        "guards_installed": True,
                        "environment_stripped": True,
                        "runtime_modules_pruned": True,
                    },
                    "boundary_metadata": {
                        "runtime_api_channel_hidden": False,
                        "private_targets_allowed": False,
                        "provenance": {"module_name": "sample_plugin"},
                    },
                    "mount_network_state": {
                        "artifact_read_access": {"status": "passed", "verified": True},
                        "artifact_write_blocked": {"status": "failed", "verified": False},
                        "writable_temp_scope": {"status": "passed", "verified": True},
                        "host_path_access_blocked": {"status": "failed", "verified": False},
                        "network_policy": {
                            "socket_guard_active": {"status": "passed", "verified": True},
                            "deny_by_default_outbound": {"status": "failed", "verified": False},
                            "private_target_blocking": {"status": "failed", "verified": False},
                            "expected_boundary_mode": {"status": "passed", "verified": True},
                        },
                    },
                },
            }
            self.stdout.put_line(json.dumps(response) + "\n")
            return
        super()._handle_line(raw)


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


def test_explicit_strong_runner_selection_is_supported(monkeypatch):
    from AINDY.config import settings
    from AINDY.platform_layer.sandbox_runner import resolve_sandbox_runner_type

    monkeypatch.setattr(settings, "AINDY_PLUGIN_SANDBOX_RUNNER", "strong_sandbox_vm")

    assert resolve_sandbox_runner_type() == "strong_sandbox_vm"


def test_container_runner_runtime_identity_reports_pinned_digest(monkeypatch):
    from AINDY.config import settings
    from AINDY.platform_layer.sandbox_runner import ContainerizedOciSandboxRunner

    digest = "sha256:" + ("c" * 64)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", digest)

    metadata = ContainerizedOciSandboxRunner().metadata()

    assert metadata["runtime_identity"]["pinned"] is True
    assert metadata["runtime_identity"]["verification"] == "configured-digest"
    assert metadata["runtime_identity"]["digest"] == digest
    assert metadata["runtime_identity"]["trust_chain"]["verification_status"] == "trusted-pinned-compatible"
    assert metadata["runtime_identity"]["trust_chain"]["accepted_for_production_safe_profiles"] is True
    assert metadata["runtime_identity"]["launch_reference"] == (
        f"ghcr.io/example/aindy-runtime:test@{digest}"
    )
    assert metadata["launch_attestation"]["status"] == "not-started"
    assert metadata["launch_attestation"]["runtime_identity"]["verified"] is False


def test_container_runner_runtime_identity_reports_mutable_reference(monkeypatch):
    from AINDY.config import settings
    from AINDY.platform_layer.sandbox_runner import ContainerizedOciSandboxRunner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", "")

    metadata = ContainerizedOciSandboxRunner().metadata()

    assert metadata["runtime_identity"]["pinned"] is False
    assert metadata["runtime_identity"]["verification"] == "mutable-reference"
    assert metadata["runtime_identity"]["mutable_reference"] is True
    assert metadata["launch_attestation"]["status"] == "not-started"


def test_container_runner_runtime_identity_can_report_trusted_signed_policy(monkeypatch):
    from AINDY.config import settings
    from AINDY.platform_layer.sandbox_runner import ContainerizedOciSandboxRunner

    digest = "sha256:" + ("e" * 64)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", digest)
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_SOURCE", "ghcr.io/example")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_TRUST_ISSUER", "example-ci")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_SIGNING_STATUS", "signature-verified")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_REQUIRED_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_TRUSTED_SOURCES", "ghcr.io/example")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_TRUSTED_ISSUERS", "example-ci")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_REQUIRE_SIGNATURE_VERIFICATION", True)

    metadata = ContainerizedOciSandboxRunner().metadata()

    assert metadata["runtime_identity"]["trust_chain"]["verification_status"] == "trusted-signed-pinned-compatible"
    assert metadata["runtime_identity"]["trust_chain"]["source_trusted"] is True
    assert metadata["runtime_identity"]["trust_chain"]["issuer_trusted"] is True
    assert metadata["runtime_identity"]["trust_chain"]["signature_verified"] is True


def test_container_runner_executes_through_plugin_host(monkeypatch, tmp_path, clean_plugin_hosts):
    from AINDY.config import settings
    from AINDY.platform_layer.plugin_host import (
        execute_plugin_host,
        shutdown_plugin_host,
        start_plugin_host,
    )
    from AINDY.platform_layer import sandbox_runner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", "sha256:" + ("d" * 64))
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_SOURCE", "ghcr.io/example")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_TRUST_ISSUER", "example-ci")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_SIGNING_STATUS", "signature-verified")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_RUNTIME_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_REQUIRED_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_TRUSTED_SOURCES", "ghcr.io/example")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_TRUSTED_ISSUERS", "example-ci")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_CONTAINER_REQUIRE_SIGNATURE_VERIFICATION", True)
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
    assert snapshot["runner"]["runtime_identity"]["pinned"] is True
    assert snapshot["runner"]["runtime_identity"]["trust_chain"]["verification_status"] == "trusted-signed-pinned-compatible"
    assert snapshot["runner"]["runtime_identity"]["launch_reference"].endswith(
        "@sha256:" + ("d" * 64)
    )
    assert snapshot["runner"]["launch_attestation"]["status"] == "launch-observed"
    assert snapshot["runner"]["launch_attestation"]["backend_identity"]["active"] == "docker"
    assert snapshot["runner"]["launch_attestation"]["runtime_identity"]["verified"] is True
    assert snapshot["runner"]["launch_attestation"]["mount_mode"]["verified"] is True
    assert snapshot["runner"]["launch_attestation"]["writable_temp"]["active"] == "tmpfs:/tmp"
    assert snapshot["runner"]["launch_attestation"]["writable_temp"]["verified"] is True
    assert snapshot["runner"]["launch_attestation"]["host_path_access"]["active"] == "plugin-root-bind-only"
    assert snapshot["runner"]["launch_attestation"]["host_path_access"]["verified"] is True
    assert snapshot["runner"]["launch_attestation"]["network_mode"]["active"] == "none"
    assert snapshot["runner"]["launch_attestation"]["network_mode"]["verified"] is True
    assert snapshot["runner"]["launch_attestation"]["resource_limit_mode"]["verified"] is True
    assert set(snapshot["runner"]["launch_attestation"]["resource_limit_mode"]["verified_limits"]) == {
        "memory_limit",
        "cpu_limit",
        "cpu_shares",
        "process_limit",
    }
    assert "no_new_privileges" in snapshot["runner"]["launch_attestation"]["hardening_profiles"]["verified_controls"]
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
    assert snapshot["sandbox_attestation"]["runtime_identity"]["pinned"] is True
    assert snapshot["sandbox_attestation"]["runtime_identity"]["trust_chain"]["accepted_for_production_safe_profiles"] is True
    assert snapshot["sandbox_attestation"]["mount_isolation"]["artifact_mount"]["verified"] is True
    assert snapshot["sandbox_attestation"]["mount_isolation"]["writable_temp"]["verified"] is True
    assert snapshot["sandbox_attestation"]["mount_isolation"]["host_path_access"]["verified"] is True
    assert snapshot["sandbox_attestation"]["network_isolation"]["deny_by_default"] is True
    assert snapshot["sandbox_attestation"]["network_isolation"]["boundary"]["active"] == "none"
    assert "no_new_privileges" in snapshot["sandbox_attestation"]["verified_hardening_controls"]
    assert snapshot["sandbox_attestation"]["launch_attestation"]["status"] == "launch-observed"
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


def test_strong_runner_reports_assurance_class_and_fails_closed_when_unavailable(monkeypatch, tmp_path, clean_plugin_hosts):
    from AINDY.config import settings
    from AINDY.platform_layer.plugin_host import start_plugin_host
    from AINDY.platform_layer.sandbox_runner import StrongSandboxVmRunner
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER", "aindy-sandbox-vm")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "")
    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: None)
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")

    metadata = StrongSandboxVmRunner().metadata()
    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    assert metadata["runner_type"] == "strong_sandbox_vm"
    assert metadata["assurance_class"] == "strong-sandbox-tier"
    assert metadata["resource_limits"]["enforcement"] == "unavailable"
    assert metadata["hardening_controls"]["active_controls"] == []

    with pytest.raises(RuntimeError, match="AINDY_PLUGIN_STRONG_SANDBOX_IMAGE|launcher 'aindy-sandbox-vm' was not found on PATH"):
        start_plugin_host(
            name="missing-strong-runner",
            handler="sample_plugin:handler",
            plugin_root=plugin_dir,
            owner_class="external-third-party",
            granted_capabilities=[],
            runner_type="strong_sandbox_vm",
        )


def test_strong_runner_reports_post_launch_verification(monkeypatch, tmp_path, clean_plugin_hosts):
    from AINDY.config import settings
    from AINDY.platform_layer.plugin_host import shutdown_plugin_host, start_plugin_host
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER", "aindy-sandbox-vm")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "ghcr.io/example/aindy-strong-sandbox:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST", "sha256:" + ("f" * 64))
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SIGNING_STATUS", "signature-verified")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_REQUIRED_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "aindy-sandbox-vm")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox_runner.subprocess, "Popen", _FakeHostProcess)

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    snapshot = start_plugin_host(
        name="strong-plugin",
        handler="sample_plugin:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
        runner_type="strong_sandbox_vm",
    )

    assert snapshot["runner_type"] == "strong_sandbox_vm"
    assert snapshot["sandbox_attestation"]["assurance_class"] == "strong-sandbox-tier"
    assert snapshot["runner"]["assurance_properties"]["boundary_type"] == "dedicated-vm-sandbox"
    assert snapshot["runner"]["assurance_properties"]["network_mediation_model"] == "sandbox-launcher-deny-default"
    assert snapshot["runner"]["launch_attestation"]["assurance_properties"]["active"]["boundary_type"] == "dedicated-vm-sandbox"
    assert snapshot["runner"]["launch_attestation"]["assurance_properties"]["verified"]["network_mediation_model"] is True
    assert snapshot["sandbox_attestation"]["post_launch_verification"]["status"] == "passed"
    assert snapshot["sandbox_attestation"]["post_launch_verification"]["worker_instance_id"] == "worker-fake-1"
    assert {
        "session_continuity.worker_instance_id",
        "session_continuity.sandbox_instance_id",
        "isolation_state.import_guard_active",
        "isolation_state.filesystem_guard_active",
        "isolation_state.network_guard_active",
        "boundary_metadata.runtime_api_channel_hidden",
        "mount_network_state.artifact_read_access",
        "mount_network_state.artifact_write_blocked",
        "mount_network_state.writable_temp_scope",
        "mount_network_state.host_path_access_blocked",
        "mount_network_state.network_policy.socket_guard_active",
        "mount_network_state.network_policy.deny_by_default_outbound",
        "mount_network_state.network_policy.private_target_blocking",
        "mount_network_state.network_policy.expected_boundary_mode",
    }.issubset(set(snapshot["sandbox_attestation"]["post_launch_verification"]["verified_fields"]))
    assert snapshot["sandbox_attestation"]["mount_isolation"]["live_verification"]["artifact_write_blocked"]["verified"] is True
    assert snapshot["sandbox_attestation"]["mount_isolation"]["live_verification"]["host_path_access_blocked"]["verified"] is True
    assert snapshot["sandbox_attestation"]["network_isolation"]["live_verification"]["deny_by_default_outbound"]["verified"] is True
    assert snapshot["sandbox_attestation"]["network_isolation"]["live_verification"]["expected_boundary_mode"]["verified"] is True
    assert snapshot["sandbox_attestation"]["certification"]["certification_tier"] == "strong-sandbox-certified"
    assert snapshot["sandbox_attestation"]["assurance_properties"]["session_verification_model"] == "launch-plus-post-launch-runtime-probe"

    assert shutdown_plugin_host("strong-plugin") is True


def test_strong_runner_fails_closed_on_post_launch_verification_failure(monkeypatch, tmp_path, clean_plugin_hosts):
    from AINDY.config import settings
    from AINDY.platform_layer.plugin_host import start_plugin_host
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER", "aindy-sandbox-vm")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "ghcr.io/example/aindy-strong-sandbox:test")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST", "sha256:" + ("1" * 64))
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SIGNING_STATUS", "signature-verified")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(settings, "AINDY_PLUGIN_STRONG_SANDBOX_REQUIRED_BASE_COMPATIBILITY", "aindy-sandbox-runtime/v1")
    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "aindy-sandbox-vm")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox_runner.subprocess, "Popen", _FakeStrongProbeFailureProcess)

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="strong sandbox post-launch verification failed"):
        start_plugin_host(
            name="strong-plugin-fail",
            handler="sample_plugin:handler",
            plugin_root=plugin_dir,
            owner_class="external-third-party",
            granted_capabilities=[],
            runner_type="strong_sandbox_vm",
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

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "runner")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Linux")

    matrix = sandbox_platform_capability_matrix()

    assert matrix["schema_version"] == "2026-05-21"
    assert matrix["current_platform"] == "linux"
    assert matrix["support_contract"]["strong_sandbox_supported_host_platforms"] == [
        "linux"
    ]
    assert matrix["support_contract"]["hostile_third_party_supported_host_platforms"] == [
        "linux"
    ]
    assert matrix["current_environment"]["production_safe_third_party_plugin_execution"] is True
    assert matrix["current_environment"]["support_levels"]["contained_process"]["support"] == "supported"
    assert matrix["current_environment"]["support_levels"]["container_sandbox"]["support"] == "supported"
    assert matrix["current_environment"]["support_levels"]["strong_sandbox"]["support"] == "supported"
    assert matrix["current_environment"]["equivalence_status"] == "full-strong-sandbox-support"
    assert matrix["current_environment"]["highest_supported_assurance_class"] == "strong-sandbox-tier"
    assert matrix["current_environment"]["high_assurance_hostile_workload_support"] is True
    assert "containerized_oci" in matrix["current_environment"]["available_runner_types"]
    assert "strong_sandbox_vm" in matrix["current_environment"]["available_runner_types"]
    assert "seccomp_profile" in matrix["current_environment"]["available_hardening_controls"]["containerized_oci"]
    assert "sandbox-runtime-hard-limits" in matrix["current_environment"]["available_hardening_controls"]["strong_sandbox_vm"]


def test_sandbox_platform_matrix_reports_windows_degraded_support(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "runner")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Windows")

    matrix = sandbox_platform_capability_matrix()

    assert matrix["current_platform"] == "windows"
    assert matrix["support_contract"]["strong_sandbox_supported_host_platforms"] == [
        "linux"
    ]
    assert matrix["current_environment"]["production_safe_third_party_plugin_execution"] is False
    assert matrix["current_environment"]["support_levels"]["contained_process"]["support"] == "supported"
    assert matrix["current_environment"]["support_levels"]["container_sandbox"]["support"] == "supported"
    assert matrix["current_environment"]["support_levels"]["strong_sandbox"]["support"] == "unsupported"
    assert matrix["current_environment"]["equivalence_status"] == "non-equivalent-container-grade-only"
    assert matrix["current_environment"]["highest_supported_assurance_class"] == "container-grade-sandbox"
    assert matrix["current_environment"]["high_assurance_hostile_workload_support"] is False
    assert "containerized_oci" in matrix["current_environment"]["available_runner_types"]
    assert "strong_sandbox_vm" not in matrix["current_environment"]["available_runner_types"]
    assert any(
        "linux-only kernel controls" in entry.lower()
        for entry in matrix["current_environment"]["degraded_modes"]
    )
    assert any(
        "strong_sandbox_vm" in entry.lower()
        for entry in matrix["current_environment"]["degraded_modes"]
    )


def test_sandbox_platform_matrix_reports_macos_without_strong_sandbox_support(monkeypatch):
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix
    import AINDY.platform_layer.sandbox_runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner.shutil, "which", lambda _: "runner")
    monkeypatch.setattr(sandbox_runner.platform, "system", lambda: "Darwin")

    matrix = sandbox_platform_capability_matrix()

    assert matrix["current_platform"] == "darwin"
    assert matrix["support_contract"]["hostile_third_party_supported_host_platforms"] == [
        "linux"
    ]
    assert matrix["current_environment"]["support_levels"]["container_sandbox"]["support"] == "supported"
    assert matrix["current_environment"]["support_levels"]["strong_sandbox"]["support"] == "unsupported"
    assert matrix["current_environment"]["equivalence_status"] == "non-equivalent-container-grade-only"
    assert matrix["current_environment"]["highest_supported_assurance_class"] == "container-grade-sandbox"
    assert matrix["current_environment"]["high_assurance_hostile_workload_support"] is False
    assert any(
        "native macos kernel policy enforcement" in entry.lower()
        for entry in matrix["current_environment"]["degraded_modes"]
    )
