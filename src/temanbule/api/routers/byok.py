"""BYOK credentials router (Phase 2): register, select, revoke, list providers/models."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.modules.catalog.byok import ByokCredentialService
from temanbule.modules.catalog.models import AiModelConfiguration, ProviderCatalog
from temanbule.platform.crypto import FieldCipher
from temanbule.platform.errors import FeatureUnavailableError

router = APIRouter(prefix="/v1/me", tags=["byok"])


class RegisterCredentialRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=26)
    api_key: SecretStr = Field(min_length=8)
    base_url: str | None = Field(default=None, max_length=500)


class ProviderResponse(BaseModel):
    provider_id: str
    code: str
    status: str
    canonical_base_url: str | None


class ModelResponse(BaseModel):
    model_id: str
    provider_id: str
    identifier: str
    revision: int
    capabilities: str
    status: str


class CredentialResponse(BaseModel):
    credential_id: str
    provider_id: str
    status: str
    fingerprint: str
    verified_at: str | None


class SelectModelRequest(BaseModel):
    capability: str = Field(pattern="^(llm|stt)$")
    credential_id: str = Field(min_length=1, max_length=26)
    model_id: str = Field(min_length=1, max_length=26)


class SelectModelResponse(BaseModel):
    capability: str
    credential_id: str
    model_id: str


def _build_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> ByokCredentialService:
    verifier = getattr(request.app.state, "credential_verifier", None)
    if verifier is None or not hasattr(verifier, "verify"):
        raise FeatureUnavailableError(
            "Verifikasi credential provider belum dikonfigurasi (menunggu DEC-08).",
        )
    cipher = FieldCipher(settings.crypto_key_encryption_key)
    return ByokCredentialService(session, cipher, verifier)


@router.get("/ai-providers", response_model=list[ProviderResponse])
async def list_providers(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ProviderResponse]:
    rows = (
        (
            await session.execute(
                select(ProviderCatalog)
                .where(ProviderCatalog.status == "active")
                .order_by(ProviderCatalog.code.asc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        ProviderResponse(
            provider_id=p.id,
            code=p.code,
            status=p.status,
            canonical_base_url=p.canonical_base_url,
        )
        for p in rows
    ]


@router.get("/ai-models", response_model=list[ModelResponse])
async def list_models(
    session: SessionDep,
    provider_id: Annotated[str | None, Query()] = None,
    capability: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ModelResponse]:
    stmt = select(AiModelConfiguration).where(AiModelConfiguration.status == "active")
    if provider_id:
        stmt = stmt.where(AiModelConfiguration.provider_id == provider_id)
    if capability:
        stmt = stmt.where(AiModelConfiguration.capabilities.contains(capability))
    rows = (
        (
            await session.execute(
                stmt.order_by(AiModelConfiguration.identifier.asc()).limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        ModelResponse(
            model_id=m.id,
            provider_id=m.provider_id,
            identifier=m.identifier,
            revision=m.revision,
            capabilities=m.capabilities,
            status=m.status,
        )
        for m in rows
    ]


@router.post("/ai-credentials", response_model=CredentialResponse, status_code=201)
async def register_credential(
    body: RegisterCredentialRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> CredentialResponse:
    service = _build_service(request, session, settings)
    credential = await service.register_credential(
        user_id=current_user.id,
        provider_id=body.provider_id,
        api_key=body.api_key.get_secret_value(),
        base_url=body.base_url,
    )
    await session.commit()
    return CredentialResponse(
        credential_id=credential.id,
        provider_id=credential.provider_id,
        status=credential.status,
        fingerprint=credential.fingerprint,
        verified_at=credential.verified_at.isoformat() if credential.verified_at else None,
    )


@router.put("/ai-selections", response_model=SelectModelResponse)
async def select_model(
    body: SelectModelRequest,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> SelectModelResponse:
    service = _build_service(request, session, settings)
    selection = await service.select_model(
        user_id=current_user.id,
        capability=body.capability,
        credential_id=body.credential_id,
        model_id=body.model_id,
    )
    await session.commit()
    return SelectModelResponse(
        capability=selection.capability,
        credential_id=selection.credential_id,
        model_id=selection.model_id,
    )


@router.delete("/ai-credentials/{credential_id}", status_code=204)
async def revoke_credential(
    credential_id: str,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> None:
    service = _build_service(request, session, settings)
    await service.revoke_credential(user_id=current_user.id, credential_id=credential_id)
    await session.commit()
