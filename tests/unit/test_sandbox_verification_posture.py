import pytest

from AINDY.platform_layer.sandbox_runner import (
    RUNNER_STRONG_SANDBOX_VM,
    VERIFICATION_METHOD_KERNEL_OBSERVABLE,
    VERIFICATION_METHOD_NONE,
    VERIFICATION_METHOD_WORKER_SELF_REPORT,
    list_supported_sandbox_runners,
    sandbox_runner_assurance_posture,
)

pytestmark = pytest.mark.runtime_only


def test_verification_method_constants_are_stable():
    assert VERIFICATION_METHOD_WORKER_SELF_REPORT == "worker-self-report"
    assert VERIFICATION_METHOD_KERNEL_OBSERVABLE == "kernel-observable"
    assert VERIFICATION_METHOD_NONE == "none"


def test_supported_runners_with_assurance_ceiling_publish_ceiling_note():
    for runner in list_supported_sandbox_runners():
        assert runner["assurance_ceiling"]
        assert runner["ceiling_note"]


def test_strong_sandbox_vm_ceiling_is_worker_self_report_verified():
    posture = sandbox_runner_assurance_posture(RUNNER_STRONG_SANDBOX_VM)
    assert posture["assurance_ceiling"] in {
        "worker-self-report-verified",
        "kernel-observable-verified",
    }


def test_version_route_publishes_verification_method_and_assurance_ceiling(runtime_only_client):
    payload = runtime_only_client.get("/api/version").json()

    public_runners = {
        entry["runner_type"]: entry
        for entry in payload["public_contract"]["extensions"]["sandbox_runners"]["available_runners"]
    }
    strong_public_runner = public_runners[RUNNER_STRONG_SANDBOX_VM]
    assert strong_public_runner["assurance_ceiling"] in {
        "worker-self-report-verified",
        "kernel-observable-verified",
    }
    assert strong_public_runner["verification_method"] in {
        VERIFICATION_METHOD_WORKER_SELF_REPORT,
        VERIFICATION_METHOD_KERNEL_OBSERVABLE,
    }

    runtime_runners = {
        entry["runner_type"]: entry
        for entry in payload["runtime"]["plugin_hosts"]["available_runners"]
    }
    strong_runtime_runner = runtime_runners[RUNNER_STRONG_SANDBOX_VM]
    assert strong_runtime_runner["assurance_ceiling"] in {
        "worker-self-report-verified",
        "kernel-observable-verified",
    }
    assert strong_runtime_runner["verification_method"] in {
        VERIFICATION_METHOD_WORKER_SELF_REPORT,
        VERIFICATION_METHOD_KERNEL_OBSERVABLE,
    }


def test_health_route_publishes_sandbox_verification_posture(runtime_only_client):
    payload = runtime_only_client.get("/health").json()

    posture = payload["sandbox_verification_posture"]
    assert posture["verification_method"] in {
        VERIFICATION_METHOD_WORKER_SELF_REPORT,
        VERIFICATION_METHOD_KERNEL_OBSERVABLE,
    }
    assert isinstance(posture["kernel_observable"], bool)
    assert posture["gap_reference"]
