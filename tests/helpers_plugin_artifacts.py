from __future__ import annotations

from pathlib import Path
from typing import Any

from AINDY.platform_layer.plugin_artifacts import (
    PLUGIN_ARTIFACT_KIND,
    PLUGIN_ARTIFACT_SCHEMA_VERSION,
    compute_plugin_artifact_integrity,
)


def build_plugin_artifact(
    tmp_path: Path,
    *,
    module_name: str,
    source: str,
    extension_id: str,
    version: str = "1.0.0",
    publisher: str | None = None,
) -> dict[str, Any]:
    artifact_root = tmp_path / f"{module_name}_artifact"
    code_dir = artifact_root / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    plugin_file = code_dir / f"{module_name}.py"
    plugin_file.write_text(source.strip(), encoding="utf-8")
    handler = f"{module_name}:handler"
    manifest = {
        "kind": PLUGIN_ARTIFACT_KIND,
        "schema_version": PLUGIN_ARTIFACT_SCHEMA_VERSION,
        "extension_id": extension_id,
        "version": version,
        "owner_class": "external-third-party",
        "publisher": publisher,
        "runtime": {
            "entrypoint": handler,
            "code_dir": "code",
            "sandbox_launch": {"entry_module": module_name},
        },
    }
    integrity = compute_plugin_artifact_integrity(
        manifest_payload=manifest,
        artifact_root=artifact_root,
    )
    manifest_with_integrity = dict(manifest)
    manifest_with_integrity["integrity"] = {
        "algorithm": "sha256",
        "value": integrity,
    }
    (artifact_root / "plugin-artifact.json").write_text(
        __import__("json").dumps(manifest_with_integrity, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    provenance = {
        "extension_id": extension_id,
        "version": version,
        "source_type": "external-plugin-artifact",
        "source_ref": str(artifact_root),
        "integrity": {
            "algorithm": "sha256",
            "value": integrity,
        },
        "publisher": publisher,
    }
    return {
        "artifact_root": artifact_root,
        "code_dir": code_dir,
        "plugin_file": plugin_file,
        "handler": handler,
        "integrity": integrity,
        "provenance": provenance,
    }
