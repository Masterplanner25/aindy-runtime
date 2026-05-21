from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from AINDY._version import __version__ as RUNTIME_PACKAGE_VERSION
from AINDY.platform_layer.extension_policy import (
    OWNER_EXTERNAL_THIRD_PARTY,
    OWNER_FIRST_PARTY_APP,
    OWNER_RUNTIME_BUILTIN,
    validate_extension_owner_class,
)


EXTENSION_PROVENANCE_POLICY_VERSION = "2026-05-20"
INTEGRITY_ALGORITHM_SHA256 = "sha256"
SOURCE_RUNTIME_PACKAGE = "runtime-package"
SOURCE_FIRST_PARTY_SOURCE = "first-party-source-tree"
SOURCE_EXTERNAL_SOURCE = "external-source-tree"
SOURCE_WEBHOOK_INTEGRATION = "webhook-integration"
SOURCE_DATA_REGISTRATION = "data-registration"
SOURCE_OPERATOR_MANUAL = "operator-manual"


class ExtensionIntegrityDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str = Field(INTEGRITY_ALGORITHM_SHA256)
    value: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ExtensionProvenanceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_id: str = Field(..., min_length=1, max_length=256)
    version: str = Field(..., min_length=1, max_length=128)
    source_type: str = Field(..., min_length=1, max_length=64)
    source_ref: str = Field(..., min_length=1, max_length=2048)
    integrity: ExtensionIntegrityDeclaration | None = None
    publisher: str | None = Field(default=None, max_length=256)


def extension_provenance_policy() -> dict[str, Any]:
    return {
        "policy_version": EXTENSION_PROVENANCE_POLICY_VERSION,
        "signing": {
            "status": "unsupported",
            "notes": (
                "The runtime does not implement artifact signing or a remote trust "
                "registry. Provenance is verified only against runtime-local source "
                "bytes or canonical registration payloads."
            ),
        },
        "trust_policies": {
            OWNER_RUNTIME_BUILTIN: "runtime-owned-derived",
            OWNER_FIRST_PARTY_APP: "first-party-derived",
            OWNER_EXTERNAL_THIRD_PARTY: "operator-declared-and-runtime-verified",
        },
        "required_when": {
            "external-third-party": [
                "dynamic-plugin-node",
                "webhook-node",
                "webhook-subscription",
                "dynamic-flow",
            ],
        },
        "notes": (
            "Runtime-built-in and first-party extensions may use runtime-derived "
            "provenance. External third-party registration surfaces must declare "
            "identity, version, source, and integrity that the runtime can verify."
        ),
    }


def provenance_required(*, owner_class: str, surface: str) -> bool:
    resolved = validate_extension_owner_class(owner_class)
    return resolved == OWNER_EXTERNAL_THIRD_PARTY and surface in {
        "dynamic-plugin-node",
        "webhook-node",
        "webhook-subscription",
        "dynamic-flow",
    }


def sha256_file(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def sha256_json_document(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def derive_python_extension_provenance(
    *,
    owner_class: str,
    surface: str,
    extension_name: str,
    module_name: str,
    source_path: str | Path | None,
    declared: dict[str, Any] | None = None,
    allow_legacy_missing: bool = False,
) -> dict[str, Any]:
    resolved = validate_extension_owner_class(owner_class)
    source_text = str(source_path or "").strip()
    observed_hash = sha256_file(source_text) if source_text else None
    source_type = SOURCE_RUNTIME_PACKAGE if resolved == OWNER_RUNTIME_BUILTIN else (
        SOURCE_FIRST_PARTY_SOURCE if resolved == OWNER_FIRST_PARTY_APP else SOURCE_EXTERNAL_SOURCE
    )
    default_version = (
        RUNTIME_PACKAGE_VERSION
        if resolved == OWNER_RUNTIME_BUILTIN
        else "unversioned-source"
    )
    payload = {
        "extension_id": module_name or extension_name,
        "version": default_version,
        "source_type": source_type,
        "source_ref": source_text or module_name,
        "publisher": None,
    }
    verification = "runtime-derived"
    declared_model = _validated_declared_provenance(
        declared,
        owner_class=resolved,
        surface=surface,
    )
    if declared_model is not None:
        payload.update(
            {
                "extension_id": declared_model.extension_id,
                "version": declared_model.version,
                "source_type": declared_model.source_type,
                "source_ref": declared_model.source_ref,
                "publisher": declared_model.publisher,
            }
        )
        if observed_hash is not None:
            _verify_integrity_match(
                declared_model.integrity,
                observed_hash=observed_hash,
                surface=surface,
                extension_id=payload["extension_id"],
            )
            verification = "declared-and-verified"
        else:
            verification = "declared-no-local-bytes"
    elif provenance_required(owner_class=resolved, surface=surface):
        if allow_legacy_missing:
            verification = "legacy-restore-unverified"
            return _finalize_provenance(
                owner_class=resolved,
                surface=surface,
                payload=payload,
                observed_hash=observed_hash,
                verification=verification,
            )
        raise ValueError(
            f"{surface} {extension_name!r} requires declared provenance for external-third-party ownership"
        )
    return _finalize_provenance(
        owner_class=resolved,
        surface=surface,
        payload=payload,
        observed_hash=observed_hash,
        verification=verification,
    )


def derive_structured_extension_provenance(
    *,
    owner_class: str,
    surface: str,
    extension_name: str,
    artifact_payload: dict[str, Any],
    source_type: str,
    source_ref: str,
    declared: dict[str, Any] | None = None,
    allow_legacy_missing: bool = False,
) -> dict[str, Any]:
    resolved = validate_extension_owner_class(owner_class)
    observed_hash = sha256_json_document(artifact_payload)
    payload = {
        "extension_id": extension_name,
        "version": "unversioned-registration",
        "source_type": source_type,
        "source_ref": source_ref,
        "publisher": None,
    }
    verification = "runtime-derived"
    declared_model = _validated_declared_provenance(
        declared,
        owner_class=resolved,
        surface=surface,
    )
    if declared_model is not None:
        payload.update(
            {
                "extension_id": declared_model.extension_id,
                "version": declared_model.version,
                "source_type": declared_model.source_type,
                "source_ref": declared_model.source_ref,
                "publisher": declared_model.publisher,
            }
        )
        _verify_integrity_match(
            declared_model.integrity,
            observed_hash=observed_hash,
            surface=surface,
            extension_id=payload["extension_id"],
        )
        verification = "declared-and-verified"
    elif provenance_required(owner_class=resolved, surface=surface):
        if allow_legacy_missing:
            verification = "legacy-restore-unverified"
            return _finalize_provenance(
                owner_class=resolved,
                surface=surface,
                payload=payload,
                observed_hash=observed_hash,
                verification=verification,
            )
        raise ValueError(
            f"{surface} {extension_name!r} requires declared provenance for external-third-party ownership"
        )
    return _finalize_provenance(
        owner_class=resolved,
        surface=surface,
        payload=payload,
        observed_hash=observed_hash,
        verification=verification,
    )


def summarize_extension_provenance(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "present": bool(entries),
        "total_count": len(entries),
        "verified_count": 0,
        "derived_count": 0,
        "missing_count": 0,
        "by_owner_class": {
            OWNER_RUNTIME_BUILTIN: 0,
            OWNER_FIRST_PARTY_APP: 0,
            OWNER_EXTERNAL_THIRD_PARTY: 0,
        },
        "by_surface": {},
        "entries": [],
        "operator_note": (
            "Extension provenance is a runtime audit surface. Integrity is verified "
            "only against local source bytes or canonical registration payloads; "
            "the runtime does not implement artifact signing or a remote trust registry."
        ),
    }
    for entry in entries:
        owner_class = str(entry.get("owner_class") or "").strip()
        surface = str(entry.get("surface") or "").strip()
        verification = str(entry.get("verification") or "").strip()
        if owner_class in summary["by_owner_class"]:
            summary["by_owner_class"][owner_class] += 1
        if surface:
            summary["by_surface"][surface] = summary["by_surface"].get(surface, 0) + 1
        if verification == "declared-and-verified":
            summary["verified_count"] += 1
        elif verification:
            summary["derived_count"] += 1
        else:
            summary["missing_count"] += 1
        summary["entries"].append(dict(entry))
    return summary


def _validated_declared_provenance(
    declared: dict[str, Any] | None,
    *,
    owner_class: str,
    surface: str,
) -> ExtensionProvenanceDeclaration | None:
    if declared is None:
        return None
    try:
        return ExtensionProvenanceDeclaration.model_validate(declared)
    except Exception as exc:
        raise ValueError(
            f"{surface} provenance for owner_class={owner_class!r} is invalid: {exc}"
        ) from exc


def _verify_integrity_match(
    declared: ExtensionIntegrityDeclaration | None,
    *,
    observed_hash: str,
    surface: str,
    extension_id: str,
) -> None:
    if declared is None:
        raise ValueError(
            f"{surface} {extension_id!r} must declare provenance.integrity for runtime verification"
        )
    if declared.algorithm != INTEGRITY_ALGORITHM_SHA256:
        raise ValueError(
            f"{surface} {extension_id!r} uses unsupported integrity algorithm {declared.algorithm!r}"
        )
    if declared.value != observed_hash:
        raise ValueError(
            f"{surface} {extension_id!r} failed integrity verification: declared sha256 does not match observed artifact bytes"
        )


def _finalize_provenance(
    *,
    owner_class: str,
    surface: str,
    payload: dict[str, Any],
    observed_hash: str | None,
    verification: str,
) -> dict[str, Any]:
    return {
        "schema_version": EXTENSION_PROVENANCE_POLICY_VERSION,
        "owner_class": owner_class,
        "surface": surface,
        "extension_id": payload["extension_id"],
        "version": payload["version"],
        "source_type": payload["source_type"],
        "source_ref": payload["source_ref"],
        "publisher": payload.get("publisher"),
        "verification": verification,
        "trust_policy": extension_provenance_policy()["trust_policies"][owner_class],
        "integrity": {
            "algorithm": INTEGRITY_ALGORITHM_SHA256 if observed_hash else None,
            "observed": observed_hash,
            "status": "verified" if verification == "declared-and-verified" else (
                "derived-local" if observed_hash else "not-applicable"
            ),
        },
        "signing": {
            "status": "unsupported",
        },
    }
