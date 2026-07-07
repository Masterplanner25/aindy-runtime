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
SOURCE_EXTERNAL_PLUGIN_ARTIFACT = "external-plugin-artifact"
SOURCE_WEBHOOK_INTEGRATION = "webhook-integration"
SOURCE_DATA_REGISTRATION = "data-registration"
SOURCE_OPERATOR_MANUAL = "operator-manual"


class ExtensionIntegrityDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str = Field(INTEGRITY_ALGORITHM_SHA256)
    value: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ExtensionSignatureDeclaration(BaseModel):
    """AGENT-HARDEN-10 — a detached signature over the artifact's sha256 digest."""

    model_config = ConfigDict(extra="forbid")

    algorithm: str = Field("ed25519")
    value: str = Field(..., min_length=1, max_length=512)   # base64 signature
    key_id: str = Field(..., min_length=1, max_length=256)  # sha256:<hex> fingerprint


class ExtensionProvenanceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_id: str = Field(..., min_length=1, max_length=256)
    version: str = Field(..., min_length=1, max_length=128)
    source_type: str = Field(..., min_length=1, max_length=64)
    source_ref: str = Field(..., min_length=1, max_length=2048)
    integrity: ExtensionIntegrityDeclaration | None = None
    signature: ExtensionSignatureDeclaration | None = None
    publisher: str | None = Field(default=None, max_length=256)


def extension_provenance_policy() -> dict[str, Any]:
    return {
        "policy_version": EXTENSION_PROVENANCE_POLICY_VERSION,
        "signing": {
            "status": "supported",
            "algorithm": "ed25519",
            "notes": (
                "Plugin bundles may carry an Ed25519 detached signature over the "
                "artifact sha256 digest, verified against a runtime trust registry "
                "(AINDY.platform_layer.extension_signing). Enforcement (refusing an "
                "unsigned/untrusted bundle on a production profile) is opt-in via "
                "AINDY_REQUIRE_SIGNED_PLUGINS; otherwise the signature is verified and "
                "reported without blocking load."
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


def derive_plugin_artifact_provenance(
    *,
    owner_class: str,
    surface: str,
    extension_name: str,
    extension_id: str,
    version: str,
    artifact_path: str | Path,
    observed_hash: str,
    publisher: str | None = None,
    declared: dict[str, Any] | None = None,
    allow_legacy_missing: bool = False,
    deployment_profile: str | None = None,
) -> dict[str, Any]:
    resolved = validate_extension_owner_class(owner_class)
    artifact_text = str(artifact_path or "").strip()
    payload = {
        "extension_id": extension_id or extension_name,
        "version": version,
        "source_type": SOURCE_EXTERNAL_PLUGIN_ARTIFACT,
        "source_ref": artifact_text or extension_name,
        "publisher": publisher,
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
    # AGENT-HARDEN-10 — verify a declared bundle signature (and enforce on
    # signature-required surfaces in a production profile when opted in).
    signing = _describe_and_enforce_signature(
        declared_model.signature if declared_model is not None else None,
        observed_hash=observed_hash,
        surface=surface,
        extension_id=payload["extension_id"],
        deployment_profile=deployment_profile,
        require_signature=provenance_required(owner_class=resolved, surface=surface),
    )
    return _finalize_provenance(
        owner_class=resolved,
        surface=surface,
        payload=payload,
        observed_hash=observed_hash,
        verification=verification,
        signing=signing,
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
            "against local source bytes or canonical registration payloads; plugin "
            "bundles may additionally carry an Ed25519 detached signature verified "
            "against the runtime trust registry (see extension_signing)."
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


def _describe_and_enforce_signature(
    declared: "ExtensionSignatureDeclaration | None",
    *,
    observed_hash: str | None,
    surface: str,
    extension_id: str,
    deployment_profile: str | None,
    require_signature: bool,
) -> dict[str, Any]:
    """Verify a declared bundle signature and return its ``signing`` block.

    AGENT-HARDEN-10: a declared signature is verified against the trust registry
    (Ed25519 over the artifact's sha256 digest). Enforcement (a hard refusal) only
    applies to signature-required surfaces (external third-party) when
    ``AINDY_REQUIRE_SIGNED_PLUGINS`` is set and the deployment profile is a production
    one — then an unsigned/untrusted/invalid bundle is **refused** (raises, mirroring
    integrity enforcement). Otherwise the result is recorded but never blocks load.
    """
    from AINDY.platform_layer.extension_signing import (
        require_signed_plugins,
        signature_required,
        verify_bundle_signature,
    )

    profile = str(deployment_profile or "").strip()
    enforced = require_signature and require_signed_plugins() and signature_required(profile)

    if declared is None or not observed_hash:
        if enforced:
            raise ValueError(
                f"{surface} {extension_id!r} must be signed: production profile "
                f"{profile!r} requires a trusted signature (AINDY_REQUIRE_SIGNED_PLUGINS)"
            )
        return {"status": "unsigned", "verified": False, "algorithm": None, "key_id": None}

    result = verify_bundle_signature(
        digest_hex=observed_hash, signature=declared.value, key_id=declared.key_id
    )
    if not result["ok"]:
        if enforced:
            raise ValueError(
                f"{surface} {extension_id!r} failed signature verification: {result['error']}"
            )
        return {
            "status": "unverified",
            "verified": False,
            "algorithm": declared.algorithm,
            "key_id": declared.key_id,
            "error": result["error"],
        }
    return {
        "status": "verified",
        "verified": True,
        "algorithm": declared.algorithm,
        "key_id": declared.key_id,
    }


def _finalize_provenance(
    *,
    owner_class: str,
    surface: str,
    payload: dict[str, Any],
    observed_hash: str | None,
    verification: str,
    signing: dict[str, Any] | None = None,
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
        "signing": signing or {"status": "unsigned", "verified": False},
    }
