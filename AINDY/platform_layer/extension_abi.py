from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from AINDY.platform_layer.extension_provenance import (
    ExtensionProvenanceDeclaration,
)


EXTENSION_ABI_POLICY_VERSION = "2026-05-20"

SURFACE_MANIFEST = "manifest"
SURFACE_DYNAMIC_NODE = "dynamic-node-registration"
SURFACE_WEBHOOK = "webhook-registration"
SURFACE_FLOW = "flow-registration"
SURFACE_AGENT_TOOL = "agent-tool-registration"
SURFACE_PLANNER_BACKEND = "planner-backend-registration"

STABILITY_STABLE = "stable"
STABILITY_EXPERIMENTAL = "experimental"

MANIFEST_ABI_V1 = "aindy.extension.manifest/v1"
NODE_REGISTRATION_ABI_V1ALPHA1 = "aindy.extension.node-registration/v1alpha1"
WEBHOOK_REGISTRATION_ABI_V1ALPHA1 = "aindy.extension.webhook-registration/v1alpha1"
FLOW_REGISTRATION_ABI_V1ALPHA1 = "aindy.extension.flow-registration/v1alpha1"
AGENT_TOOL_REGISTRATION_ABI_V1ALPHA1 = "aindy.extension.agent-tool-registration/v1alpha1"
PLANNER_BACKEND_REGISTRATION_ABI_V1ALPHA1 = (
    "aindy.extension.planner-backend-registration/v1alpha1"
)

LEGACY_UNVERSIONED_MANIFEST = "legacy-unversioned"
MANIFEST_KIND = "aindy-extension-manifest"

_SURFACE_POLICY: dict[str, dict[str, Any]] = {
    SURFACE_MANIFEST: {
        "stability": STABILITY_STABLE,
        "supported_versions": [MANIFEST_ABI_V1],
        "default_version": MANIFEST_ABI_V1,
        "legacy_accepted": True,
        "notes": (
            "Versioned manifest v1 is the stable manifest ABI. Legacy unversioned "
            "manifests remain accepted for backward compatibility."
        ),
    },
    SURFACE_DYNAMIC_NODE: {
        "stability": STABILITY_EXPERIMENTAL,
        "supported_versions": [NODE_REGISTRATION_ABI_V1ALPHA1],
        "default_version": NODE_REGISTRATION_ABI_V1ALPHA1,
        "legacy_accepted": False,
        "notes": (
            "Dynamic node registration remains experimental even though the payload "
            "shape is now explicitly versioned."
        ),
    },
    SURFACE_WEBHOOK: {
        "stability": STABILITY_EXPERIMENTAL,
        "supported_versions": [WEBHOOK_REGISTRATION_ABI_V1ALPHA1],
        "default_version": WEBHOOK_REGISTRATION_ABI_V1ALPHA1,
        "legacy_accepted": False,
        "notes": (
            "Webhook registration is versioned but still experimental while the "
            "surface evolves."
        ),
    },
    SURFACE_FLOW: {
        "stability": STABILITY_EXPERIMENTAL,
        "supported_versions": [FLOW_REGISTRATION_ABI_V1ALPHA1],
        "default_version": FLOW_REGISTRATION_ABI_V1ALPHA1,
        "legacy_accepted": False,
        "notes": (
            "Dynamic flow registration is versioned but still experimental."
        ),
    },
    SURFACE_AGENT_TOOL: {
        "stability": STABILITY_EXPERIMENTAL,
        "supported_versions": [AGENT_TOOL_REGISTRATION_ABI_V1ALPHA1],
        "default_version": AGENT_TOOL_REGISTRATION_ABI_V1ALPHA1,
        "legacy_accepted": False,
        "notes": (
            "Agent tool registration is a code-level integration surface and is not "
            "yet declared stable."
        ),
    },
    SURFACE_PLANNER_BACKEND: {
        "stability": STABILITY_EXPERIMENTAL,
        "supported_versions": [PLANNER_BACKEND_REGISTRATION_ABI_V1ALPHA1],
        "default_version": PLANNER_BACKEND_REGISTRATION_ABI_V1ALPHA1,
        "legacy_accepted": False,
        "notes": (
            "Planner backend registration is a code-level integration surface and is "
            "not yet declared stable."
        ),
    },
}


class ManifestPluginEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str = Field(..., min_length=1)
    owner_class: str | None = None
    provenance: ExtensionProvenanceDeclaration | None = None


class ManifestDeclarativeNodeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field("dynamic-node")
    abi_version: str = Field(NODE_REGISTRATION_ABI_V1ALPHA1)
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    handler: str = Field(..., min_length=1)
    timeout_seconds: int = Field(10, ge=1, le=30)
    secret: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    owner_class: str = Field(..., min_length=1)
    provenance: ExtensionProvenanceDeclaration | None = None
    overwrite: bool = False

    @model_validator(mode="after")
    def _validate_entry(self) -> "ManifestDeclarativeNodeEntry":
        if self.kind != "dynamic-node":
            raise ValueError("manifest declarative node entry must declare kind='dynamic-node'")
        self.abi_version = validate_extension_abi_version(
            SURFACE_DYNAMIC_NODE,
            self.abi_version,
        )
        return self


class ManifestDeclarativeWebhookEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field("webhook-subscription")
    abi_version: str = Field(WEBHOOK_REGISTRATION_ABI_V1ALPHA1)
    event_type: str = Field(..., min_length=1)
    callback_url: str = Field(..., min_length=1)
    secret: str | None = None
    owner_class: str = Field(..., min_length=1)
    provenance: ExtensionProvenanceDeclaration | None = None

    @model_validator(mode="after")
    def _validate_entry(self) -> "ManifestDeclarativeWebhookEntry":
        if self.kind != "webhook-subscription":
            raise ValueError(
                "manifest declarative webhook entry must declare kind='webhook-subscription'"
            )
        self.abi_version = validate_extension_abi_version(
            SURFACE_WEBHOOK,
            self.abi_version,
        )
        return self


class ManifestDeclarativeFlowEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field("dynamic-flow")
    abi_version: str = Field(FLOW_REGISTRATION_ABI_V1ALPHA1)
    name: str = Field(..., min_length=1)
    nodes: list[str] = Field(..., min_length=1)
    edges: dict[str, list[str]] = Field(default_factory=dict)
    start: str = Field(..., min_length=1)
    end: list[str] = Field(..., min_length=1)
    owner_class: str = Field(..., min_length=1)
    provenance: ExtensionProvenanceDeclaration | None = None
    overwrite: bool = False

    @model_validator(mode="after")
    def _validate_entry(self) -> "ManifestDeclarativeFlowEntry":
        if self.kind != "dynamic-flow":
            raise ValueError("manifest declarative flow entry must declare kind='dynamic-flow'")
        self.abi_version = validate_extension_abi_version(
            SURFACE_FLOW,
            self.abi_version,
        )
        return self


class ManifestProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: list[str | ManifestPluginEntry] = Field(default_factory=list)
    extensions: list[
        ManifestDeclarativeNodeEntry | ManifestDeclarativeWebhookEntry | ManifestDeclarativeFlowEntry
    ] = Field(default_factory=list)


class VersionedManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(...)
    abi_version: str = Field(...)
    default_profile: str | None = None
    profiles: dict[str, ManifestProfile] = Field(..., min_length=1)


class LegacyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_profile: str | None = None
    profiles: dict[str, ManifestProfile] | None = None
    plugins: list[str | ManifestPluginEntry] | None = None


def extension_abi_policy() -> dict[str, Any]:
    return {
        "schema_version": EXTENSION_ABI_POLICY_VERSION,
        "surfaces": {
            surface: {
                "stability": metadata["stability"],
                "supported_versions": list(metadata["supported_versions"]),
                "default_version": metadata["default_version"],
                "legacy_accepted": bool(metadata["legacy_accepted"]),
                "notes": metadata["notes"],
            }
            for surface, metadata in _SURFACE_POLICY.items()
        },
    }


def extension_surface_stability(surface: str) -> str:
    return str(_SURFACE_POLICY[surface]["stability"])


def extension_surface_default_version(surface: str) -> str:
    return str(_SURFACE_POLICY[surface]["default_version"])


def validate_extension_abi_version(
    surface: str,
    abi_version: str | None,
    *,
    allow_legacy: bool = False,
) -> str:
    if surface not in _SURFACE_POLICY:
        raise ValueError(f"unknown extension ABI surface {surface!r}")
    policy = _SURFACE_POLICY[surface]
    if abi_version is None or not str(abi_version).strip():
        if allow_legacy and policy["legacy_accepted"]:
            return LEGACY_UNVERSIONED_MANIFEST
        raise ValueError(
            f"{surface} requires an explicit abi_version in {policy['supported_versions']}"
        )
    cleaned = str(abi_version).strip()
    if cleaned not in policy["supported_versions"]:
        raise ValueError(
            f"Unsupported abi_version {cleaned!r} for {surface}. "
            f"Supported versions: {policy['supported_versions']}"
        )
    return cleaned


def manifest_effective_abi_version(data: dict[str, Any]) -> str:
    return validate_extension_abi_version(
        SURFACE_MANIFEST,
        data.get("abi_version"),
        allow_legacy=True,
    )


def validate_extension_manifest_document(
    data: dict[str, Any],
    *,
    path: str | Path | None = None,
) -> str:
    if not isinstance(data, dict):
        raise ValueError(f"Plugin manifest at {path or '<memory>'} must be a JSON object")
    effective_version = manifest_effective_abi_version(data)
    if effective_version == LEGACY_UNVERSIONED_MANIFEST:
        manifest = LegacyManifest.model_validate(data)
        if not isinstance(manifest.plugins, list) and not isinstance(manifest.profiles, dict):
            raise ValueError(
                f"Plugin manifest at {path or '<memory>'} must declare either top-level "
                "'plugins' or 'profiles'"
            )
        return effective_version

    manifest = VersionedManifestV1.model_validate(data)
    if manifest.kind != MANIFEST_KIND:
        raise ValueError(
            f"Plugin manifest at {path or '<memory>'} must declare kind={MANIFEST_KIND!r}"
        )
    validate_extension_abi_version(SURFACE_MANIFEST, manifest.abi_version)
    return manifest.abi_version
