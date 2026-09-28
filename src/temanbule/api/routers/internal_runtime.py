"""Internal runtime router (FND-09): service auth + execution grants."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from temanbule.api.deps import SessionDep, SettingsDep, require_service_identity
from temanbule.modules.ai_runtime.analysis_tools import register_analysis_tools
from temanbule.modules.ai_runtime.grants import ExecutionGrantService
from temanbule.modules.ai_runtime.ingestion_tools import register_ingestion_tools
from temanbule.modules.ai_runtime.learning_tools import register_learning_tools
from temanbule.modules.ai_runtime.tools import ToolExecutionService
from temanbule.modules.ai_runtime.vocabulary_tools import register_vocabulary_tools
from temanbule.modules.catalog.models import ToolRegistry
from temanbule.platform.errors import AppError, FeatureUnavailableError
from temanbule.platform.security import new_ulid

router = APIRouter(prefix="/internal/v1/runtime", tags=["internal-runtime"])


class IssueGrantRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=list)
    ttl_seconds: int = Field(default=60, ge=1, le=600)
    owner_user_id: str | None = None
    resource_ref: str | None = None


class GrantResponse(BaseModel):
    execution_ref: str
    expires_at: str


class ValidateGrantRequest(BaseModel):
    execution_ref: str
    required_scope: str | None = None
    consume: bool = True


class GrantMetadataResponse(BaseModel):
    execution_ref: str
    purpose: str
    scopes: list[str]
    owner_user_id: str | None
    service_identity: str


class RuntimeRequest(BaseModel):
    schema_version: str = Field(pattern="^1$")
    request_id: str = Field(pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
    execution_ref: str = Field(pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


class ToolRequest(RuntimeRequest):
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None, pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"
    )


class ToolErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class ToolResponse(BaseModel):
    schema_version: str = "1"
    execution_id: str
    status: str
    result: dict[str, object] | None = None
    error: ToolErrorResponse | None = None


@router.post("/grants:issue", response_model=GrantResponse)
async def issue_grant(
    body: IssueGrantRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: str = Depends(require_service_identity),
) -> GrantResponse:
    """Issue execution grant; hanya dipanggil oleh trusted services."""
    grants = ExecutionGrantService(session)
    async with session.begin():
        grant = grants.issue_grant(
            request_id=new_ulid(),
            service_identity=service_identity,
            purpose=body.purpose,
            scopes=body.scopes,
            ttl_seconds=body.ttl_seconds,
            owner_user_id=body.owner_user_id,
            resource_ref=body.resource_ref,
        )
    return GrantResponse(execution_ref=grant.id, expires_at=grant.expires_at.isoformat())


@router.post("/grants:validate", response_model=GrantMetadataResponse)
async def validate_grant(
    body: ValidateGrantRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: str = Depends(require_service_identity),
) -> GrantMetadataResponse:
    grants = ExecutionGrantService(session)
    async with session.begin():
        grant = await grants.validate_grant(
            grant_id=body.execution_ref,
            expected_service_identity=service_identity,
            required_scope=body.required_scope,
            consume=body.consume,
        )
    return GrantMetadataResponse(
        execution_ref=grant.id,
        purpose=grant.purpose,
        scopes=json.loads(grant.scopes),
        owner_user_id=grant.owner_user_id,
        service_identity=grant.service_identity,
    )


def _runtime_error_code(error: AppError) -> str:
    """Map application errors to the stable CallCraft/runtime vocabulary."""
    allowed = {
        "INVALID_ARGUMENT", "UNAUTHENTICATED", "FORBIDDEN", "RESOURCE_NOT_FOUND",
        "STATE_CONFLICT", "IDEMPOTENCY_CONFLICT", "RATE_LIMITED",
        "DEPENDENCY_UNAVAILABLE", "INTERNAL",
    }
    if error.code == "VALIDATION_FAILED":
        return "INVALID_ARGUMENT"
    return error.code if error.code in allowed else "INTERNAL"


@router.post("/context:resolve")
async def resolve_context(
    body: RuntimeRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: str = Depends(require_service_identity),
) -> dict[str, object]:
    """Resolve only grant-owned context; credentials are never returned."""
    del settings
    grants = ExecutionGrantService(session)
    async with session.begin():
        grant = await grants.validate_grant(
            grant_id=body.execution_ref,
            expected_service_identity=service_identity,
            consume=False,
        )
    if grant.snapshot_id is None:
        raise FeatureUnavailableError(
            "Runtime snapshot belum tersedia untuk execution grant.",
            code="RUNTIME_SNAPSHOT_REQUIRED",
        )
    # Snapshot enrichment must come from the backend catalog/provider broker.
    # Do not fabricate plan, capability, payer, or model identifiers here.
    raise FeatureUnavailableError(
        "Runtime snapshot resolver belum terhubung ke broker konfigurasi.",
        code="RUNTIME_CONTEXT_UNAVAILABLE",
    )


@router.post("/tools:execute", response_model=ToolResponse)
async def execute_tool(
    body: ToolRequest,
    session: SessionDep,
    settings: SettingsDep,
    service_identity: str = Depends(require_service_identity),
) -> ToolResponse:
    """Execute a registered domain tool under a backend-owned execution grant."""
    registry = (
        await session.execute(
            select(ToolRegistry).where(
                ToolRegistry.environment == settings.app_env,
                ToolRegistry.name == body.tool_name,
                ToolRegistry.schema_version == "1",
                ToolRegistry.status == "active",
            )
        )
    ).scalar_one_or_none()
    if registry is None or not registry.callcraft_spec_id.strip():
        return ToolResponse(
            execution_id=new_ulid(),
            status="failed",
            result=None,
            error=ToolErrorResponse(
                code="DEPENDENCY_UNAVAILABLE",
                message="Tool registry aktif belum dikonfigurasi.",
                request_id=body.request_id,
            ),
        )
    if registry.idempotency_policy not in {"required", "not_required"}:
        return ToolResponse(
            execution_id=new_ulid(),
            status="failed",
            result=None,
            error=ToolErrorResponse(
                code="DEPENDENCY_UNAVAILABLE",
                message="Tool registry memiliki policy idempotency yang tidak dikenal.",
                request_id=body.request_id,
            ),
        )
    if registry.idempotency_policy == "required" and body.idempotency_key is None:
        return ToolResponse(
            execution_id=new_ulid(),
            status="failed",
            result=None,
            error=ToolErrorResponse(
                code="INVALID_ARGUMENT",
                message="Tool registry mewajibkan Idempotency-Key.",
                request_id=body.request_id,
            ),
        )
    tools = ToolExecutionService(session)
    register_vocabulary_tools(tools)
    register_analysis_tools(tools)
    register_learning_tools(tools)
    register_ingestion_tools(tools)
    try:
        # Query registry di atas sudah membuka transaksi implisit pada session
        # (SQLAlchemy async auto-begin saat query pertama), sehingga tidak bisa
        # memakai session.begin() eksplisit. Commit eksplisit setelah execute.
        result = await tools.execute(
            tool_name=body.tool_name,
            grant_id=body.execution_ref,
            service_identity=service_identity,
            request_id=body.request_id,
            idempotency_key=body.idempotency_key,
            arguments=body.arguments,
        )
        await session.commit()
    except AppError as error:
        await session.rollback()
        return ToolResponse(
            execution_id=new_ulid(),
            status="failed",
            result=None,
            error=ToolErrorResponse(
                code=_runtime_error_code(error),
                message=error.message,
                request_id=body.request_id,
            ),
        )
    return ToolResponse(
        execution_id=result.execution_id,
        status=result.status,
        result=result.result,
        error=(
            ToolErrorResponse(
                code=result.error_code or "INTERNAL",
                message=result.error_message or "Tool execution failed.",
                request_id=body.request_id,
            )
            if result.status == "failed"
            else None
        ),
    )
