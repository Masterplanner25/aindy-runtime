from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    validate_extension_owner_class,
)
from AINDY.platform_layer.extension_provenance import sha256_json_document

PLUGIN_ARTIFACT_KIND = "aindy-plugin-artifact"
PLUGIN_ARTIFACT_SCHEMA_VERSION = "2026-05-21"
PLUGIN_ARTIFACT_MANIFEST_FILENAME = "plugin-artifact.json"


class PluginArtifactIntegrity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str = Field("sha256")
    value: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class PluginArtifactRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint: str = Field(..., min_length=3, max_length=256)
    code_dir: str = Field("code", min_length=1, max_length=128)
    sandbox_launch: dict[str, Any] = Field(default_factory=dict)


class PluginArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(PLUGIN_ARTIFACT_KIND)
    schema_version: str = Field(PLUGIN_ARTIFACT_SCHEMA_VERSION)
    extension_id: str = Field(..., min_length=1, max_length=256)
    version: str = Field(..., min_length=1, max_length=128)
    owner_class: str = Field(OWNER_EXTERNAL_THIRD_PARTY)
    publisher: str | None = Field(default=None, max_length=256)
    runtime: PluginArtifactRuntime
    integrity: PluginArtifactIntegrity


def plugin_artifact_contract() -> dict[str, Any]:
    return {
        "schema_version": PLUGIN_ARTIFACT_SCHEMA_VERSION,
        "manifest_filename": PLUGIN_ARTIFACT_MANIFEST_FILENAME,
        "kind": PLUGIN_ARTIFACT_KIND,
        "required_owner_class": OWNER_EXTERNAL_THIRD_PARTY,
        "notes": (
            "Third-party plugin artifacts are immutable bundle directories containing "
            "a manifest plus a code subtree. The runtime verifies the bundle hash "
            "before sandbox launch."
        ),
    }


def compute_plugin_artifact_integrity(
    *,
    manifest_payload: dict[str, Any],
    artifact_root: str | Path,
) -> str:
    root = Path(artifact_root).resolve()
    file_records: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == PLUGIN_ARTIFACT_MANIFEST_FILENAME:
            continue
        file_records.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return sha256_json_document(
        {
            "manifest": manifest_payload,
            "files": file_records,
        }
    )


def admit_plugin_artifact(
    *,
    artifact_path: str | Path,
    expected_owner_class: str,
    expected_handler: str | None = None,
) -> dict[str, Any]:
    owner_class = validate_extension_owner_class(expected_owner_class)
    if owner_class != OWNER_EXTERNAL_THIRD_PARTY:
        raise ValueError("plugin artifact admission is only required for external-third-party ownership")

    root = Path(artifact_path).resolve()
    if not root.is_dir():
        raise ValueError(f"plugin artifact path {str(root)!r} is not a directory")
    manifest_path = root / PLUGIN_ARTIFACT_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ValueError(
            f"plugin artifact {str(root)!r} is missing {PLUGIN_ARTIFACT_MANIFEST_FILENAME}"
        )
    try:
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"plugin artifact manifest is invalid JSON: {exc}") from exc
    try:
        manifest = PluginArtifactManifest.model_validate(manifest_raw)
    except Exception as exc:
        raise ValueError(f"plugin artifact manifest is invalid: {exc}") from exc

    if manifest.kind != PLUGIN_ARTIFACT_KIND:
        raise ValueError(f"plugin artifact kind must be {PLUGIN_ARTIFACT_KIND!r}")
    if manifest.schema_version != PLUGIN_ARTIFACT_SCHEMA_VERSION:
        raise ValueError(
            f"plugin artifact schema_version must be {PLUGIN_ARTIFACT_SCHEMA_VERSION!r}"
        )
    if validate_extension_owner_class(manifest.owner_class) != OWNER_EXTERNAL_THIRD_PARTY:
        raise ValueError("plugin artifact owner_class must be external-third-party")
    if expected_handler and manifest.runtime.entrypoint != expected_handler:
        raise ValueError(
            f"plugin artifact entrypoint {manifest.runtime.entrypoint!r} does not match requested handler {expected_handler!r}"
        )

    code_path = (root / manifest.runtime.code_dir).resolve()
    if not code_path.is_dir():
        raise ValueError(
            f"plugin artifact code_dir {manifest.runtime.code_dir!r} was not found under {str(root)!r}"
        )
    if root not in code_path.parents and code_path != root:
        raise ValueError("plugin artifact code_dir escapes the artifact root")

    manifest_without_integrity = manifest.model_dump()
    manifest_without_integrity.pop("integrity", None)
    observed_hash = compute_plugin_artifact_integrity(
        manifest_payload=manifest_without_integrity,
        artifact_root=root,
    )
    if manifest.integrity.algorithm != "sha256":
        raise ValueError(
            f"plugin artifact uses unsupported integrity algorithm {manifest.integrity.algorithm!r}"
        )
    if manifest.integrity.value != observed_hash:
        raise ValueError(
            "plugin artifact failed integrity verification: declared sha256 does not match observed bundle bytes"
        )
    return {
        "artifact_root": str(root),
        "manifest_path": str(manifest_path),
        "code_path": str(code_path),
        "entrypoint": manifest.runtime.entrypoint,
        "extension_id": manifest.extension_id,
        "version": manifest.version,
        "publisher": manifest.publisher,
        "owner_class": manifest.owner_class,
        "integrity_hash": observed_hash,
        "sandbox_launch": dict(manifest.runtime.sandbox_launch or {}),
        "manifest": manifest.model_dump(),
    }
