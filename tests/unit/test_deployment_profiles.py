from __future__ import annotations

import pytest

from AINDY.platform_layer.deployment_contract import (
    DEPLOYMENT_PROFILE_DISTRIBUTED_API,
    DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER,
    DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY,
    DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
    deployment_contract_summary,
    publish_api_runtime_state,
    reset_runtime_state,
    resolve_api_deployment_profile,
    validate_api_deployment_profile,
    validate_worker_deployment_profile,
)
from AINDY.platform_layer import health_service


class _HealthyBackend:
    degraded = False
    fallback_reason = None


def _ok_dependency(name: str, *, critical: bool = False) -> health_service.DependencyStatus:
    return health_service.DependencyStatus(name=name, status="ok", critical=critical)


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch):
    monkeypatch.delenv("AINDY_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.delenv("AINDY_EVENT_BUS_ENABLED", raising=False)
    reset_runtime_state()
    yield
    reset_runtime_state()


def test_single_instance_profile_is_inferred_from_thread_mode(monkeypatch):
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "thread")

    profile_name, source = resolve_api_deployment_profile()

    assert profile_name == DEPLOYMENT_PROFILE_SINGLE_INSTANCE
    assert source == "derived:EXECUTION_MODE"


def test_explicit_single_instance_profile_rejects_distributed_execution(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_SINGLE_INSTANCE)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")

    with pytest.raises(RuntimeError, match="single-instance profile requires EXECUTION_MODE=thread"):
        validate_api_deployment_profile()


def test_distributed_api_profile_requires_redis(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")

    with pytest.raises(RuntimeError, match="requires REDIS_URL"):
        validate_api_deployment_profile()


def test_distributed_api_profile_rejects_ambiguous_plugin_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "auto")

    with pytest.raises(RuntimeError, match="AINDY_PLUGIN_SANDBOX_RUNNER=auto is not allowed"):
        validate_api_deployment_profile()


def test_distributed_api_profile_rejects_insecure_dev_plugin_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "insecure_dev_subprocess")

    with pytest.raises(RuntimeError, match="does not permit AINDY_PLUGIN_SANDBOX_RUNNER=insecure_dev_subprocess"):
        validate_api_deployment_profile()


def test_distributed_api_profile_accepts_explicit_container_plugin_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "containerized_oci")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(
        "AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST",
        "sha256:" + ("a" * 64),
    )
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "docker")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Linux")

    profile = validate_api_deployment_profile()

    assert profile["plugin_sandbox_policy"]["configured_runner"] == "containerized_oci"
    assert profile["plugin_sandbox_policy"]["resolved_runner"] == "containerized_oci"
    assert profile["plugin_sandbox_policy"]["runtime_identity"]["pinned"] is True
    assert profile["plugin_sandbox_policy"]["runtime_identity"]["launch_reference"].endswith(
        "@sha256:" + ("a" * 64)
    )
    assert profile["plugin_sandbox_policy"]["platform_matrix"]["current_platform"] == "linux"


def test_distributed_api_profile_accepts_explicit_strong_plugin_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "strong_sandbox_vm")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "ghcr.io/example/aindy-strong-sandbox:test")
    monkeypatch.setattr(
        "AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST",
        "sha256:" + ("b" * 64),
    )
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "sandbox")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Linux")

    profile = validate_api_deployment_profile()

    assert profile["plugin_sandbox_policy"]["configured_runner"] == "strong_sandbox_vm"
    assert profile["plugin_sandbox_policy"]["resolved_runner"] == "strong_sandbox_vm"
    assert profile["plugin_sandbox_policy"]["runtime_identity"]["pinned"] is True
    assert profile["plugin_sandbox_policy"]["runtime_identity"]["launch_reference"].endswith(
        "@sha256:" + ("b" * 64)
    )
    assert profile["plugin_sandbox_policy"]["platform_matrix"]["current_platform"] == "linux"


def test_hostile_third_party_profile_accepts_explicit_strong_plugin_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "strong_sandbox_vm")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "ghcr.io/example/aindy-strong-sandbox:test")
    monkeypatch.setattr(
        "AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST",
        "sha256:" + ("c" * 64),
    )
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "sandbox")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Linux")

    profile = validate_api_deployment_profile()

    assert profile["name"] == DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY
    assert profile["plugin_sandbox_policy"]["resolved_runner"] == "strong_sandbox_vm"
    assert profile["plugin_sandbox_policy"]["assurance_class"] == "strong-sandbox-tier"
    assert profile["plugin_sandbox_policy"]["runtime_identity"]["pinned"] is True
    assert (
        profile["plugin_sandbox_policy"]["hostile_third_party_attestation_requirements"]["required_runner_type"]
        == "strong_sandbox_vm"
    )


def test_distributed_api_profile_rejects_unpinned_container_runtime_identity(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "containerized_oci")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST", "")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "docker")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Linux")

    with pytest.raises(RuntimeError, match="requires a pinned sandbox runtime identity"):
        validate_api_deployment_profile()


def test_distributed_api_profile_rejects_unpinned_strong_runtime_identity(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "strong_sandbox_vm")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "ghcr.io/example/aindy-strong-sandbox:test")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST", "")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "sandbox")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Linux")

    with pytest.raises(RuntimeError, match="requires a pinned sandbox runtime identity"):
        validate_api_deployment_profile()


def test_worker_profile_requires_distributed_mode(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "thread")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")

    with pytest.raises(RuntimeError, match="requires EXECUTION_MODE=distributed"):
        validate_worker_deployment_profile()


def test_distributed_worker_profile_rejects_ambiguous_plugin_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_WORKER)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "auto")

    with pytest.raises(RuntimeError, match="requires an explicit third-party plugin sandbox runner"):
        validate_worker_deployment_profile()


def test_distributed_api_profile_rejects_non_linux_container_sandbox_host(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "containerized_oci")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(
        "AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST",
        "sha256:" + ("e" * 64),
    )
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "docker")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Windows")

    with pytest.raises(RuntimeError, match="requires a Linux host with compatible container sandbox support"):
        validate_api_deployment_profile()


def test_distributed_api_profile_rejects_unavailable_strong_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "strong_sandbox_vm")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: None)
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Windows")

    with pytest.raises(RuntimeError, match="AINDY_PLUGIN_STRONG_SANDBOX_IMAGE|requires a Linux host with compatible strong sandbox VM support"):
        validate_api_deployment_profile()


def test_hostile_third_party_profile_rejects_non_linux_high_assurance_host(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "strong_sandbox_vm")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE", "ghcr.io/example/aindy-strong-sandbox:test")
    monkeypatch.setattr(
        "AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST",
        "sha256:" + ("f" * 64),
    )
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "sandbox")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Windows")

    with pytest.raises(RuntimeError, match="requires a Linux host with compatible strong sandbox VM support|does not provide that support"):
        validate_api_deployment_profile()


def test_hostile_third_party_profile_rejects_container_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "containerized_oci")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE", "ghcr.io/example/aindy-runtime:test")
    monkeypatch.setattr(
        "AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST",
        "sha256:" + ("d" * 64),
    )
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.shutil.which", lambda _name: "docker")
    monkeypatch.setattr("AINDY.platform_layer.sandbox_runner.platform.system", lambda: "Linux")

    with pytest.raises(RuntimeError, match="requires AINDY_PLUGIN_SANDBOX_RUNNER=strong_sandbox_vm"):
        validate_api_deployment_profile()


def test_hostile_third_party_profile_rejects_insecure_runner(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_HOSTILE_THIRD_PARTY)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "distributed")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.REDIS_URL", "redis://example")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.AINDY_PLUGIN_SANDBOX_RUNNER", "insecure_dev_subprocess")

    with pytest.raises(RuntimeError, match="requires AINDY_PLUGIN_SANDBOX_RUNNER=strong_sandbox_vm|does not permit AINDY_PLUGIN_SANDBOX_RUNNER=insecure_dev_subprocess"):
        validate_api_deployment_profile()


def test_distributed_api_missing_worker_is_startup_fatal_in_production(monkeypatch):
    import AINDY.startup as startup

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_DISTRIBUTED_API)
    monkeypatch.setattr(startup.settings, "ENV", "production")
    monkeypatch.setattr(startup.settings, "TESTING", False)
    monkeypatch.setattr(startup.settings, "TEST_MODE", False)
    monkeypatch.setattr(startup.settings, "EXECUTION_MODE", "distributed")
    monkeypatch.setattr(startup.settings, "REDIS_URL", "redis://example")
    monkeypatch.setattr(startup.settings, "AINDY_CACHE_BACKEND", "redis")
    monkeypatch.setattr(startup, "validate_queue_backend", lambda: _HealthyBackend())
    monkeypatch.setattr(startup, "_check_worker_presence", lambda _log: False)

    with pytest.raises(RuntimeError, match="no worker heartbeat detected"):
        startup._validate_queue_and_workers()


def test_readiness_reports_active_deployment_profile(monkeypatch):
    monkeypatch.setattr(health_service.settings, "TESTING", False)
    monkeypatch.setattr(health_service.settings, "TEST_MODE", False)
    monkeypatch.setattr(health_service.settings, "ENV", "development")
    publish_api_runtime_state(
        process_role="api",
        startup_complete=True,
        background_enabled=False,
        scheduler_role="disabled",
        background_leadership_mode="in-process",
        deployment_profile=DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
        deployment_profile_source="derived:EXECUTION_MODE",
    )
    monkeypatch.setattr(health_service, "check_postgres", lambda: _ok_dependency("postgres", critical=True))
    monkeypatch.setattr(health_service, "check_redis", lambda: _ok_dependency("redis"))
    monkeypatch.setattr(health_service, "check_queue", lambda: _ok_dependency("queue"))
    monkeypatch.setattr(health_service, "check_event_bus", lambda: _ok_dependency("event_bus"))
    monkeypatch.setattr(health_service, "check_mongo", lambda: _ok_dependency("mongo"))
    monkeypatch.setattr(health_service, "check_schema", lambda: _ok_dependency("schema", critical=True))
    monkeypatch.setattr(health_service, "check_ai_providers", lambda: _ok_dependency("ai_providers"))
    monkeypatch.setattr(health_service, "get_degraded_domains", lambda: [])

    status_code, payload = health_service.get_readiness_report()

    assert status_code == 200
    assert payload["checks"]["deployment_profile"] == DEPLOYMENT_PROFILE_SINGLE_INSTANCE
    assert payload["checks"]["background_leadership_mode"] == "in-process"
    assert payload["checks"]["plugin_sandbox_posture"] == {
        "deployment_profile": DEPLOYMENT_PROFILE_SINGLE_INSTANCE,
        "current": {
            "runner_type": "insecure_dev_subprocess",
            "assurance_class": "insecure-dev",
            "certification_tier": "contained-process-certified",
            "certification_status": "certified",
        },
        "required": {
            "assurance_class": None,
            "runner_type": None,
            "certification_tier": None,
        },
        "requirement_status": {
            "assurance_class_satisfied": True,
            "certification_tier_satisfied": True,
        },
        "unsupported_claims": [
            "general third-party sandboxing",
            "hard resource-limit enforcement",
            "kernel-level isolation guarantees",
        ],
        "distinction_note": (
            "Assurance class describes the runner category, attestation describes what the runtime "
            "observed, and certification describes what the runtime can justify from verified evidence."
        ),
        "notes": "This profile does not require a third-party sandbox assurance class.",
    }
    assert "must not be conflated" in payload["readiness_scope"]
    assert "does not imply stronger sandbox guarantees" in payload["readiness_scope"]


def test_deployment_contract_summary_reports_active_profile(monkeypatch):
    monkeypatch.setenv("AINDY_DEPLOYMENT_PROFILE", DEPLOYMENT_PROFILE_SINGLE_INSTANCE)
    monkeypatch.setattr("AINDY.platform_layer.deployment_contract.settings.EXECUTION_MODE", "thread")

    summary = deployment_contract_summary()

    assert summary["active_profile"]["name"] == DEPLOYMENT_PROFILE_SINGLE_INSTANCE
    assert summary["active_profile"]["source"] == "AINDY_DEPLOYMENT_PROFILE"
    assert summary["release_posture"]["support_tier"] == "trusted-internal"
