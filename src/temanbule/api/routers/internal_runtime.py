"""Internal runtime router (FND-09): service auth + execution grants."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from temanbule.api.deps import SessionDep, SettingsDep, require_service_identity
from temanbule.modules.ai_runtime.grants import ExecutionGrantService
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
