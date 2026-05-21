from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from AINDY.platform_layer.extension_abi import (
    FLOW_REGISTRATION_ABI_V1ALPHA1,
    NODE_REGISTRATION_ABI_V1ALPHA1,
    SURFACE_DYNAMIC_NODE,
    SURFACE_FLOW,
    SURFACE_WEBHOOK,
    WEBHOOK_REGISTRATION_ABI_V1ALPHA1,
    validate_extension_abi_version,
)
from AINDY.platform_layer.extension_provenance import (
    ExtensionProvenanceDeclaration,
)
from AINDY.platform_layer.extension_policy import OWNER_EXTERNAL_THIRD_PARTY
from AINDY.platform_layer.nodus_script_store import (
    _NODUS_SCRIPT_REGISTRY,
    _SCRIPTS_DIR,
    _script_lock,
)


class FlowDefinition(BaseModel):
    abi_version: str = Field(FLOW_REGISTRATION_ABI_V1ALPHA1)
    name: str = Field(...)
    nodes: List[str] = Field(..., min_length=1)
    edges: Dict[str, List[str]] = Field(default_factory=dict)
    start: str = Field(...)
    end: List[str] = Field(..., min_length=1)
    owner_class: str = Field(OWNER_EXTERNAL_THIRD_PARTY)
    provenance: ExtensionProvenanceDeclaration | None = None
    overwrite: bool = Field(False)

    @model_validator(mode="after")
    def _validate_abi(self) -> "FlowDefinition":
        self.abi_version = validate_extension_abi_version(SURFACE_FLOW, self.abi_version)
        return self


class FlowRunRequest(BaseModel):
    state: Dict[str, Any] = Field(default_factory=dict)


class NodeRegistration(BaseModel):
    abi_version: str = Field(NODE_REGISTRATION_ABI_V1ALPHA1)
    name: str = Field(...)
    type: str = Field(...)
    handler: str = Field(...)
    timeout_seconds: int = Field(10, ge=1, le=30)
    secret: Optional[str] = Field(None)
    capabilities: List[str] = Field(default_factory=list)
    owner_class: str = Field(OWNER_EXTERNAL_THIRD_PARTY)
    provenance: ExtensionProvenanceDeclaration | None = None
    overwrite: bool = Field(False)

    @model_validator(mode="after")
    def _validate_abi(self) -> "NodeRegistration":
        self.abi_version = validate_extension_abi_version(
            SURFACE_DYNAMIC_NODE,
            self.abi_version,
        )
        return self


class WebhookSubscription(BaseModel):
    abi_version: str = Field(WEBHOOK_REGISTRATION_ABI_V1ALPHA1)
    event_type: str = Field(...)
    callback_url: str = Field(...)
    secret: Optional[str] = Field(None)
    owner_class: str = Field(OWNER_EXTERNAL_THIRD_PARTY)
    provenance: ExtensionProvenanceDeclaration | None = None

    @model_validator(mode="after")
    def _validate_abi(self) -> "WebhookSubscription":
        self.abi_version = validate_extension_abi_version(SURFACE_WEBHOOK, self.abi_version)
        return self


class NodusRunRequest(BaseModel):
    script: Optional[str] = Field(None)
    script_name: Optional[str] = Field(None)
    input: Dict[str, Any] = Field(default_factory=dict)
    error_policy: str = Field("fail")

    @model_validator(mode="after")
    def _require_source(self) -> "NodusRunRequest":
        if not self.script and not self.script_name:
            raise ValueError(
                "Provide either 'script' (inline source) or 'script_name' (uploaded script name)"
            )
        if self.script and self.script_name:
            raise ValueError("Provide 'script' or 'script_name', not both")
        if self.error_policy not in ("fail", "retry"):
            raise ValueError("error_policy must be 'fail' or 'retry'")
        return self


class NodusScriptUpload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.]+$")
    content: str = Field(..., min_length=1)
    description: Optional[str] = Field(None, max_length=512)
    overwrite: bool = Field(False)


class NodusFlowRequest(BaseModel):
    flow_name: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-]+$")
    script: str = Field(..., min_length=1)
    input: Dict[str, Any] = Field(default_factory=dict)
    register: bool = Field(False)
    run: bool = Field(True)


class NodusScheduleRequest(BaseModel):
    script: Optional[str] = Field(None)
    script_name: Optional[str] = Field(None)
    cron: str = Field(...)
    input: Dict[str, Any] = Field(default_factory=dict)
    job_name: Optional[str] = Field(None, max_length=256)
    error_policy: str = Field("fail")
    max_retries: int = Field(3, ge=1, le=10)

    @model_validator(mode="after")
    def _require_source(self) -> "NodusScheduleRequest":
        if not self.script and not self.script_name:
            raise ValueError(
                "Provide either 'script' (inline source) or 'script_name' (uploaded script name)"
            )
        if self.script and self.script_name:
            raise ValueError("Provide 'script' or 'script_name', not both")
        if self.error_policy not in ("fail", "retry"):
            raise ValueError("error_policy must be 'fail' or 'retry'")
        return self


class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    scopes: List[str] = Field(..., min_length=1)
    expires_at: Optional[str] = Field(None)


class SyscallDispatchRequest(BaseModel):
    name: str = Field(..., examples=["sys.v1.memory.read"])
    payload: Dict[str, Any] = Field(default_factory=dict)
