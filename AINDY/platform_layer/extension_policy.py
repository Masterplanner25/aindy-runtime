"""Shared extension trust policy helpers.

This module does not provide sandboxing. Any Python module imported into the
runtime still executes with full interpreter privileges.
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

_DEFAULT_TRUSTED_BOOTSTRAP_PREFIXES = ("AINDY.", "apps.")
_DEFAULT_EXTERNAL_BOOTSTRAP_PREFIXES: tuple[str, ...] = ()
_PRIVATE_HOST_ALIASES = {"localhost", "127.0.0.1", "::1"}
_EXTERNAL_PYTHON_OVERRIDE_ENV_VAR = "AINDY_TRUST_EXTERNAL_PYTHON_EXTENSIONS"
_EXTERNAL_PYTHON_PROD_ACK_ENV_VAR = "AINDY_ACK_UNSANDBOXED_EXTERNAL_PYTHON"
OWNER_RUNTIME_BUILTIN = "runtime-built-in"
OWNER_FIRST_PARTY_APP = "first-party-app"
OWNER_EXTERNAL_THIRD_PARTY = "external-third-party"
ALLOWED_EXTENSION_OWNER_CLASSES = {
    OWNER_RUNTIME_BUILTIN,
    OWNER_FIRST_PARTY_APP,
    OWNER_EXTERNAL_THIRD_PARTY,
}


def _env_truthy(env_name: str, default: str = "false") -> bool:
    return os.getenv(env_name, default).lower() in {
        "1",
        "true",
        "yes",
    }


def trusted_bootstrap_prefixes() -> tuple[str, ...]:
    configured = os.getenv("AINDY_TRUSTED_BOOTSTRAP_PREFIXES", "").strip()
    if not configured:
        return _DEFAULT_TRUSTED_BOOTSTRAP_PREFIXES
    prefixes = tuple(
        value.strip()
        for value in configured.split(",")
        if value.strip()
    )
    return prefixes or _DEFAULT_TRUSTED_BOOTSTRAP_PREFIXES


def external_bootstrap_prefixes() -> tuple[str, ...]:
    configured = os.getenv("AINDY_EXTERNAL_BOOTSTRAP_PREFIXES", "").strip()
    if not configured:
        return _DEFAULT_EXTERNAL_BOOTSTRAP_PREFIXES
    prefixes = tuple(
        value.strip()
        for value in configured.split(",")
        if value.strip()
    )
    return prefixes or _DEFAULT_EXTERNAL_BOOTSTRAP_PREFIXES


def infer_bootstrap_owner_class(module_name: str) -> str:
    cleaned = str(module_name or "").strip()
    if cleaned.startswith("AINDY."):
        return OWNER_RUNTIME_BUILTIN
    if cleaned.startswith("apps."):
        return OWNER_FIRST_PARTY_APP
    return OWNER_EXTERNAL_THIRD_PARTY


def validate_extension_owner_class(owner_class: str) -> str:
    cleaned = str(owner_class or "").strip()
    if cleaned not in ALLOWED_EXTENSION_OWNER_CLASSES:
        raise ValueError(
            f"owner_class must be one of {sorted(ALLOWED_EXTENSION_OWNER_CLASSES)!r}, got {owner_class!r}"
        )
    return cleaned


def external_python_extensions_trusted() -> bool:
    return _env_truthy(_EXTERNAL_PYTHON_OVERRIDE_ENV_VAR)


def external_python_override_production_acknowledged() -> bool:
    return _env_truthy(_EXTERNAL_PYTHON_PROD_ACK_ENV_VAR)


def external_python_override_state() -> dict[str, object]:
    enabled = external_python_extensions_trusted()
    return {
        "enabled": enabled,
        "env_var": _EXTERNAL_PYTHON_OVERRIDE_ENV_VAR,
        "production_ack_env_var": _EXTERNAL_PYTHON_PROD_ACK_ENV_VAR,
        "production_acknowledged": (
            external_python_override_production_acknowledged() if enabled else False
        ),
        "execution_model": (
            "isolated-plugin-host-required"
            if enabled
            else "external-python-blocked"
        ),
        "sandboxing": (
            "subprocess-boundary"
            if enabled
            else "not-applicable"
        ),
        "operator_warning": (
            "External third-party Python does not execute in-process. Dynamic plugin "
            "nodes must use the isolated plugin-host boundary, and manifest "
            "bootstrap modules remain unsupported for third-party code."
        ),
        "legacy_override_has_effect": False,
    }


def python_extension_trust_class(owner_class: str) -> str:
    resolved = validate_extension_owner_class(owner_class)
    if resolved == OWNER_RUNTIME_BUILTIN:
        return "trusted-runtime-python"
    if resolved == OWNER_FIRST_PARTY_APP:
        return "trusted-first-party-python"
    return "isolated-third-party-python"


def python_extension_execution_metadata(
    owner_class: str,
    *,
    surface: str | None = None,
) -> dict[str, object]:
    resolved = validate_extension_owner_class(owner_class)
    trust_class = python_extension_trust_class(resolved)
    metadata: dict[str, object] = {
        "owner_class": resolved,
        "trust_class": trust_class,
        "sandboxing": "none",
        "execution_model": "trusted-in-process-python",
        "trusted_override_active": False,
        "trusted_override_env_var": None,
    }
    if resolved == OWNER_EXTERNAL_THIRD_PARTY:
        if surface == "dynamic plugin node":
            metadata["execution_model"] = "isolated-plugin-host"
            metadata["sandboxing"] = "subprocess-boundary"
        else:
            metadata["execution_model"] = "blocked-external-python"
            metadata["sandboxing"] = "not-applicable"
    return metadata


def assert_python_extension_allowed(
    owner_class: str,
    *,
    surface: str,
    identifier: str,
) -> str:
    resolved = validate_extension_owner_class(owner_class)
    if resolved != OWNER_EXTERNAL_THIRD_PARTY:
        return python_extension_trust_class(resolved)
    if surface == "dynamic plugin node":
        return "isolated-third-party-python"
    raise ValueError(
        f"external-third-party {surface} {identifier!r} is not supported in-process because "
        "the runtime does not sandbox bootstrap imports or direct Python execution. "
        "Use a contract-driven webhook integration or an isolated dynamic plugin node instead."
    )


def validate_bootstrap_module_name(
    module_name: str,
    *,
    owner_class: str | None = None,
    manifest_owner: str | None = None,
) -> str:
    if not isinstance(module_name, str) or not module_name.strip():
        raise ValueError("plugin module name must be a non-empty string")
    cleaned = module_name.strip()
    if any(token in cleaned for token in ("/", "\\", ":", "..")) or cleaned.startswith("."):
        raise ValueError(f"plugin module name contains illegal path syntax: {module_name!r}")
    parts = cleaned.split(".")
    if not all(part.isidentifier() for part in parts):
        raise ValueError(f"plugin module name must contain only Python identifiers: {module_name!r}")
    resolved_owner_class = validate_extension_owner_class(owner_class or infer_bootstrap_owner_class(cleaned))
    if resolved_owner_class == OWNER_RUNTIME_BUILTIN and not cleaned.startswith("AINDY."):
        raise ValueError(
            f"runtime-built-in bootstrap modules must live under 'AINDY.', got {module_name!r}"
        )
    if resolved_owner_class == OWNER_FIRST_PARTY_APP and not cleaned.startswith("apps."):
        raise ValueError(
            f"first-party-app bootstrap modules must live under 'apps.', got {module_name!r}"
        )
    if resolved_owner_class == OWNER_EXTERNAL_THIRD_PARTY:
        prefixes = external_bootstrap_prefixes()
        if not prefixes or not any(cleaned.startswith(prefix) for prefix in prefixes):
            raise ValueError(
                f"external-third-party bootstrap module {module_name!r} is outside allowed prefixes "
                f"{prefixes!r}"
            )
    if resolved_owner_class != OWNER_EXTERNAL_THIRD_PARTY and not any(
        cleaned.startswith(prefix) for prefix in trusted_bootstrap_prefixes()
    ):
        raise ValueError(
            f"plugin module {module_name!r} is outside trusted bootstrap prefixes "
            f"{trusted_bootstrap_prefixes()!r}"
        )
    if manifest_owner == "runtime" and resolved_owner_class != OWNER_RUNTIME_BUILTIN:
        raise ValueError(
            f"runtime-owned manifests may only declare runtime-built-in extensions, got {resolved_owner_class!r} for {module_name!r}"
        )
    return resolved_owner_class


def _private_extension_targets_allowed() -> bool:
    return os.getenv("AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def validate_outbound_extension_url(url: str, *, field_name: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http:// or https://")
    if not parsed.hostname:
        raise ValueError(f"{field_name} must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not embed credentials")

    hostname = parsed.hostname.strip().lower()
    if hostname in _PRIVATE_HOST_ALIASES and not _private_extension_targets_allowed():
        raise ValueError(
            f"{field_name} targets a private/loopback host {hostname!r}. "
            "Set AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS=true only for explicitly trusted environments."
        )

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ) and not _private_extension_targets_allowed():
        raise ValueError(
            f"{field_name} targets private or non-routable address {hostname!r}. "
            "Set AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS=true only for explicitly trusted environments."
        )
