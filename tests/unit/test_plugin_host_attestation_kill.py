"""SANDBOX-EVIDENCE-1 — the hostile-third-party POST-LAUNCH kill, witnessed.

Under ``hostile-third-party`` admission is checked twice. The pre-spawn policy refusal was
pinned; the post-launch attestation (``_start_record``: live snapshot fails
``hostile_third_party_attestation_violations`` -> mark ``contract_violation`` -> force-kill ->
raise) had no test. These tests launch a strong runner whose launch attestation and live
probe are REAL code over a fake process — the argv-derived launch attestation is the
production one, the probe is what a healthy worker reports — and then break exactly one
attestation field, so the branch is reached by the real check, not by a stub of it.

The assertion that matters is that the PROCESS IS DEAD. A test that only reads the raised
message passes with the worker still alive — that is the hole the entry names.

Harness notes: ``metadata()`` is the real ``StrongSandboxVmRunner`` one under the same
settings the deployment-profile tests use, so the pre-spawn policy passes for real;
``_spawn_process`` swaps ``subprocess.Popen`` for a fake whose ``poll()`` flips on
``terminate()``/``kill()``; ``_send_command`` answers ``start``/``probe``/``heartbeat``
in-process. ``pid()`` is non-None so the kernel-evidence path runs and (off Linux) reports
"not observable", leaving the worker's self-report as the verification method — the same
degradation a Windows or macOS host sees.
"""

from __future__ import annotations

import pytest

from AINDY.platform_layer import sandbox_runner as sr

pytestmark = pytest.mark.runtime_only

_PLUGIN = "hostile-attested-plugin"


class _FakeProcess:
    """Enough of ``subprocess.Popen`` for the base runner's lifecycle methods."""

    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.kill_calls = 0
        self.terminate_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _passing_probe(start_context: dict) -> dict:
    """What a healthy strong-sandbox worker reports: every field ``_verify_post_launch_state``
    reads, satisfied, echoing the identity the host handed it on ``start``."""
    plugin_context = dict(start_context.get("plugin_context") or start_context)
    verified = {"verified": True}
    return {
        "worker_instance_id": "worker-1",
        "session_continuity": {
            "extension_name": plugin_context.get("extension_name"),
            "owner_class": plugin_context.get("owner_class"),
            "sandbox_instance_id": (plugin_context.get("runtime_api") or {}).get("sandbox_instance_id"),
            "started": True,
        },
        "isolation_state": {
            "import_guard_active": True,
            "filesystem_guard_active": True,
            "network_guard_active": True,
        },
        "boundary_metadata": {"runtime_api_channel_hidden": True},
        "mount_network_state": {
            "artifact_read_access": dict(verified),
            "artifact_write_blocked": dict(verified),
            "writable_temp_scope": dict(verified),
            "host_path_access_blocked": dict(verified),
            "network_policy": {
                "socket_guard_active": dict(verified),
                "deny_by_default_outbound": dict(verified),
                "private_target_blocking": dict(verified),
                "expected_boundary_mode": dict(verified),
            },
        },
    }


class _FakeLaunchedStrongRunner(sr.StrongSandboxVmRunner):
    """The real strong runner with the process boundary faked.

    ``tamper_launch`` mutates the argv-derived launch attestation after spawn (a launcher that
    did something other than asked); ``tamper_probe`` mutates the live probe (a worker that
    is not where it should be). Each test breaks at most one.
    """

    instances: list["_FakeLaunchedStrongRunner"] = []

    def __init__(self) -> None:
        super().__init__()
        self.process: _FakeProcess | None = None
        self.shutdown_calls: list[bool] = []
        self.start_context: dict = {}
        self.tamper_launch = None
        self.tamper_probe = None
        type(self).instances.append(self)

    def _spawn_process(self, plugin_root) -> None:
        args = self._process_args(plugin_root)
        self._launch_attestation = self._build_launch_attestation(args=list(args), plugin_root=plugin_root)
        if self.tamper_launch is not None:
            self.tamper_launch(self._launch_attestation)
        self.process = _FakeProcess()
        self._process = self.process

    def _send_command(self, command, *, timeout_seconds):
        name = command.get("command")
        if name == "start":
            self.start_context = dict(command.get("context") or {})
            return {"ok": True, "provenance": {}}
        if name == "probe":
            probe = _passing_probe(self.start_context)
            if self.tamper_probe is not None:
                self.tamper_probe(probe)
            return {"ok": True, "probe": probe}
        return {"ok": True}

    def shutdown(self, *, force: bool = False) -> None:
        self.shutdown_calls.append(force)
        super().shutdown(force=force)


@pytest.fixture
def hostile_strong_host(tmp_path, monkeypatch):
    """A hostile-third-party profile whose pre-spawn policy accepts the strong runner, with
    ``plugin_host.create_sandbox_runner`` returning the fake-launched runner."""
    from AINDY.config import settings
    from AINDY.platform_layer import plugin_host
    from AINDY.platform_layer.deployment_contract import (
        get_api_runtime_state,
        publish_api_runtime_state,
    )

    plugin_dir = tmp_path / "plugins" / "nodes"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "safe_node.py").write_text(
        "def handler(state, context):\n    return {'status': 'SUCCESS'}\n", encoding="utf-8"
    )

    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", "hostile-third-party")
    for key, value in {
        "EXECUTION_MODE": "distributed",
        "REDIS_URL": "redis://example",
        "AINDY_CACHE_BACKEND": "redis",
        "AINDY_PLUGIN_SANDBOX_RUNNER": "strong_sandbox_vm",
        "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE": "ghcr.io/example/aindy-strong-sandbox:test",
        "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST": "sha256:" + ("c" * 64),
        "AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_BASE_COMPATIBILITY": "aindy-sandbox-runtime/v1",
        "AINDY_PLUGIN_STRONG_SANDBOX_REQUIRED_BASE_COMPATIBILITY": "aindy-sandbox-runtime/v1",
        "AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SIGNING_STATUS": "signature-verified",
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(sr.shutil, "which", lambda _name: "sandbox")
    monkeypatch.setattr(sr.platform, "system", lambda: "Linux")

    _FakeLaunchedStrongRunner.instances = []
    next_tamper: dict = {"launch": None, "probe": None}

    def _factory(runner_type):
        assert runner_type == "strong_sandbox_vm"
        runner = _FakeLaunchedStrongRunner()
        runner.tamper_launch = next_tamper["launch"]
        runner.tamper_probe = next_tamper["probe"]
        return runner

    monkeypatch.setattr(plugin_host, "create_sandbox_runner", _factory)
    plugin_host.reset_plugin_hosts()
    original_state = get_api_runtime_state()
    publish_api_runtime_state(
        process_role="api",
        deployment_profile="hostile-third-party",
        deployment_profile_source="AINDY_DEPLOYMENT_PROFILE",
    )
    try:
        yield {"plugin_dir": plugin_dir, "next_tamper": next_tamper}
    finally:
        publish_api_runtime_state(**original_state)
        plugin_host.reset_plugin_hosts()


def _start(plugin_dir):
    from AINDY.platform_layer.plugin_host import start_plugin_host

    return start_plugin_host(
        name=_PLUGIN,
        handler="safe_node:handler",
        plugin_root=plugin_dir,
        owner_class="external-third-party",
        granted_capabilities=[],
    )


def _record():
    from AINDY.platform_layer import plugin_host

    return plugin_host._HOSTS[_PLUGIN]


def _unverify_backend(launch_attestation: dict) -> None:
    """The launcher reported a backend other than the one requested."""
    launch_attestation["backend_identity"]["verified"] = False


def _hide_nothing(probe: dict) -> None:
    """The worker reports its runtime-API channel is NOT hidden from plugin code."""
    probe["boundary_metadata"]["runtime_api_channel_hidden"] = False


# -- Liveness control: the harness CAN pass ----------------------------------------------


def test_control_a_fully_attested_launch_is_admitted(hostile_strong_host):
    """Nothing tampered -> running, no kill. Without this, the kill tests below could pass
    because the fake is grossly wrong rather than wrong in the one intended field."""
    snapshot = _start(hostile_strong_host["plugin_dir"])

    runner = _FakeLaunchedStrongRunner.instances[-1]
    assert snapshot["lifecycle_state"] == "running"
    assert snapshot["sandbox_attestation"]["post_launch_verification"]["status"] == "passed"
    assert runner.shutdown_calls == []
    assert runner.process.poll() is None
    assert _record().contract_violations == 0


# -- The kill, through the public entry point --------------------------------------------


def test_attestation_violation_kills_the_worker_on_start(hostile_strong_host):
    hostile_strong_host["next_tamper"]["launch"] = _unverify_backend

    with pytest.raises(
        RuntimeError,
        match=r"attestation requirements were not verified: launch_attestation\.backend_identity",
    ):
        _start(hostile_strong_host["plugin_dir"])

    runner = _FakeLaunchedStrongRunner.instances[-1]
    record = _record()
    # The process is dead — force-killed, not asked politely.
    assert runner.shutdown_calls and runner.shutdown_calls[0] is True
    assert runner.process.poll() is not None
    assert runner.is_running() is False
    assert record.runner is None
    # And the record says why.
    assert record.state == "failed"
    assert record.contract_violations == 1
    assert record.last_failure_kind == "contract_violation"
    assert record.total_failures == 1
    snapshot = record.snapshot()
    # A marked failure opens the restart circuit, and the snapshot reports THAT — the
    # operator sees "backoff" over a dead pid, not a live "running".
    assert snapshot["lifecycle_state"] == "backoff"
    assert snapshot["pid"] is None


# -- The kill, through restart — where _start_record's own kill is the ONLY kill -----------


def test_attestation_violation_kills_the_worker_on_restart(hostile_strong_host):
    from AINDY.platform_layer.plugin_host import restart_plugin_host

    _start(hostile_strong_host["plugin_dir"])
    healthy = _FakeLaunchedStrongRunner.instances[-1]
    hostile_strong_host["next_tamper"]["launch"] = _unverify_backend

    with pytest.raises(RuntimeError, match=r"launch_attestation\.backend_identity"):
        restart_plugin_host(_PLUGIN)

    relaunched = _FakeLaunchedStrongRunner.instances[-1]
    assert relaunched is not healthy
    assert healthy.process.poll() is not None  # the old worker went down with the restart
    assert relaunched.shutdown_calls == [True]
    assert relaunched.process.poll() is not None
    record = _record()
    assert record.runner is None
    assert record.state == "failed"
    assert record.last_failure_kind == "contract_violation"


# -- The OTHER post-launch failure: strong verification, not hostile attestation -----------


def test_failed_post_launch_verification_kills_the_worker_on_restart(hostile_strong_host):
    """``_verify_post_launch_state`` fails (the worker says its runtime-API channel is not
    hidden). Through ``restart_plugin_host`` nothing wraps ``_start_record`` — is the
    worker dead and the record honest?"""
    from AINDY.platform_layer.plugin_host import restart_plugin_host

    _start(hostile_strong_host["plugin_dir"])
    hostile_strong_host["next_tamper"]["probe"] = _hide_nothing

    with pytest.raises(RuntimeError, match=r"post-launch verification failed: .*runtime_api_channel_hidden"):
        restart_plugin_host(_PLUGIN)

    relaunched = _FakeLaunchedStrongRunner.instances[-1]
    record = _record()
    assert relaunched.process.poll() is not None, "worker left alive after failed verification"
    assert relaunched.is_running() is False
    assert record.runner is None
    assert record.state == "failed"
    assert record.snapshot()["lifecycle_state"] == "backoff"
    assert record.snapshot()["pid"] is None


def test_failed_post_launch_verification_kills_the_worker_on_start(hostile_strong_host):
    hostile_strong_host["next_tamper"]["probe"] = _hide_nothing

    with pytest.raises(RuntimeError, match=r"post-launch verification failed"):
        _start(hostile_strong_host["plugin_dir"])

    runner = _FakeLaunchedStrongRunner.instances[-1]
    record = _record()
    assert runner.process.poll() is not None
    assert record.runner is None
    assert record.state == "failed"
