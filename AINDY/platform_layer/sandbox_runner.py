from __future__ import annotations

import json
import os
import platform
import queue
import re
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
RUNNER_STRONG_SANDBOX_VM = "strong_sandbox_vm"
RUNNER_SELECTION_AUTO = "auto"
SUPPORTED_SANDBOX_RUNNERS = (
    RUNNER_INSECURE_DEV_SUBPROCESS,
    RUNNER_CONTAINERIZED_OCI,
    RUNNER_STRONG_SANDBOX_VM,
)

ASSURANCE_CLASS_INSECURE_DEV = "insecure-dev"
ASSURANCE_CLASS_CONTAINER = "container-grade-sandbox"
ASSURANCE_CLASS_STRONG = "strong-sandbox-tier"
OCI_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

PLATFORM_LINUX = "linux"
PLATFORM_WINDOWS = "windows"
PLATFORM_MACOS = "darwin"
PLATFORM_OTHER = "other"
STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)
HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)
PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)
CONTAINER_GRADE_HOST_PLATFORMS = (
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
    PLATFORM_MACOS,
)
CONTAINED_PROCESS_HOST_PLATFORMS = (
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
    PLATFORM_MACOS,
    PLATFORM_OTHER,
)

STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES = {
    "boundary_type": "dedicated-vm-sandbox",
    "process_separation_model": "vm-kernel-boundary",
    "mount_mediation_model": "sandbox-launcher-mediated-read-only",
    "network_mediation_model": "sandbox-launcher-deny-default",
    "runtime_identity_model": "pinned-sandbox-runtime",
    "session_verification_model": "launch-plus-post-launch-runtime-probe",
}


def list_supported_sandbox_runners() -> list[dict[str, Any]]:
    return [
        {
            "runner_type": RUNNER_INSECURE_DEV_SUBPROCESS,
            "assurance_class": ASSURANCE_CLASS_INSECURE_DEV,
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
            "assurance_class": ASSURANCE_CLASS_CONTAINER,
            "stability": "current",
            "isolation_claim": "container-boundary",
            "execution_boundary": "container-stdio-json-rpc",
            "kernel_control_reporting": "explicit",
            "resource_limit_enforcement": "container-runtime-hard-limits",
            "assurance_properties": {
                "boundary_type": "shared-kernel-container-sandbox",
                "process_separation_model": "container-namespace-boundary",
                "mount_mediation_model": "container-runtime-bind-mount-read-only",
                "network_mediation_model": "container-runtime-network-policy",
                "runtime_identity_model": "pinned-oci-image",
                "session_verification_model": "launch-attestation-only",
            },
            "runtime_identity_requirement": "pinned-oci-image-for-production-safe-profiles",
            "operator_note": (
                "This runner launches the extension worker in an operator-supplied "
                "container image with a read-only plugin mount and minimal environment. "
                "It is materially stronger than the dev subprocess runner, but it is "
                "still not a blanket security guarantee."
            ),
        },
        {
            "runner_type": RUNNER_STRONG_SANDBOX_VM,
            "assurance_class": ASSURANCE_CLASS_STRONG,
            "stability": "current",
            "isolation_claim": "vm-boundary",
            "execution_boundary": "vm-stdio-json-rpc",
            "resource_limit_enforcement": "sandbox-runtime-hard-limits",
            "assurance_properties": dict(STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES),
            "runtime_identity_requirement": "pinned-sandbox-image-for-production-safe-profiles",
            "operator_note": (
                "This runner targets a dedicated VM-grade sandbox launcher that is "
                "separate from the generic container runner. The runtime fails closed "
                "if the launcher or sandbox image is unavailable."
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


def _normalize_digest(digest: str | None) -> str | None:
    value = str(digest or "").strip()
    return value or None


def _normalize_csv_values(raw: str | None) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _normalize_signing_status(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {
        "signature-verified",
        "signature-present",
        "unsigned",
        "unverified",
        "verification-failed",
        "unsupported",
    }:
        return value
    return "unverified"


def evaluate_runtime_identity_trust(
    *,
    runtime_identity: dict[str, Any],
    runtime_source: str | None,
    runtime_trust_issuer: str | None,
    runtime_signing_status: str | None,
    runtime_base_compatibility: str | None,
    required_base_compatibility: str | None,
    trusted_sources: list[str] | None = None,
    trusted_issuers: list[str] | None = None,
    require_signature_verification: bool = False,
    require_trusted_source_for_hostile: bool = False,
) -> dict[str, Any]:
    source = str(runtime_source or "").strip() or None
    issuer = str(runtime_trust_issuer or "").strip() or None
    signing_status = _normalize_signing_status(runtime_signing_status)
    base_compatibility = str(runtime_base_compatibility or "").strip() or None
    required_compatibility = str(required_base_compatibility or "").strip() or None
    trusted_source_values = list(trusted_sources or [])
    trusted_issuer_values = list(trusted_issuers or [])
    pinned = bool(runtime_identity.get("pinned"))
    source_trusted = bool(source and source in trusted_source_values) if trusted_source_values else True
    issuer_trusted = bool(issuer and issuer in trusted_issuer_values) if trusted_issuer_values else True
    compatibility_satisfied = (
        True if not required_compatibility else base_compatibility == required_compatibility
    )
    signature_verified = signing_status == "signature-verified"
    signature_requirement_satisfied = (
        signature_verified if require_signature_verification else True
    )
    issues: list[str] = []
    if not pinned:
        issues.append("runtime identity is not digest-pinned")
    if required_compatibility and not compatibility_satisfied:
        issues.append(
            f"runtime base compatibility {base_compatibility!r} does not satisfy required contract {required_compatibility!r}"
        )
    if trusted_source_values and not source_trusted:
        issues.append(f"runtime source {source!r} is not in the trusted source policy")
    if trusted_issuer_values and not issuer_trusted:
        issues.append(f"runtime trust issuer {issuer!r} is not in the trusted issuer policy")
    if require_signature_verification and not signature_verified:
        issues.append(
            f"runtime signing status {signing_status!r} does not satisfy signature verification policy"
        )
    accepted_for_production_safe_profiles = (
        pinned and compatibility_satisfied and source_trusted and issuer_trusted and signature_requirement_satisfied
    )
    accepted_for_hostile_profiles = (
        pinned
        and compatibility_satisfied
        and signature_verified
        and issuer_trusted
        and (source_trusted if require_trusted_source_for_hostile or trusted_source_values else True)
    )
    if accepted_for_hostile_profiles:
        verification_status = "trusted-signed-pinned-compatible"
    elif accepted_for_production_safe_profiles:
        verification_status = "trusted-pinned-compatible"
    elif pinned and compatibility_satisfied:
        verification_status = "pinned-compatible-untrusted"
    elif pinned:
        verification_status = "pinned-incompatible-or-untrusted"
    else:
        verification_status = str(runtime_identity.get("verification") or "unverified")
    return {
        "source": source,
        "trusted_source_policy": trusted_source_values,
        "source_trusted": source_trusted,
        "trust_issuer": issuer,
        "trusted_issuer_policy": trusted_issuer_values,
        "issuer_trusted": issuer_trusted,
        "signing_status": signing_status,
        "signature_verified": signature_verified,
        "require_signature_verification": bool(require_signature_verification),
        "runtime_base_compatibility": base_compatibility,
        "required_base_compatibility": required_compatibility,
        "compatibility_satisfied": compatibility_satisfied,
        "accepted_for_production_safe_profiles": accepted_for_production_safe_profiles,
        "accepted_for_hostile_profiles": accepted_for_hostile_profiles,
        "verification_status": verification_status,
        "issues": issues,
        "operator_note": (
            "Runtime trust combines digest pinning with optional source policy, issuer policy, "
            "signature-verification status, and sandbox base compatibility metadata."
        ),
    }


def classify_oci_runtime_identity(
    *,
    reference: str | None,
    configured_digest: str | None = None,
) -> dict[str, Any]:
    configured_reference = str(reference or "").strip()
    digest_override = _normalize_digest(configured_digest)
    issues: list[str] = []
    reference_digest: str | None = None
    base_reference = configured_reference

    if configured_reference and "@" in configured_reference:
        base_reference, possible_digest = configured_reference.rsplit("@", 1)
        if OCI_DIGEST_PATTERN.match(possible_digest):
            reference_digest = possible_digest
        else:
            issues.append("reference digest is not a valid sha256 OCI digest")

    if digest_override and not OCI_DIGEST_PATTERN.match(digest_override):
        issues.append("configured digest is not a valid sha256 OCI digest")

    if digest_override and reference_digest and digest_override != reference_digest:
        issues.append("configured digest does not match digest embedded in reference")

    effective_digest = digest_override or reference_digest
    if effective_digest and configured_reference:
        launch_reference = f"{base_reference}@{effective_digest}"
        verification = "configured-digest" if digest_override else "digest-in-reference"
        pinned = not issues
    elif configured_reference:
        launch_reference = configured_reference
        verification = "mutable-reference"
        pinned = False
    else:
        launch_reference = ""
        verification = "missing-reference"
        pinned = False

    return {
        "identity_kind": "oci-image",
        "configured_reference": configured_reference or None,
        "configured_digest": digest_override,
        "embedded_digest": reference_digest,
        "launch_reference": launch_reference or None,
        "digest": effective_digest,
        "pinned": bool(pinned),
        "verification": verification,
        "mutable_reference": bool(configured_reference and not effective_digest),
        "issues": issues,
        "operator_note": (
            "Production-safe third-party plugin execution requires a pinned runtime identity. "
            "Mutable tags or unqualified image references are not treated as equivalent to a digest-pinned identity."
        ),
    }


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
    strong_launcher: str,
    strong_runtime_available: bool,
) -> dict[str, Any]:
    linux = platform_name == PLATFORM_LINUX
    windows = platform_name == PLATFORM_WINDOWS
    macos = platform_name == PLATFORM_MACOS
    available_runner_types = [RUNNER_INSECURE_DEV_SUBPROCESS]
    if runtime_available:
        available_runner_types.append(RUNNER_CONTAINERIZED_OCI)
    if linux and strong_runtime_available:
        available_runner_types.append(RUNNER_STRONG_SANDBOX_VM)

    available_hardening_controls = {
        "insecure_dev_subprocess": [],
        "containerized_oci": [
            "disable_network",
            "read_only_rootfs",
        ],
        "strong_sandbox_vm": [
            "dedicated-vm-boundary",
            "sandbox-runtime-hard-limits",
            "read_only_plugin_mount",
            "minimal_environment",
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
    if not linux:
        available_hardening_controls["strong_sandbox_vm"] = []

    process_grade_supported = True
    container_grade_supported = bool(runtime_available)
    strong_sandbox_supported = bool(linux and strong_runtime_available)
    if strong_sandbox_supported:
        highest_assurance_class = ASSURANCE_CLASS_STRONG
    elif container_grade_supported:
        highest_assurance_class = ASSURANCE_CLASS_CONTAINER
    else:
        highest_assurance_class = ASSURANCE_CLASS_INSECURE_DEV

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
    if not strong_runtime_available:
        degraded_modes.append(
            f"strong_sandbox_vm unavailable because sandbox launcher {strong_launcher!r} is not on PATH"
        )
        unsupported_guarantees.append("strong sandbox VM third-party plugin execution")
    if windows or macos or platform_name == PLATFORM_OTHER:
        degraded_modes.append(
            "containerized_oci cannot report Linux-only kernel controls such as no_new_privileges, "
            "seccomp, AppArmor, or SELinux on this host platform"
        )
        unsupported_guarantees.append(
            "Linux-grade hardened third-party plugin sandbox guarantees on non-Linux hosts"
        )
        degraded_modes.append(
            "strong_sandbox_vm is currently characterized only for Linux hosts with a compatible VM sandbox launcher"
        )
        unsupported_guarantees.append(
            "strong sandbox VM guarantees on non-Linux hosts"
        )
    if macos:
        degraded_modes.append(
            "containerized_oci relies on host container virtualization and does not imply native macOS kernel policy enforcement"
        )
    if platform_name == PLATFORM_OTHER:
        degraded_modes.append(
            "host platform is not part of the explicitly characterized sandbox support set"
        )

    equivalence_status = "outside-characterized-support-set"
    if strong_sandbox_supported:
        equivalence_status = "full-strong-sandbox-support"
    elif container_grade_supported:
        equivalence_status = "non-equivalent-container-grade-only"
    elif process_grade_supported:
        equivalence_status = "contained-process-only"

    return {
        "platform": platform_name,
        "label": _platform_label(platform_name),
        "equivalence_status": equivalence_status,
        "support_levels": {
            "contained_process": {
                "support": "supported" if process_grade_supported else "unsupported",
                "assurance_class": ASSURANCE_CLASS_INSECURE_DEV,
            },
            "container_sandbox": {
                "support": "supported" if container_grade_supported else "unsupported",
                "assurance_class": ASSURANCE_CLASS_CONTAINER,
            },
            "strong_sandbox": {
                "support": "supported" if strong_sandbox_supported else "unsupported",
                "assurance_class": ASSURANCE_CLASS_STRONG,
            },
        },
        "highest_supported_assurance_class": highest_assurance_class,
        "high_assurance_hostile_workload_support": bool(strong_sandbox_supported),
        "available_runner_types": available_runner_types,
        "available_hardening_controls": available_hardening_controls,
        "production_safe_third_party_plugin_execution": bool(linux and runtime_available),
        "unsupported_guarantees": unsupported_guarantees,
        "degraded_modes": degraded_modes,
        "operator_note": (
            "Production-safe third-party plugin sandbox guarantees are currently characterized only "
            "for Linux hosts with a compatible container runtime available. Strong sandbox VM "
            "guarantees are characterized only for Linux hosts with a compatible strong sandbox launcher."
        ),
    }


def sandbox_platform_capability_matrix(
    *,
    current_platform: str | None = None,
    container_runtime: str | None = None,
) -> dict[str, Any]:
    runtime_name = str(container_runtime or settings.AINDY_PLUGIN_CONTAINER_RUNTIME or "docker").strip() or "docker"
    runtime_available = shutil.which(runtime_name) is not None
    strong_launcher = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER or "aindy-sandbox-vm").strip() or "aindy-sandbox-vm"
    strong_runtime_available = shutil.which(strong_launcher) is not None
    current_name = _normalize_platform_name(current_platform)
    supported_platforms = {
        PLATFORM_LINUX: _platform_matrix_entry(
            platform_name=PLATFORM_LINUX,
            container_runtime=runtime_name,
            runtime_available=True,
            strong_launcher=strong_launcher,
            strong_runtime_available=True,
        ),
        PLATFORM_WINDOWS: _platform_matrix_entry(
            platform_name=PLATFORM_WINDOWS,
            container_runtime=runtime_name,
            runtime_available=True,
            strong_launcher=strong_launcher,
            strong_runtime_available=False,
        ),
        PLATFORM_MACOS: _platform_matrix_entry(
            platform_name=PLATFORM_MACOS,
            container_runtime=runtime_name,
            runtime_available=True,
            strong_launcher=strong_launcher,
            strong_runtime_available=False,
        ),
        PLATFORM_OTHER: _platform_matrix_entry(
            platform_name=PLATFORM_OTHER,
            container_runtime=runtime_name,
            runtime_available=False,
            strong_launcher=strong_launcher,
            strong_runtime_available=False,
        ),
    }
    support_contract = {
        "claim_scope": "platform-specific-assurance-contract",
        "contained_process_supported_host_platforms": list(
            CONTAINED_PROCESS_HOST_PLATFORMS
        ),
        "container_grade_supported_host_platforms": list(
            CONTAINER_GRADE_HOST_PLATFORMS
        ),
        "production_safe_container_supported_host_platforms": list(
            PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS
        ),
        "strong_sandbox_supported_host_platforms": list(
            STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS
        ),
        "hostile_third_party_supported_host_platforms": list(
            HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS
        ),
        "non_equivalent_host_platforms": {
            PLATFORM_WINDOWS: (
                "Container-grade isolation may be available, but it is not treated as "
                "equivalent to Linux strong-sandbox support."
            ),
            PLATFORM_MACOS: (
                "Container-grade isolation may be available through virtualization, but it "
                "is not treated as equivalent to Linux strong-sandbox support."
            ),
            PLATFORM_OTHER: (
                "The host is outside the explicitly characterized support set for third-party "
                "sandbox assurances."
            ),
        },
        "operator_note": (
            "The runtime does not claim strong-sandbox or hostile-workload parity across all "
            "host platforms. Linux is the only declared fully supported host platform for "
            "strong_sandbox_vm and hostile third-party plugin execution."
        ),
    }
    return {
        "schema_version": SANDBOX_PLATFORM_MATRIX_VERSION,
        "current_platform": current_name,
        "current_environment": _platform_matrix_entry(
            platform_name=current_name,
            container_runtime=runtime_name,
            runtime_available=runtime_available,
            strong_launcher=strong_launcher,
            strong_runtime_available=strong_runtime_available,
        ),
        "support_contract": support_contract,
        "supported_platforms": supported_platforms,
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
    def probe(self, *, timeout_seconds: float) -> dict[str, Any]:
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
    if resolved == RUNNER_STRONG_SANDBOX_VM:
        return StrongSandboxVmRunner()
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
        self._launch_attestation: dict[str, Any] = self._default_launch_attestation()

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
                "started_at": time.time(),
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

    def probe(self, *, timeout_seconds: float) -> dict[str, Any]:
        return self._send_command(
            {"command": "probe"},
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

    def _default_launch_attestation(self) -> dict[str, Any]:
        return {
            "verification_scope": "none",
            "status": "not-started",
            "backend_identity": {
                "requested": None,
                "active": None,
                "verified": False,
            },
            "runtime_identity": {
                "requested": None,
                "active": None,
                "verified": False,
            },
            "mount_mode": {
                "requested": None,
                "active": None,
                "verified": False,
            },
            "writable_temp": {
                "requested": None,
                "active": None,
                "verified": False,
            },
            "host_path_access": {
                "requested": None,
                "active": None,
                "verified": False,
            },
            "network_mode": {
                "requested": None,
                "active": None,
                "verified": False,
            },
            "resource_limit_mode": {
                "requested": None,
                "active": None,
                "verified": False,
                "verified_limits": [],
            },
            "hardening_profiles": {
                "requested_controls": [],
                "active_controls": [],
                "verified_controls": [],
            },
            "operator_note": "No launch attestation is available until the runner starts.",
        }

    def _build_launch_attestation(
        self,
        *,
        args: list[str],
        plugin_root: str | Path,
    ) -> dict[str, Any]:
        _ = args
        _ = plugin_root
        return self._default_launch_attestation()

    def _spawn_process(self, plugin_root: str | Path) -> None:
        args = self._process_args(plugin_root)
        self._launch_attestation = self._build_launch_attestation(
            args=list(args),
            plugin_root=plugin_root,
        )
        process = subprocess.Popen(
            args,
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
        runtime_identity = classify_oci_runtime_identity(reference=None)
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=None,
            runtime_trust_issuer=None,
            runtime_signing_status=None,
            runtime_base_compatibility=None,
            required_base_compatibility=None,
        )
        return {
            "runner_type": self.runner_type,
            "assurance_class": ASSURANCE_CLASS_INSECURE_DEV,
            "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
            "execution_boundary": "subprocess-json-rpc",
            "isolation_claim": "none",
            "runtime_identity": runtime_identity,
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
            "launch_attestation": {
                "verification_scope": "none",
                "status": "not-applicable",
                "backend_identity": {
                    "requested": sys.executable,
                    "active": sys.executable,
                    "verified": True,
                },
                "runtime_identity": {
                    "requested": None,
                    "active": None,
                    "verified": False,
                },
                "mount_mode": {
                    "requested": "host-process",
                    "active": "host-process",
                    "verified": True,
                },
                "writable_temp": {
                    "requested": "host-process",
                    "active": "host-process",
                    "verified": True,
                },
                "host_path_access": {
                    "requested": "host-process",
                    "active": "host-process",
                    "verified": True,
                },
                "network_mode": {
                    "requested": "host-process",
                    "active": "host-process",
                    "verified": True,
                },
                "resource_limit_mode": {
                    "requested": "none",
                    "active": "none",
                    "verified": True,
                    "verified_limits": [],
                },
                "hardening_profiles": {
                    "requested_controls": [],
                    "active_controls": [],
                    "verified_controls": [],
                },
                "operator_note": (
                    "The insecure development subprocess runner has no sandbox launch "
                    "attestation beyond the local Python executable path."
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
        self.image_digest = str(settings.AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST or "").strip()
        self.runtime_source = str(settings.AINDY_PLUGIN_CONTAINER_RUNTIME_SOURCE or "").strip()
        self.runtime_trust_issuer = str(settings.AINDY_PLUGIN_CONTAINER_RUNTIME_TRUST_ISSUER or "").strip()
        self.runtime_signing_status = str(settings.AINDY_PLUGIN_CONTAINER_RUNTIME_SIGNING_STATUS or "unverified").strip()
        self.runtime_base_compatibility = str(settings.AINDY_PLUGIN_CONTAINER_RUNTIME_BASE_COMPATIBILITY or "").strip()
        self.required_base_compatibility = str(settings.AINDY_PLUGIN_CONTAINER_REQUIRED_BASE_COMPATIBILITY or "").strip()
        self.trusted_sources = _normalize_csv_values(settings.AINDY_PLUGIN_CONTAINER_TRUSTED_SOURCES)
        self.trusted_issuers = _normalize_csv_values(settings.AINDY_PLUGIN_CONTAINER_TRUSTED_ISSUERS)
        self.require_signature_verification = bool(
            settings.AINDY_PLUGIN_CONTAINER_REQUIRE_SIGNATURE_VERIFICATION
        )
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
        runtime_identity = classify_oci_runtime_identity(
            reference=self.image,
            configured_digest=self.image_digest,
        )
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=self.runtime_source,
            runtime_trust_issuer=self.runtime_trust_issuer,
            runtime_signing_status=self.runtime_signing_status,
            runtime_base_compatibility=self.runtime_base_compatibility,
            required_base_compatibility=self.required_base_compatibility,
            trusted_sources=self.trusted_sources,
            trusted_issuers=self.trusted_issuers,
            require_signature_verification=self.require_signature_verification,
        )
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
            "assurance_class": ASSURANCE_CLASS_CONTAINER,
            "assurance_properties": {
                "boundary_type": "shared-kernel-container-sandbox",
                "process_separation_model": "container-namespace-boundary",
                "mount_mediation_model": "container-runtime-bind-mount-read-only",
                "network_mediation_model": "container-runtime-network-policy",
                "runtime_identity_model": "pinned-oci-image",
                "session_verification_model": "launch-attestation-only",
            },
            "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
            "execution_boundary": "container-stdio-json-rpc",
            "isolation_claim": "container-boundary",
            "container_runtime": self.container_runtime,
            "image": self.image or None,
            "runtime_identity": runtime_identity,
            "plugin_mount_path": self.plugin_mount_path,
            "plugin_mount_mode": "read-only",
            "writable_tmp": self.writable_tmp,
            "tmpfs_size": self.tmpfs_size if self.writable_tmp else None,
            "workdir": self.workdir,
            "kernel_controls": kernel_controls,
            "resource_limits": resource_limits,
            "launch_attestation": dict(self._launch_attestation),
            "selection_mode": "explicit-or-auto",
            "operator_note": (
                "The containerized OCI runner requires an operator-provided image with "
                "aindy-runtime installed. The runtime does not silently fall back to the "
                "development subprocess runner when this mode is selected."
            ),
        }

    def _build_launch_attestation(
        self,
        *,
        args: list[str],
        plugin_root: str | Path,
    ) -> dict[str, Any]:
        runtime_identity = classify_oci_runtime_identity(
            reference=self.image,
            configured_digest=self.image_digest,
        )
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=self.runtime_source,
            runtime_trust_issuer=self.runtime_trust_issuer,
            runtime_signing_status=self.runtime_signing_status,
            runtime_base_compatibility=self.runtime_base_compatibility,
            required_base_compatibility=self.required_base_compatibility,
            trusted_sources=self.trusted_sources,
            trusted_issuers=self.trusted_issuers,
            require_signature_verification=self.require_signature_verification,
        )
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
        requested_controls = list(kernel_controls.get("requested_controls") or [])
        active_controls = list(kernel_controls.get("active_controls") or [])
        verified_controls: list[str] = []
        args_list = list(args)
        security_opts = [
            str(args_list[index + 1])
            for index, value in enumerate(args_list[:-1])
            if value == "--security-opt"
        ]
        mount_args = [
            str(args_list[index + 1])
            for index, value in enumerate(args_list[:-1])
            if value == "--mount"
        ]
        bind_mounts = [value for value in mount_args if "type=bind" in value]
        tmpfs_mounts = [value for value in mount_args if "type=tmpfs" in value]
        if "--network" in args_list:
            idx = args_list.index("--network")
            if idx + 1 < len(args_list) and args_list[idx + 1] == "none":
                verified_controls.append("disable_network")
        if "--read-only" in args_list:
            verified_controls.append("read_only_rootfs")
        if "--cap-drop" in args_list:
            idx = args_list.index("--cap-drop")
            if idx + 1 < len(args_list) and args_list[idx + 1] == "ALL":
                verified_controls.append("drop_all_capabilities")
        if "no-new-privileges" in security_opts:
            verified_controls.append("no_new_privileges")
        if "--pids-limit" in args_list:
            verified_controls.append("pids_limit")
        if any(str(opt).startswith("seccomp=") for opt in security_opts):
            verified_controls.append("seccomp_profile")
        if any(str(opt).startswith("apparmor=") for opt in security_opts):
            verified_controls.append("apparmor_profile")
        if any(str(opt).startswith("label=") for opt in security_opts):
            verified_controls.append("selinux_label")
        verified_limits = [
            limit_name
            for flag, limit_name in (
                ("--memory", "memory_limit"),
                ("--cpus", "cpu_limit"),
                ("--cpu-shares", "cpu_shares"),
                ("--pids-limit", "process_limit"),
            )
            if flag in args_list
        ]
        mount_mode_active = None
        mount_mode_verified = False
        if any("readonly" in value for value in bind_mounts):
            mount_mode_active = "read-only"
            mount_mode_verified = True
        writable_temp_active = "tmpfs:/tmp" if tmpfs_mounts else "none"
        writable_temp_verified = any("dst=/tmp" in value for value in tmpfs_mounts)
        host_path_access_active = "plugin-root-bind-only" if len(bind_mounts) == 1 else "additional-bind-mounts-present"
        host_path_access_verified = len(bind_mounts) == 1
        network_mode_active = "none" if "--network" in args_list and "none" in args_list else "default"
        resource_limit_mode_active = (
            "container-runtime-hard-limits" if verified_limits else "wall-clock-timeout-only"
        )
        resolved_backend = shutil.which(self.container_runtime) or self.container_runtime
        return {
            "verification_scope": "launch-argv-and-resolved-executable",
            "status": "launch-observed",
            "backend_identity": {
                "requested": self.container_runtime,
                "active": resolved_backend,
                "verified": bool(resolved_backend),
            },
            "runtime_identity": {
                "requested": runtime_identity.get("launch_reference"),
                "active": runtime_identity.get("launch_reference"),
                "verified": bool(runtime_identity.get("launch_reference")),
                "trust_chain": dict(runtime_identity.get("trust_chain") or {}),
            },
            "mount_mode": {
                "requested": "read-only",
                "active": mount_mode_active,
                "verified": mount_mode_verified,
                "evidence": mount_args,
                "plugin_root": str(Path(plugin_root).resolve()),
            },
            "writable_temp": {
                "requested": "isolated-tmpfs:/tmp" if self.writable_tmp else "none",
                "active": writable_temp_active,
                "verified": writable_temp_verified if self.writable_tmp else True,
                "evidence": tmpfs_mounts,
            },
            "host_path_access": {
                "requested": "no-ambient-host-paths",
                "active": host_path_access_active,
                "verified": host_path_access_verified,
                "evidence": bind_mounts,
            },
            "network_mode": {
                "requested": "none" if self.disable_network else "default",
                "active": network_mode_active,
                "verified": True,
            },
            "resource_limit_mode": {
                "requested": "container-runtime-hard-limits",
                "active": resource_limit_mode_active,
                "verified": True,
                "verified_limits": verified_limits,
            },
            "hardening_profiles": {
                "requested_controls": requested_controls,
                "active_controls": active_controls,
                "verified_controls": sorted(set(verified_controls)),
            },
            "assurance_properties": {
                "requested": {
                    "boundary_type": "shared-kernel-container-sandbox",
                    "process_separation_model": "container-namespace-boundary",
                    "mount_mediation_model": "container-runtime-bind-mount-read-only",
                    "network_mediation_model": "container-runtime-network-policy",
                    "runtime_identity_model": "pinned-oci-image",
                    "session_verification_model": "launch-attestation-only",
                },
                "active": {
                    "boundary_type": "shared-kernel-container-sandbox",
                    "process_separation_model": "container-namespace-boundary",
                    "mount_mediation_model": "container-runtime-bind-mount-read-only",
                    "network_mediation_model": "container-runtime-network-policy",
                    "runtime_identity_model": "pinned-oci-image",
                    "session_verification_model": "launch-attestation-only",
                },
                "verified": {
                    "boundary_type": True,
                    "process_separation_model": True,
                    "mount_mediation_model": mount_mode_verified,
                    "network_mediation_model": True,
                    "runtime_identity_model": bool(runtime_identity.get("launch_reference")),
                    "session_verification_model": True,
                },
            },
            "operator_note": (
                "Verified controls reflect launch arguments and resolved backend identity "
                "observed by the runtime. They do not prove kernel enforcement after launch."
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
        runtime_identity = classify_oci_runtime_identity(
            reference=self.image,
            configured_digest=self.image_digest,
        )
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=self.runtime_source,
            runtime_trust_issuer=self.runtime_trust_issuer,
            runtime_signing_status=self.runtime_signing_status,
            runtime_base_compatibility=self.runtime_base_compatibility,
            required_base_compatibility=self.required_base_compatibility,
            trusted_sources=self.trusted_sources,
            trusted_issuers=self.trusted_issuers,
            require_signature_verification=self.require_signature_verification,
        )
        if not runtime_identity.get("configured_reference"):
            raise RuntimeError(
                "container sandbox runner requires AINDY_PLUGIN_CONTAINER_IMAGE"
            )
        if runtime_identity.get("issues"):
            raise RuntimeError(
                "container sandbox runner runtime identity is invalid: "
                + "; ".join(str(item) for item in runtime_identity["issues"])
            )
        if (runtime_identity.get("trust_chain") or {}).get("issues"):
            raise RuntimeError(
                "container sandbox runner runtime trust is invalid: "
                + "; ".join(
                    str(item)
                    for item in (runtime_identity.get("trust_chain") or {}).get("issues") or []
                )
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
                classify_oci_runtime_identity(
                    reference=self.image,
                    configured_digest=self.image_digest,
                )["launch_reference"],
                "python",
                "-m",
                "AINDY.platform_layer.extension_worker",
                "--host",
            ]
        )
        return args


class StrongSandboxVmRunner(_JsonRpcProcessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.launcher = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER or "aindy-sandbox-vm").strip() or "aindy-sandbox-vm"
        self.image = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE or "").strip()
        self.image_digest = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST or "").strip()
        self.runtime_source = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SOURCE or "").strip()
        self.runtime_trust_issuer = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_TRUST_ISSUER or "").strip()
        self.runtime_signing_status = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SIGNING_STATUS or "unverified").strip()
        self.runtime_base_compatibility = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_BASE_COMPATIBILITY or "").strip()
        self.required_base_compatibility = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_REQUIRED_BASE_COMPATIBILITY or "").strip()
        self.trusted_sources = _normalize_csv_values(settings.AINDY_PLUGIN_STRONG_SANDBOX_TRUSTED_SOURCES)
        self.trusted_issuers = _normalize_csv_values(settings.AINDY_PLUGIN_STRONG_SANDBOX_TRUSTED_ISSUERS)
        self.require_signature_verification = bool(
            settings.AINDY_PLUGIN_STRONG_SANDBOX_REQUIRE_SIGNATURE_VERIFICATION
        )
        self.plugin_mount_path = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_PLUGIN_MOUNT_PATH or "/plugin-root").strip() or "/plugin-root"
        self.workdir = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_WORKDIR or "/work").strip() or "/work"
        self.memory_limit = str(settings.AINDY_PLUGIN_STRONG_SANDBOX_MEMORY_LIMIT or "").strip()
        self.cpu_limit = float(settings.AINDY_PLUGIN_STRONG_SANDBOX_CPU_LIMIT or 0.0)
        self.pids_limit = int(settings.AINDY_PLUGIN_STRONG_SANDBOX_PIDS_LIMIT or 0)

    @property
    def runner_type(self) -> str:
        return RUNNER_STRONG_SANDBOX_VM

    def metadata(self) -> dict[str, Any]:
        runtime_identity = classify_oci_runtime_identity(
            reference=self.image,
            configured_digest=self.image_digest,
        )
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=self.runtime_source,
            runtime_trust_issuer=self.runtime_trust_issuer,
            runtime_signing_status=self.runtime_signing_status,
            runtime_base_compatibility=self.runtime_base_compatibility,
            required_base_compatibility=self.required_base_compatibility,
            trusted_sources=self.trusted_sources,
            trusted_issuers=self.trusted_issuers,
            require_signature_verification=self.require_signature_verification,
            require_trusted_source_for_hostile=True,
        )
        launcher_available = shutil.which(self.launcher) is not None
        platform_name = _normalized_platform_system() or "unknown"
        supported = platform_name == PLATFORM_LINUX and launcher_available
        unsupported_reasons: list[str] = []
        if platform_name != PLATFORM_LINUX:
            unsupported_reasons.append(
                f"strong_sandbox_vm is currently characterized only for Linux hosts, not {platform_name!r}"
            )
        if not launcher_available:
            unsupported_reasons.append(
                f"sandbox launcher {self.launcher!r} is not available on PATH"
            )
        if not runtime_identity.get("configured_reference"):
            unsupported_reasons.append(
                "AINDY_PLUGIN_STRONG_SANDBOX_IMAGE is not configured"
            )
        if runtime_identity.get("issues"):
            unsupported_reasons.extend(str(item) for item in runtime_identity["issues"])
        if (runtime_identity.get("trust_chain") or {}).get("issues"):
            unsupported_reasons.extend(
                str(item) for item in (runtime_identity.get("trust_chain") or {}).get("issues") or []
            )
        return {
            "runner_type": self.runner_type,
            "assurance_class": ASSURANCE_CLASS_STRONG,
            "assurance_properties": dict(STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES),
            "interface_version": SANDBOX_RUNNER_INTERFACE_VERSION,
            "execution_boundary": "vm-stdio-json-rpc",
            "isolation_claim": "vm-boundary",
            "sandbox_launcher": self.launcher,
            "sandbox_image": self.image or None,
            "runtime_identity": runtime_identity,
            "plugin_mount_path": self.plugin_mount_path,
            "plugin_mount_mode": "read-only",
            "workdir": self.workdir,
            "resource_limits": {
                "enforcement": "sandbox-runtime-hard-limits" if supported and runtime_identity.get("launch_reference") and not runtime_identity.get("issues") else "unavailable",
                "runtime_available": launcher_available,
                "effective_limits": {
                    "wall_clock_timeout_seconds": 30.0,
                    "memory_limit": self.memory_limit or None,
                    "cpu_limit": self.cpu_limit or None,
                    "cpu_shares": None,
                    "process_limit": self.pids_limit or None,
                },
                "unsupported_limits": [] if supported and self.image else [
                    {
                        "limit": "sandbox-runtime-hard-limits",
                        "reason": "; ".join(unsupported_reasons) or "strong sandbox runtime unavailable",
                    }
                ],
                "operator_note": (
                    "Strong sandbox VM limits are hard limits only when the dedicated sandbox launcher "
                    "and image are available on a supported host platform."
                ),
            },
            "hardening_controls": {
                "reporting_version": SANDBOX_RUNNER_INTERFACE_VERSION,
                "platform": platform_name,
                "runtime_available": launcher_available,
                "requested_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ],
                "supported_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ] if supported else [],
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ] if supported and runtime_identity.get("launch_reference") and not runtime_identity.get("issues") else [],
                "unsupported_controls": [
                    {
                        "control": "strong_sandbox_vm",
                        "reason": "; ".join(unsupported_reasons) or "strong sandbox runtime unavailable",
                    }
                ] if unsupported_reasons else [],
            },
            "launch_attestation": dict(self._launch_attestation),
            "selection_mode": "explicit-only",
            "operator_note": (
                "The strong sandbox VM runner targets a dedicated higher-assurance sandbox launcher. "
                "The runtime fails closed when it is selected but unavailable and does not map it "
                "onto the generic container runner."
            ),
        }

    def _build_launch_attestation(
        self,
        *,
        args: list[str],
        plugin_root: str | Path,
    ) -> dict[str, Any]:
        runtime_identity = classify_oci_runtime_identity(
            reference=self.image,
            configured_digest=self.image_digest,
        )
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=self.runtime_source,
            runtime_trust_issuer=self.runtime_trust_issuer,
            runtime_signing_status=self.runtime_signing_status,
            runtime_base_compatibility=self.runtime_base_compatibility,
            required_base_compatibility=self.required_base_compatibility,
            trusted_sources=self.trusted_sources,
            trusted_issuers=self.trusted_issuers,
            require_signature_verification=self.require_signature_verification,
            require_trusted_source_for_hostile=True,
        )
        verified_limits = [
            limit_name
            for flag, limit_name in (
                ("--memory", "memory_limit"),
                ("--cpus", "cpu_limit"),
                ("--pids-limit", "process_limit"),
            )
            if flag in args
        ]
        verified_controls: list[str] = []
        if "--mount-readonly" in args:
            verified_controls.append("read_only_plugin_mount")
        if "--network-deny-default" in args:
            verified_controls.append("launcher_network_deny_default")
        if "--deny-host-paths" in args:
            verified_controls.append("launcher_host_path_denial")
        resolved_backend = shutil.which(self.launcher) or self.launcher
        return {
            "verification_scope": "launch-argv-and-resolved-executable",
            "status": "launch-observed",
            "backend_identity": {
                "requested": self.launcher,
                "active": resolved_backend,
                "verified": bool(resolved_backend),
            },
            "runtime_identity": {
                "requested": runtime_identity.get("launch_reference"),
                "active": runtime_identity.get("launch_reference"),
                "verified": bool(runtime_identity.get("launch_reference")),
                "trust_chain": dict(runtime_identity.get("trust_chain") or {}),
            },
            "mount_mode": {
                "requested": "read-only",
                "active": "read-only" if "--mount-readonly" in args else None,
                "verified": "--mount-readonly" in args,
                "evidence": [
                    str(args[index + 1])
                    for index, value in enumerate(args[:-1])
                    if value == "--mount-readonly"
                ],
                "plugin_root": str(Path(plugin_root).resolve()),
            },
            "writable_temp": {
                "requested": "isolated-writable-temp",
                "active": "isolated-writable-temp" if "--tmpfs" in args else "launcher-defined",
                "verified": "--tmpfs" in args,
            },
            "host_path_access": {
                "requested": "launcher-denied-host-path-access",
                "active": "launcher-denied-host-path-access" if "--deny-host-paths" in args else "unknown",
                "verified": "--deny-host-paths" in args,
            },
            "network_mode": {
                "requested": "deny-default-via-sandbox-launcher",
                "active": "deny-default-via-sandbox-launcher" if "--network-deny-default" in args else "launcher-defined",
                "verified": "--network-deny-default" in args,
            },
            "resource_limit_mode": {
                "requested": "sandbox-runtime-hard-limits",
                "active": "sandbox-runtime-hard-limits" if verified_limits else "launcher-default",
                "verified": bool(verified_limits),
                "verified_limits": verified_limits,
            },
            "hardening_profiles": {
                "requested_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ],
                "active_controls": [
                    "dedicated_vm_boundary",
                    "read_only_plugin_mount",
                    "launcher_network_deny_default",
                    "launcher_host_path_denial",
                    "minimal_environment",
                    "sandbox_runtime_limits",
                ] if runtime_identity.get("launch_reference") else [],
                "verified_controls": sorted(set(verified_controls)),
            },
            "assurance_properties": {
                "requested": dict(STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES),
                "active": dict(STRONG_SANDBOX_REQUIRED_ASSURANCE_PROPERTIES),
                "verified": {
                    "boundary_type": bool(resolved_backend),
                    "process_separation_model": bool(resolved_backend),
                    "mount_mediation_model": "--mount-readonly" in args,
                    "network_mediation_model": "--network-deny-default" in args,
                    "runtime_identity_model": bool(runtime_identity.get("launch_reference")),
                    "session_verification_model": True,
                },
            },
            "operator_note": (
                "Verified controls reflect launch arguments and resolved launcher identity "
                "observed by the runtime. VM isolation properties remain launcher-defined."
            ),
        }

    def _worker_plugin_root(self, plugin_root: str | Path) -> str:
        _ = plugin_root
        return self.plugin_mount_path

    def _build_child_env(self) -> dict[str, str]:
        return {
            "PYTHONIOENCODING": "utf-8",
        }

    def _ensure_strong_sandbox_ready(self) -> None:
        runtime_identity = classify_oci_runtime_identity(
            reference=self.image,
            configured_digest=self.image_digest,
        )
        runtime_identity["trust_chain"] = evaluate_runtime_identity_trust(
            runtime_identity=runtime_identity,
            runtime_source=self.runtime_source,
            runtime_trust_issuer=self.runtime_trust_issuer,
            runtime_signing_status=self.runtime_signing_status,
            runtime_base_compatibility=self.runtime_base_compatibility,
            required_base_compatibility=self.required_base_compatibility,
            trusted_sources=self.trusted_sources,
            trusted_issuers=self.trusted_issuers,
            require_signature_verification=self.require_signature_verification,
            require_trusted_source_for_hostile=True,
        )
        if not runtime_identity.get("configured_reference"):
            raise RuntimeError(
                "strong sandbox runner requires AINDY_PLUGIN_STRONG_SANDBOX_IMAGE"
            )
        if runtime_identity.get("issues"):
            raise RuntimeError(
                "strong sandbox runner runtime identity is invalid: "
                + "; ".join(str(item) for item in runtime_identity["issues"])
            )
        if (runtime_identity.get("trust_chain") or {}).get("issues"):
            raise RuntimeError(
                "strong sandbox runner runtime trust is invalid: "
                + "; ".join(
                    str(item)
                    for item in (runtime_identity.get("trust_chain") or {}).get("issues") or []
                )
            )
        if _normalized_platform_system() != PLATFORM_LINUX:
            raise RuntimeError(
                "strong sandbox runner requires a Linux host"
            )
        if shutil.which(self.launcher) is None:
            raise RuntimeError(
                f"strong sandbox runner unavailable: launcher {self.launcher!r} was not found on PATH"
            )

    def _process_args(self, plugin_root: str | Path) -> list[str]:
        self._ensure_strong_sandbox_ready()
        resolved_plugin_root = str(Path(plugin_root).resolve())
        args = [
            self.launcher,
            "run",
            "--image",
            classify_oci_runtime_identity(
                reference=self.image,
                configured_digest=self.image_digest,
            )["launch_reference"],
            "--mount-readonly",
            f"{resolved_plugin_root}:{self.plugin_mount_path}",
            "--deny-host-paths",
            "--network-deny-default",
            "--tmpfs",
            "/tmp",
            "--workdir",
            self.workdir,
        ]
        if self.memory_limit:
            args.extend(["--memory", self.memory_limit])
        if self.cpu_limit > 0:
            args.extend(["--cpus", f"{self.cpu_limit:g}"])
        if self.pids_limit > 0:
            args.extend(["--pids-limit", str(self.pids_limit)])
        args.extend(
            [
                "--",
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
