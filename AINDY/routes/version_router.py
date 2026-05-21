from pydantic import BaseModel
from fastapi import APIRouter, Request

from AINDY.config import settings
from AINDY.core.execution_helper import execute_with_pipeline
from AINDY.platform_layer.public_contract import runtime_public_contract_metadata
from AINDY.platform_layer.deployment_contract import runtime_ui_surface_state
from AINDY.platform_layer.runtime_compatibility import runtime_repo_compatibility_metadata

router = APIRouter(prefix="/api", tags=["version"])


class RuntimeSurfaceResponse(BaseModel):
    process_role: str
    boot_mode: str
    boot_profile: str
    boot_profile_source: str
    deployment_profile: str
    deployment_profile_source: str
    background_leadership_mode: str
    app_plugins_loaded: bool
    app_plugin_count: int
    external_python_override_active: bool
    external_python_override_execution_model: str
    trusted_python_execution: dict
    extension_provenance: dict
    ui_mode: str
    default_route: str
    platform_home: str


class RuntimePackageResponse(BaseModel):
    name: str
    version: str


class AppsRepoContractResponse(BaseModel):
    declaration_format: str
    recommended_runtime_requirement: str
    compatible_runtime_major: str
    compatible_api_major: str
    policy: str


class RepoCompatibilityResponse(BaseModel):
    runtime_package: RuntimePackageResponse
    apps_repo_contract: AppsRepoContractResponse


class VersionResponse(BaseModel):
    api_version: str
    min_client_version: str
    breaking_change_policy: str
    changelog_url: str | None
    compatibility: RepoCompatibilityResponse
    public_contract: dict
    runtime: RuntimeSurfaceResponse


@router.get("/version", response_model=VersionResponse)
async def get_api_version(request: Request):
    def handler(ctx):
        return VersionResponse(
            api_version=settings.API_VERSION,
            min_client_version=settings.API_MIN_CLIENT_VERSION,
            breaking_change_policy=(
                "MAJOR version increments indicate breaking changes. "
                "Clients must re-deploy when the MAJOR version changes. "
                "MINOR and PATCH increments are safe for existing clients."
            ),
            changelog_url=None,
            compatibility=RepoCompatibilityResponse(**runtime_repo_compatibility_metadata()),
            public_contract=runtime_public_contract_metadata(),
            runtime=RuntimeSurfaceResponse(**runtime_ui_surface_state()),
        )

    result = await execute_with_pipeline(
        request=request,
        route_name="api.version.get",
        handler=handler,
        metadata={"source": "version_router", "disable_memory_capture": True},
        return_result=True,
    )
    if not result.success:
        detail = result.metadata.get("detail") or result.error or "Execution failed"
        raise RuntimeError(detail)
    return result.data
