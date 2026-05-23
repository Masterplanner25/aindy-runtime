from __future__ import annotations

from typing import Any

from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    OWNER_FIRST_PARTY_APP,
    OWNER_RUNTIME_BUILTIN,
)
from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix

EXECUTION_MODEL_KERNEL_RESIDENT = "kernel-resident"
EXECUTION_MODEL_ISOLATED_EXTERNALIZED = "isolated-externalized"
EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION = (
    "capability-confined-in-process-exception"
)
EXTENSION_EXECUTION_MODEL_SCHEMA_VERSION = "2026-05-22"

ALL_CHARACTERIZED_HOST_PLATFORMS = ["linux", "windows", "darwin", "other"]
STRONG_SANDBOX_HOST_PLATFORMS = ["linux"]


def extension_execution_model_contract() -> dict[str, Any]:
    platform_matrix = sandbox_platform_capability_matrix()
    support_contract = dict(platform_matrix.get("support_contract") or {})
    return {
        "schema_version": EXTENSION_EXECUTION_MODEL_SCHEMA_VERSION,
        "execution_model_classes": [
            {
                "id": EXECUTION_MODEL_KERNEL_RESIDENT,
                "meaning": (
                    "Runtime-owned or runtime-executed behavior that runs directly in the "
                    "main interpreter and is not sandboxed."
                ),
            },
            {
                "id": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "meaning": (
                    "Extension behavior externalized behind a runtime-owned worker, plugin host, "
                    "or network contract boundary."
                ),
            },
            {
                "id": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "meaning": (
                    "Residual in-process extension behavior confined only by explicit runtime-owned "
                    "capability mediation on official kernel APIs. This is not sandboxing."
                ),
            },
        ],
        "surface_matrix": [
            {
                "surface_id": "manifest-bootstrap:runtime-built-in",
                "surface": "manifest bootstrap modules",
                "owner_class": OWNER_RUNTIME_BUILTIN,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "execution_path": "in-process bootstrap import plus runtime-owned registration capability checks",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "trusted_python_execution.manifest_modules",
                "notes": (
                    "This is the residual in-process privileged exception for runtime-owned bootstrap code."
                ),
            },
            {
                "surface_id": "manifest-bootstrap:first-party-app",
                "surface": "manifest bootstrap modules",
                "owner_class": OWNER_FIRST_PARTY_APP,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "execution_path": "in-process bootstrap import plus restricted runtime-owned registration allowlist",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "trusted_python_execution.manifest_modules",
                "notes": (
                    "First-party bootstrap remains an explicit in-process exception and is not sandboxed."
                ),
            },
            {
                "surface_id": "manifest-bootstrap:external-third-party",
                "surface": "manifest bootstrap modules",
                "owner_class": OWNER_EXTERNAL_THIRD_PARTY,
                "supported": False,
                "execution_model_class": None,
                "execution_path": "unsupported",
                "registration_boundary": None,
                "platform_support": {
                    "supported_host_platforms": [],
                },
                "operator_surface": "public_contract.extensions.experimental[manifest bootstrap modules]",
                "notes": (
                    "External third-party bootstrap Python is blocked because bootstrap import/registration "
                    "would execute in-process."
                ),
            },
            {
                "surface_id": "manifest-declarative-entry:any-owner",
                "surface": "manifest declarative extension entries",
                "owner_class": "any-supported-owner",
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_KERNEL_RESIDENT,
                "execution_path": "kernel-resident manifest parsing and validation",
                "registration_boundary": EXECUTION_MODEL_KERNEL_RESIDENT,
                "delegates_to_surface_ids": [
                    "dynamic-plugin-node:runtime-built-in",
                    "dynamic-plugin-node:first-party-app",
                    "dynamic-plugin-node:external-third-party",
                    "webhook-node:any-owner",
                    "webhook-subscription:any-owner",
                    "dynamic-flow:any-owner",
                ],
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "public_contract.extensions.experimental[manifest declarative extension entries]",
                "notes": (
                    "The manifest entry itself is declarative data. Actual execution follows the concrete "
                    "surface it registers."
                ),
            },
            {
                "surface_id": "registry-kernel-callable:runtime-built-in",
                "surface": "runtime registry callables",
                "owner_class": OWNER_RUNTIME_BUILTIN,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_KERNEL_RESIDENT,
                "execution_path": "in-process callable execution from kernel-owned registries",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "examples": [
                    "syscalls",
                    "jobs",
                    "event handlers",
                    "response adapters",
                    "route guards",
                    "agent tools",
                    "planner backends",
                    "flow strategies",
                ],
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "extensions.execution_models.surface_matrix",
                "notes": (
                    "These runtime-owned callables remain kernel-resident after registration."
                ),
            },
            {
                "surface_id": "registry-kernel-callable:first-party-app",
                "surface": "runtime registry callables",
                "owner_class": OWNER_FIRST_PARTY_APP,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_KERNEL_RESIDENT,
                "execution_path": "in-process callable execution from kernel-owned registries",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "examples": [
                    "syscalls",
                    "jobs",
                    "event handlers",
                    "response adapters",
                    "route guards",
                    "agent tools",
                    "planner backends",
                    "flow strategies",
                ],
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "extensions.execution_models.surface_matrix",
                "notes": (
                    "First-party callable registrations are still privileged in-process once registered. "
                    "Only registration itself is capability-mediated."
                ),
            },
            {
                "surface_id": "runtime-callback-worker:runtime-built-in",
                "surface": "startup and provider callback workers",
                "owner_class": OWNER_RUNTIME_BUILTIN,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "execution_path": "runtime-owned isolated callback worker subprocess",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "callback_types": [
                    "startup hooks",
                    "planner context providers",
                    "run tool providers",
                    "trigger evaluators",
                    "agent completion hooks",
                    "capability definition providers",
                ],
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "registry.runtime_callback_invocations",
                "notes": (
                    "Only the callback invocation path is externalized; registration still occurs during bootstrap."
                ),
            },
            {
                "surface_id": "runtime-callback-worker:first-party-app",
                "surface": "startup and provider callback workers",
                "owner_class": OWNER_FIRST_PARTY_APP,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "execution_path": "runtime-owned isolated callback worker subprocess",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "callback_types": [
                    "startup hooks",
                    "planner context providers",
                    "run tool providers",
                    "trigger evaluators",
                    "agent completion hooks",
                    "capability definition providers",
                ],
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "registry.runtime_callback_invocations",
                "notes": (
                    "First-party callback-style provider execution is externalized where the runtime can resolve "
                    "the callback as a concrete module function."
                ),
            },
            {
                "surface_id": "dynamic-plugin-node:runtime-built-in",
                "surface": "dynamic plugin nodes",
                "owner_class": OWNER_RUNTIME_BUILTIN,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_KERNEL_RESIDENT,
                "execution_path": "in-process node callable execution",
                "registration_boundary": EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "trusted_python_execution.plugin_nodes",
                "notes": (
                    "Runtime-built-in plugin nodes remain kernel-resident and are not sandboxed."
                ),
            },
            {
                "surface_id": "dynamic-plugin-node:first-party-app",
                "surface": "dynamic plugin nodes",
                "owner_class": OWNER_FIRST_PARTY_APP,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "execution_path": "isolated plugin host over authenticated runtime RPC",
                "registration_boundary": EXECUTION_MODEL_KERNEL_RESIDENT,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                    "strong_sandbox_supported_host_platforms": list(
                        support_contract.get("strong_sandbox_supported_host_platforms") or STRONG_SANDBOX_HOST_PLATFORMS
                    ),
                },
                "operator_surface": "plugin_sandbox_attestation.hosts",
                "notes": (
                    "First-party dynamic plugin nodes use the same isolated plugin-host boundary as third-party nodes."
                ),
            },
            {
                "surface_id": "dynamic-plugin-node:external-third-party",
                "surface": "dynamic plugin nodes",
                "owner_class": OWNER_EXTERNAL_THIRD_PARTY,
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "execution_path": "isolated plugin host over authenticated runtime RPC",
                "registration_boundary": EXECUTION_MODEL_KERNEL_RESIDENT,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                    "production_safe_host_platforms": list(
                        support_contract.get("production_safe_third_party_supported_host_platforms")
                        or []
                    ),
                    "strong_sandbox_supported_host_platforms": list(
                        support_contract.get("strong_sandbox_supported_host_platforms") or STRONG_SANDBOX_HOST_PLATFORMS
                    ),
                    "hostile_third_party_supported_host_platforms": list(
                        support_contract.get("hostile_third_party_supported_host_platforms")
                        or STRONG_SANDBOX_HOST_PLATFORMS
                    ),
                },
                "operator_surface": "plugin_sandbox_attestation.hosts",
                "notes": (
                    "External third-party plugin nodes require verified artifacts and isolated execution."
                ),
            },
            {
                "surface_id": "webhook-node:any-owner",
                "surface": "webhook nodes",
                "owner_class": "any-supported-owner",
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "execution_path": "contract-driven outbound HTTP integration",
                "registration_boundary": EXECUTION_MODEL_KERNEL_RESIDENT,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "node_registry.dynamic_nodes[type=webhook]",
                "notes": (
                    "Webhook nodes are externalized by network boundary, not by local Python import."
                ),
            },
            {
                "surface_id": "webhook-subscription:any-owner",
                "surface": "webhook subscriptions",
                "owner_class": "any-supported-owner",
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "execution_path": "contract-driven outbound webhook delivery",
                "registration_boundary": EXECUTION_MODEL_KERNEL_RESIDENT,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "webhook_subscriptions",
                "notes": (
                    "Webhook subscriptions are runtime-owned delivery contracts, not in-process extension code."
                ),
            },
            {
                "surface_id": "dynamic-flow:any-owner",
                "surface": "dynamic flows",
                "owner_class": "any-supported-owner",
                "supported": True,
                "execution_model_class": EXECUTION_MODEL_KERNEL_RESIDENT,
                "execution_path": "data-only flow definition executed by the runtime flow engine",
                "registration_boundary": EXECUTION_MODEL_KERNEL_RESIDENT,
                "platform_support": {
                    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
                },
                "operator_surface": "dynamic_flows",
                "notes": (
                    "Dynamic flows do not execute extension Python; the runtime executes the flow graph itself."
                ),
            },
        ],
        "attestation_scope": {
            "plugin_sandbox_attestation": {
                "covered_execution_model_class": EXECUTION_MODEL_ISOLATED_EXTERNALIZED,
                "covered_surface_ids": [
                    "dynamic-plugin-node:first-party-app",
                    "dynamic-plugin-node:external-third-party",
                ],
                "excluded_surface_ids": [
                    "manifest-bootstrap:runtime-built-in",
                    "manifest-bootstrap:first-party-app",
                    "registry-kernel-callable:runtime-built-in",
                    "registry-kernel-callable:first-party-app",
                    "dynamic-plugin-node:runtime-built-in",
                    "dynamic-flow:any-owner",
                ],
                "notes": (
                    "Plugin sandbox attestation and certification describe isolated plugin-host execution only. "
                    "They do not cover kernel-resident or capability-confined in-process bootstrap surfaces."
                ),
            },
            "deployment_profile_enforcement": {
                "covered_surface_ids": [
                    "dynamic-plugin-node:external-third-party",
                ],
                "notes": (
                    "Deployment-profile sandbox gating currently applies only to external third-party dynamic plugin nodes."
                ),
            },
        },
        "operator_note": (
            "This matrix is the authoritative execution-model taxonomy for extension classes. "
            "It distinguishes kernel-resident behavior, isolated externalized behavior, and the residual "
            "capability-confined in-process bootstrap exception without implying sandboxing where none exists."
        ),
    }
