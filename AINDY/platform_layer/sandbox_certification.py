from __future__ import annotations

from typing import Any

from AINDY.platform_layer.sandbox_runner import (
    RUNNER_CONTAINERIZED_OCI,
    RUNNER_INSECURE_DEV_SUBPROCESS,
    SANDBOX_RUNNER_INTERFACE_VERSION,
    create_sandbox_runner,
)

SANDBOX_CERTIFICATION_SCHEMA_VERSION = "2026-05-21"


def sandbox_certification_contract() -> dict[str, Any]:
    return {
        "schema_version": SANDBOX_CERTIFICATION_SCHEMA_VERSION,
        "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
        "shared_worker_policy_checks": [
            {
                "id": "blocked_internal_imports",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin admission must block direct imports of internal "
                    "runtime modules outside the supported extension API."
                ),
            },
            {
                "id": "blocked_filesystem_writes",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin execution must deny filesystem writes from the "
                    "isolated worker path."
                ),
            },
            {
                "id": "blocked_out_of_policy_network_access",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin execution must deny outbound access without the "
                    "required capability and private/loopback targets remain blocked by policy."
                ),
            },
            {
                "id": "denied_capabilities",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Runtime capability checks must deny ungranted runtime operations for "
                    "third-party plugins."
                ),
            },
            {
                "id": "quarantine_behavior",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "quarantine_after_repeated_failure",
                "notes": (
                    "Repeated plugin-host failures must move the extension into explicit "
                    "backoff/quarantine instead of retrying forever."
                ),
            },
            {
                "id": "provenance_rejection",
                "verification_mode": "end_to_end_behavior",
                "expected_outcome": "fail_closed",
                "notes": (
                    "Third-party plugin admission must reject missing or unverifiable "
                    "provenance/integrity declarations."
                ),
            },
        ],
        "runner_specific_checks": [
            {
                "id": "runner_identity_reporting",
                "verification_mode": "attestation_and_runtime_state",
                "expected_outcome": "reported",
                "notes": (
                    "Operator surfaces must expose the actual runner type and isolation class."
                ),
            },
            {
                "id": "hard_resource_limits",
                "verification_mode": "attestation_and_fail_closed",
                "expected_outcome": (
                    "enforced_when_supported_and_configured"
                ),
                "notes": (
                    "Hard resource limits are only certifiable when the active runner "
                    "reports container-runtime enforcement. Timeout-only containment "
                    "must not be treated as equivalent."
                ),
            },
        ],
        "operator_note": (
            "This contract describes what the certification suite verifies. Shared worker-policy "
            "checks are exercised end to end against isolated plugin execution. Runner-specific "
            "checks certify only the guarantees that the active runner can actually expose "
            "or enforce."
        ),
    }


def sandbox_certification_profile(
    *,
    runner_type: str,
    runner_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(runner_metadata or create_sandbox_runner(runner_type).metadata())
    resource_limits = dict(metadata.get("resource_limits") or {})
    hard_limits_active = (
        runner_type == RUNNER_CONTAINERIZED_OCI
        and resource_limits.get("enforcement") == "container-runtime-hard-limits"
    )
    shared_worker_status = (
        "certifiable-shared-worker-policy"
        if runner_type in {RUNNER_INSECURE_DEV_SUBPROCESS, RUNNER_CONTAINERIZED_OCI}
        else "unsupported"
    )
    runner_checks = []
    for check in sandbox_certification_contract()["runner_specific_checks"]:
        check_record = dict(check)
        if check["id"] == "hard_resource_limits":
            if hard_limits_active:
                check_record["runner_status"] = "certifiable"
                check_record["effective_enforcement"] = resource_limits.get("enforcement")
            else:
                check_record["runner_status"] = "not_certifiable_for_runner"
                check_record["effective_enforcement"] = resource_limits.get("enforcement")
                check_record["reason"] = (
                    "The active runner does not report container-runtime hard resource limits."
                )
        else:
            check_record["runner_status"] = "certifiable"
        runner_checks.append(check_record)

    return {
        "schema_version": SANDBOX_CERTIFICATION_SCHEMA_VERSION,
        "runner_type": runner_type,
        "shared_worker_policy_status": shared_worker_status,
        "resource_limit_enforcement": resource_limits.get("enforcement"),
        "checks": [
            *[
                {
                    **dict(check),
                    "runner_status": shared_worker_status,
                }
                for check in sandbox_certification_contract()["shared_worker_policy_checks"]
            ],
            *runner_checks,
        ],
        "operator_note": (
            "This profile is not itself a certification result. It tells operators and CI "
            "which guarantees the current runner mode is expected to satisfy and which "
            "claims must remain unsupported."
        ),
    }
